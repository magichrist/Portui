"""Process discovery and metadata via psutil, with graceful degradation."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import psutil

from ..models import ProcessRecord

log = logging.getLogger(__name__)

__all__ = ["ProcessCollector", "format_elapsed", "process_to_record"]

# Sentinel for "read the CPU value from psutil". Distinct from ``None``, which
# means "deliberately report *unavailable* instead of calling psutil".
_READ_CPU: Any = object()


def format_elapsed(seconds: float | None) -> str | None:
    """Render a duration as ``3d 04h 12m`` style text."""
    if seconds is None or seconds < 0:
        return None
    total = int(seconds)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    if days:
        return f"{days}d {hours:02d}h {minutes:02d}m"
    if hours:
        return f"{hours:02d}h {minutes:02d}m"
    if minutes:
        return f"{minutes:02d}m {seconds:02d}s"
    return f"{seconds}s"


def _safe(fn: Any, default: Any = None) -> Any:
    """Call a psutil accessor, swallowing the access-denied races it raises."""
    try:
        return fn()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return default


def process_to_record(
    process: psutil.Process,
    *,
    include_cmdline: bool = True,
    cpu_percent: Any = _READ_CPU,
) -> ProcessRecord:
    """Convert a psutil process into a normalized record.

    Every field degrades to ``None`` rather than a placeholder like ``0`` so the
    UI can distinguish "unavailable" from "genuinely zero".

    ``cpu_percent`` is read from psutil by default. A caller that already knows
    the value is not yet determinable — because this handle is about to be
    primed, and psutil would answer a meaningless ``0.0`` — passes ``None`` to
    force the *unavailable* marker instead.
    """
    name = _safe(process.name, "unknown") or "unknown"
    with process.oneshot():
        ppid = _safe(process.ppid)
        user = _safe(process.username)
        cpu = _safe(process.cpu_percent) if cpu_percent is _READ_CPU else cpu_percent
        mem = _safe(process.memory_percent)
        meminfo = _safe(process.memory_info)
        threads = _safe(process.num_threads)
        status = _safe(process.status)
        created = _safe(process.create_time)
        exe = _safe(process.exe)
        cmdline = _safe(process.cmdline) if include_cmdline else None

    rss_bytes = getattr(meminfo, "rss", None) if meminfo is not None else None

    elapsed = None
    if created is not None:
        elapsed = format_elapsed(max(0.0, time.time() - created))

    return ProcessRecord(
        pid=process.pid,
        name=name,
        ppid=ppid,
        user=user,
        cpu_percent=cpu,
        memory_percent=mem,
        memory_rss=rss_bytes,
        num_threads=threads,
        status=status,
        create_time=created,
        elapsed=elapsed,
        exe=exe,
        cmdline=list(cmdline) if cmdline else [],
    )


@dataclass(slots=True)
class _DetailSample:
    """A cached ``psutil.Process`` handle for the detail path.

    ``cpu_percent(interval=None)`` diffs against the previous sample taken on
    *this* object, so the handle itself is the state. ``create_time`` is kept
    alongside it because psutil caches a ``Process`` identity too: if the PID is
    recycled, the old baseline describes a process that no longer exists.
    """

    process: psutil.Process
    create_time: float | None
    primed: bool = False


class ProcessCollector:
    """Snapshot provider for the process list and the process detail screen.

    ``cpu_percent`` from a single non-primed call returns ``0.0`` on every
    process, which would be a lie. We prime the counter once and read it again
    on the next snapshot so the first refresh shows real CPU or "unavailable".

    The list path gets that priming for free, because ``psutil.process_iter()``
    hands back its own cached ``Process`` instances and so diffs the second
    snapshot against the first. The detail path builds its own handle per PID
    and would therefore have no baseline at all — a fresh handle every call means
    ``cpu_percent`` answers ``0.0`` forever. So ``detail_blocking`` caches one
    handle per PID, primes it on first sight, and reports *unavailable* until a
    second sample exists. Both paths honour the same rule: never a fake ``0.0%``.
    """

    def __init__(self, *, prime_cpu: bool = True) -> None:
        self._primed = False
        self._prime_cpu = prime_cpu
        self._cache: list[ProcessRecord] = []
        self._cache_time: float = 0.0
        self._detail_samples: dict[int, _DetailSample] = {}

    def prime(self) -> None:
        """Prime psutil's CPU counters without blocking the event loop."""
        for process in psutil.process_iter(["pid"]):
            try:
                process.cpu_percent(interval=None)
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
        self._primed = True

    def snapshot_blocking(self) -> list[ProcessRecord]:
        """Collect every visible process. Safe to call off the event loop."""
        if self._prime_cpu and not self._primed:
            self.prime()

        records: list[ProcessRecord] = []
        for process in psutil.process_iter(["pid", "name"]):
            try:
                if process.pid <= 0:
                    continue
                records.append(process_to_record(process))
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
                continue
            except Exception:
                # psutil races (NoSuchProcess/AccessDenied/Zombie) are expected
                # and handled above; anything else is a bug we must not bury.
                log.warning("unexpected error collecting process %s", process.pid, exc_info=True)
        records.sort(key=lambda record: record.pid)
        return records

    async def snapshot(self) -> list[ProcessRecord]:
        """Async wrapper so the Textual event loop is never blocked."""
        return await asyncio.to_thread(self.snapshot_blocking)

    async def process_detail(self, pid: int) -> ProcessRecord | None:
        """Re-read metadata for a single process. ``None`` if it exited."""
        return await asyncio.to_thread(self.detail_blocking, pid)

    def detail_blocking(self, pid: int) -> ProcessRecord | None:
        """Re-read metadata for a single process. ``None`` if it exited.

        The cached handle makes the second call onwards report a real diff-based
        CPU percentage; the first call primes it and reports *unavailable*.
        """
        if not psutil.pid_exists(pid):
            self._detail_samples.pop(pid, None)
            return None

        sample = self._detail_samples.get(pid)
        if sample is not None and not self._sample_is_current(sample):
            # The PID was recycled: the cached handle now points at a different
            # process and its CPU baseline describes something that is gone.
            self._detail_samples.pop(pid, None)
            sample = None

        if sample is None:
            try:
                process = psutil.Process(pid)
                create_time = _safe(process.create_time)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
                self._detail_samples.pop(pid, None)
                return None
            sample = _DetailSample(process=process, create_time=create_time)
            self._detail_samples[pid] = sample
            self._prune_detail_samples()

        if not sample.primed:
            # First sighting of this PID. There is no previous sample on this
            # handle, so reading it would return a meaningless 0.0. Prime it and
            # report "unavailable" instead of a fake zero.
            try:
                sample.process.cpu_percent(interval=None)
            except (psutil.NoSuchProcess, psutil.ZombieProcess, OSError):
                self._detail_samples.pop(pid, None)
                return None
            except psutil.AccessDenied:
                # Cannot prime; leave ``primed`` false so we keep reporting
                # "unavailable" rather than trusting a priming-only reading.
                pass
            else:
                sample.primed = True
            return process_to_record(sample.process, cpu_percent=None)

        try:
            return process_to_record(sample.process)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            self._detail_samples.pop(pid, None)
            return None

    def _sample_is_current(self, sample: _DetailSample) -> bool:
        """True when the cached handle still points at the process we primed.

        psutil treats ``(pid, create_time)`` as the identity of a process, so a
        changed creation time means the PID was recycled and the cached CPU
        baseline must be discarded. ``create_time`` is a *method*, so it has to
        be read through ``_safe`` (which calls it) rather than passed bare.
        When it cannot be read we defer to psutil's own liveness check, which
        also accounts for reuse.
        """
        current = _safe(sample.process.create_time)
        if sample.create_time is None or current is None:
            return sample.process.is_running()
        return current == sample.create_time

    def _prune_detail_samples(self) -> None:
        """Drop handles for PIDs that no longer exist.

        Only called when a new PID is cached, so the dict is swept lazily and
        cannot grow without bound as processes come and go.
        """
        for stale in [pid for pid in self._detail_samples if not psutil.pid_exists(pid)]:
            self._detail_samples.pop(stale, None)

    def cache(self) -> list[ProcessRecord]:
        return self._cache

    def store(self, records: list[ProcessRecord]) -> None:
        self._cache = records
        self._cache_time = time.time()

    def cached_detail(self, pid: int) -> ProcessRecord | None:
        for record in self._cache:
            if record.pid == pid:
                return record
        return None
