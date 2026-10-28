"""Sending termination signals to a process.

This is the one part of the application that is not read-only, so it is kept
deliberately small, side-effect free of any UI, and hard to fire by accident.

Safety rules enforced here rather than in the screens:

* A signal is never sent to PID 0 or 1, to our own process, or to any PID the
  caller could not have legitimately meant.
* ``terminate`` always sends SIGTERM first. SIGKILL is a separate, explicit
  escalation that only runs when the caller decides to, after checking the
  process actually survived SIGTERM.
* Nothing here escalates privileges. A process owned by another user simply
  fails with ``permission-denied`` and the user is told to use ``sudo`` if that
  is really what they want.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import os
import signal
import time
from dataclasses import dataclass

import psutil

log = logging.getLogger(__name__)

__all__ = [
    "GRACE_PERIOD",
    "KillOutcome",
    "KillRefused",
    "KillResult",
    "force_kill",
    "is_alive",
    "terminate",
    "validate_target",
]


GRACE_PERIOD = 2.0
"""Seconds to wait after SIGTERM before reporting that it was ignored."""


class KillOutcome(enum.StrEnum):
    """What actually happened when we tried to signal a process."""

    TERMINATED = "terminated"
    """The process is gone."""

    SENT = "sent"
    """The signal was delivered; the caller must now wait and re-check liveness.

    This is an internal handoff value: ``terminate``/``force_kill`` consume it
    and answer with the final outcome, so it never reaches the user directly.
    """

    IGNORED = "ignored"
    """Still alive after SIGTERM; a hard kill may be needed."""

    PERMISSION_DENIED = "permission-denied"
    """The OS refused; the process belongs to another user."""

    NOT_FOUND = "not-found"
    """It exited on its own between selection and the signal."""

    REFUSED = "refused"
    """We declined to signal this PID, on purpose."""

    ERROR = "error"


@dataclass(frozen=True, slots=True)
class KillResult:
    """Outcome plus a message that can be shown to the user verbatim."""

    outcome: KillOutcome
    detail: str
    pid: int
    signal_number: int | None = None

    @property
    def ok(self) -> bool:
        return self.outcome in (KillOutcome.TERMINATED, KillOutcome.NOT_FOUND)


class KillRefused(Exception):
    """Raised when a PID is structurally unsafe to signal."""


def validate_target(pid: int, *, self_pid: int | None = None) -> None:
    """Reject PIDs we must never signal. Raises :class:`KillRefused`.

    ``self_pid`` defaults to this process. Killing our own PID would take the
    whole TUI down mid-render, and PID 1 / 0 are never what the user selected.
    """
    own = os.getpid() if self_pid is None else self_pid
    if pid <= 1:
        raise KillRefused(f"PID {pid} is a kernel or init process and is never signalled.")
    if pid == own:
        raise KillRefused("Refusing to terminate Ports Explorer itself.")


def is_alive(pid: int) -> bool:
    """True only if the PID exists and has not become a zombie."""
    try:
        # One handle for both reads: constructing two Process objects around a
        # potential exit is a race, and a fresh handle loses nothing anyway.
        process = psutil.Process(pid)
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        # It exists; we simply are not allowed to look closely.
        return True
    except Exception:
        log.debug("liveness check failed for PID %s", pid, exc_info=True)
        return False


def _signal(pid: int, sig: int, *, self_pid: int | None = None) -> KillResult:
    try:
        validate_target(pid, self_pid=self_pid)
    except KillRefused as refusal:
        return KillResult(KillOutcome.REFUSED, str(refusal), pid)

    try:
        process = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return KillResult(
            KillOutcome.NOT_FOUND,
            f"PID {pid} already exited.",
            pid,
        )
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("could not open PID %s", pid, exc_info=True)
        return KillResult(KillOutcome.ERROR, f"Could not open PID {pid}: {exc}", pid)

    try:
        if sig == signal.SIGTERM:
            process.terminate()
        else:
            process.kill()
    except psutil.AccessDenied:
        return KillResult(
            KillOutcome.PERMISSION_DENIED,
            f"Not permitted to signal PID {pid} — it belongs to another user. "
            "Run Ports Explorer yourself under sudo if that is really what you want.",
            pid,
            signal_number=sig,
        )
    except psutil.NoSuchProcess:
        return KillResult(
            KillOutcome.NOT_FOUND, f"PID {pid} already exited.", pid, signal_number=sig
        )
    except Exception as exc:
        log.exception("failed to signal PID %s", pid)
        return KillResult(KillOutcome.ERROR, f"Could not signal PID {pid}: {exc}", pid, sig)

    return KillResult(
        KillOutcome.SENT,
        f"{signal.Signals(sig).name} was delivered to PID {pid}; waiting to see whether it exits.",
        pid,
        signal_number=sig,
    )


async def terminate(
    pid: int,
    *,
    grace_period: float = GRACE_PERIOD,
    self_pid: int | None = None,
    poll_interval: float = 0.1,
) -> KillResult:
    """Send SIGTERM, then wait up to ``grace_period`` for a clean exit.

    Returns :attr:`KillOutcome.TERMINATED` if the process went away, or
    :attr:`KillOutcome.IGNORED` if it is still running — which is the signal to
    the caller that a hard kill is the only remaining option.
    """
    sent = _signal(pid, signal.SIGTERM, self_pid=self_pid)
    if sent.outcome is not KillOutcome.SENT:
        return sent

    deadline = time.monotonic() + grace_period
    while time.monotonic() < deadline:
        await asyncio.sleep(poll_interval)
        if not is_alive(pid):
            return KillResult(
                KillOutcome.TERMINATED,
                f"PID {pid} exited after SIGTERM.",
                pid,
                signal_number=signal.SIGTERM,
            )

    return KillResult(
        KillOutcome.IGNORED,
        f"PID {pid} ignored SIGTERM and is still running after {grace_period:.0f}s.",
        pid,
        signal_number=signal.SIGTERM,
    )


async def force_kill(
    pid: int,
    *,
    self_pid: int | None = None,
    wait: float = 2.0,
    poll_interval: float = 0.05,
) -> KillResult:
    """Send SIGKILL and wait briefly for the process to disappear.

    SIGKILL cannot be caught, blocked or ignored. The caller is responsible for
    only reaching this after a SIGTERM was ignored.
    """
    sent = _signal(pid, signal.SIGKILL, self_pid=self_pid)
    if sent.outcome is not KillOutcome.SENT:
        return sent

    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        await asyncio.sleep(poll_interval)
        if not is_alive(pid):
            return KillResult(
                KillOutcome.TERMINATED,
                f"PID {pid} was force killed.",
                pid,
                signal_number=signal.SIGKILL,
            )

    return KillResult(
        KillOutcome.ERROR,
        f"PID {pid} survived SIGKILL, which should not be possible. "
        "It may be stuck in uninterruptible I/O.",
        pid,
        signal_number=signal.SIGKILL,
    )
