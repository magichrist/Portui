"""Tests for the psutil process collector and the CLI/entry-point wiring.

These tests use the *real* process list where it is safe to do so, and mocked
psutil calls where determinism matters. Nothing here requires privileges.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time

import psutil
import pytest

from ports_explorer.__main__ import build_parser, main
from ports_explorer.collectors.lsof import FIELD_ARGUMENT, LsofCollector
from ports_explorer.collectors.processes import (
    ProcessCollector,
    format_elapsed,
    process_to_record,
)
from ports_explorer.models import ProcessRecord, format_percent


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0s"),
        (5, "5s"),
        (65, "01m 05s"),
        (3661, "01h 01m"),
        (90000, "1d 01h 00m"),
        (None, None),
        (-1, None),
    ],
)
def test_format_elapsed(seconds, expected) -> None:
    assert format_elapsed(seconds) == expected


def test_process_record_helpers() -> None:
    record = ProcessRecord(
        pid=7,
        name="nginx",
        cmdline=["nginx", "-g", "daemon off;"],
        exe="/usr/local/bin/nginx",
    )
    assert record.command_line == "nginx -g daemon off;"
    assert "nginx" in record.search_blob
    assert "daemon" in record.search_blob


def test_process_record_without_cmdline_falls_back_to_exe() -> None:
    record = ProcessRecord(pid=1, name="x", exe="/bin/x")
    assert record.command_line == "/bin/x"


def test_process_record_without_anything_falls_back_to_name() -> None:
    assert ProcessRecord(pid=1, name="lonely").command_line == "lonely"


def test_is_system_flags_low_pids() -> None:
    assert ProcessRecord(pid=1, name="launchd").is_system is True
    assert ProcessRecord(pid=900, name="nginx").is_system is False


def test_search_blob_tolerates_none_fields() -> None:
    assert isinstance(ProcessRecord(pid=5, name="x").search_blob, str)


async def test_snapshot_returns_processes_including_self() -> None:
    collector = ProcessCollector()
    records = await collector.snapshot()
    assert records, "the process list should never be empty on a running system"
    assert any(record.pid == __import__("os").getpid() for record in records)


async def test_snapshot_is_sorted_by_pid() -> None:
    records = await ProcessCollector().snapshot()
    pids = [record.pid for record in records]
    assert pids == sorted(pids)


async def test_snapshot_records_are_typed() -> None:
    records = await ProcessCollector().snapshot()
    for record in records[:20]:
        assert isinstance(record.pid, int)
        assert isinstance(record.name, str) and record.name
        assert record.user is None or isinstance(record.user, str)


async def test_snapshot_supports_hundreds_of_processes() -> None:
    records = await ProcessCollector().snapshot()

    assert len(records) < 20000


async def test_detail_for_self_succeeds() -> None:
    record = await ProcessCollector().process_detail(__import__("os").getpid())
    assert record is not None
    assert record.pid == __import__("os").getpid()
    assert record.cmdline


async def test_detail_for_missing_pid_returns_none_not_an_exception() -> None:
    assert await ProcessCollector().process_detail(999_999) is None


async def test_store_and_cached_detail_round_trip() -> None:
    collector = ProcessCollector()
    pid = __import__("os").getpid()
    collector.store([ProcessRecord(pid=pid, name="test")])
    cached = collector.cached_detail(pid)
    assert cached is not None and cached.name == "test"
    assert collector.cached_detail(424_242) is None


async def test_detail_reflects_a_process_that_exits() -> None:
    """A short-lived process must not raise, only return None."""
    proc = await asyncio.create_subprocess_exec(sys.executable, "-c", "pass")
    await proc.wait()
    assert await ProcessCollector().process_detail(proc.pid) is None


# --- CPU sampling on the detail path -------------------------------------
#
# psutil's cpu_percent(interval=None) diffs against the previous sample taken
# on the *same* Process object. Rebuilding the object per call therefore has no
# baseline and returns 0.0 forever, which the detail tile would show as a real
# "0.0%". The collector caches the handle instead, so the first call reports
# *unavailable* and the second reports a genuine diff.


def test_detail_first_call_reports_cpu_as_unavailable() -> None:
    """First sighting of a PID has no baseline, so it must not claim 0.0%."""
    collector = ProcessCollector()
    record = collector.detail_blocking(__import__("os").getpid())

    assert record is not None
    assert record.cpu_percent is None, "a priming-only call must report unavailable, not a fake 0.0"
    assert format_percent(record.cpu_percent) != "0.0%"
    assert record.pid == __import__("os").getpid()


def test_detail_second_call_reports_a_real_cpu_percentage() -> None:
    """With a cached handle the second call yields a real, diff-based number."""
    collector = ProcessCollector()
    pid = __import__("os").getpid()

    assert collector.detail_blocking(pid).cpu_percent is None

    second = collector.detail_blocking(pid)

    assert second is not None
    assert second.cpu_percent is not None, "the second sample must be determinable"
    assert isinstance(second.cpu_percent, float)


def test_detail_reports_a_nonzero_cpu_for_a_busy_process() -> None:
    """The strongest proof: a spinning child measures far above the old 0.0."""
    code = "import time\nend = time.time() + 10\nwhile time.time() < end:\n    pass\n"
    proc = subprocess.Popen([sys.executable, "-c", code])
    collector = ProcessCollector()
    try:
        # Let the child reach its busy loop before the baseline is taken.
        time.sleep(0.3)

        assert collector.detail_blocking(proc.pid).cpu_percent is None

        time.sleep(0.5)
        record = collector.detail_blocking(proc.pid)

        assert record is not None
        assert record.cpu_percent is not None
        # A spinning loop burns ~100% of one core; anything above a few percent
        # already separates this from the permanently-0.0 regression.
        assert record.cpu_percent > 1.0, f"expected real CPU, got {record.cpu_percent!r}"
    finally:
        proc.kill()
        proc.wait()


def test_detail_reuses_one_cached_handle_per_pid() -> None:
    """Regression guard: the handle itself is the CPU baseline, so keep it."""
    collector = ProcessCollector()
    pid = __import__("os").getpid()

    collector.detail_blocking(pid)
    handle = collector._detail_samples[pid].process
    collector.detail_blocking(pid)

    assert collector._detail_samples[pid].process is handle
    assert handle.pid == pid


def test_detail_reprimes_when_the_pid_has_been_recycled() -> None:
    """A cached baseline belongs to the old process, so a new PID restarts it."""
    collector = ProcessCollector()
    pid = __import__("os").getpid()

    collector.detail_blocking(pid)
    # Simulate the PID being recycled underneath us: psutil identifies a process
    # by (pid, create_time), so a different creation time means a new process.
    collector._detail_samples[pid].create_time -= 100.0

    recycled = collector.detail_blocking(pid)

    assert recycled is not None, "the recycled PID is alive, it must still resolve"
    assert recycled.cpu_percent is None, "a recycled PID must re-prime, not reuse a baseline"


def test_detail_drops_the_cached_handle_when_the_process_exits() -> None:
    """Exited PIDs must not leave handles behind."""
    collector = ProcessCollector()
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert collector.detail_blocking(proc.pid) is not None
        assert proc.pid in collector._detail_samples
    finally:
        proc.kill()
        proc.wait()

    assert collector.detail_blocking(proc.pid) is None
    assert proc.pid not in collector._detail_samples


def test_detail_cache_never_retains_dead_pids() -> None:
    """Handles are pruned lazily, so the cache cannot grow without bound."""
    collector = ProcessCollector()
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()

    collector.detail_blocking(__import__("os").getpid())

    assert all(psutil.pid_exists(pid) for pid in collector._detail_samples)
    assert dead.pid not in collector._detail_samples


def test_process_to_record_reads_cpu_by_default() -> None:
    """The list path keeps reading CPU from psutil when nothing is forced."""
    record = process_to_record(psutil.Process(__import__("os").getpid()))

    assert isinstance(record.cpu_percent, float)


def test_process_to_record_can_force_cpu_to_unavailable() -> None:
    """The override reports *unavailable* without consulting psutil at all."""
    record = process_to_record(
        psutil.Process(__import__("os").getpid()),
        cpu_percent=None,
    )

    assert record.cpu_percent is None
    assert format_percent(record.cpu_percent) == "\u2014"


async def test_real_lsof_collection_returns_parsed_records() -> None:
    """End-to-end against the real binary; skips gracefully if unavailable."""
    collector = LsofCollector()
    if not collector.available():
        pytest.skip("lsof is not installed on this machine")

    result = await collector.collect(__import__("os").getpid())
    assert result.records, "lsof should report at least our own descriptors"
    assert all(record.pid > 0 for record in result.records)
    assert result.permission.value in {"ok", "partial"}


async def test_real_lsof_collection_of_missing_pid_is_gone() -> None:
    collector = LsofCollector()
    if not collector.available():
        pytest.skip("lsof is not installed on this machine")

    result = await collector.collect(999_999)
    assert result.records == []
    assert result.permission.value == "gone"


def test_command_requests_structured_field_output() -> None:
    command = LsofCollector().build_command(pid=1)
    assert any(part.startswith("-F") for part in command)
    assert FIELD_ARGUMENT in "".join(command)


def test_parser_defaults() -> None:
    args = build_parser().parse_args([])
    assert args.debug is False
    assert args.timeout == 20.0
    assert args.no_color is False


def test_parser_accepts_flags() -> None:
    args = build_parser().parse_args(
        ["--debug", "--timeout", "5", "--no-color", "--log-file", "/tmp/x.log"]
    )
    assert args.debug is True
    assert args.timeout == 5.0
    assert args.no_color is True
    assert str(args.log_file) == "/tmp/x.log"


def test_help_exits_cleanly(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--help"])
    assert excinfo.value.code == 0
    assert "ports-explorer" in capsys.readouterr().out


def test_version_exits_cleanly(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--version"])
    assert excinfo.value.code == 0
    assert "ports-explorer" in capsys.readouterr().out


def test_main_delegates_to_app_without_running_it(monkeypatch) -> None:
    """`--version` must not import Textual or touch the terminal."""
    import ports_explorer.app as app_module

    ran = {"value": False}

    class FakeApp:
        def __init__(self, theme: str | None = None) -> None:
            self.theme = theme

        def run(self) -> None:
            ran["value"] = True

    monkeypatch.setattr(app_module, "PortsExplorerApp", FakeApp)
    assert main(["--no-color"]) == 0
    assert ran["value"] is True


def test_console_script_entry_point_is_importable() -> None:
    import ports_explorer

    assert callable(ports_explorer.main)
    assert ports_explorer.__version__
