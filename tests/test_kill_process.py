"""Tests for the one non-read-only path: the two-key terminate action."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time

import pytest

from ports_explorer.services.kill_process import (
    KillOutcome,
    KillRefused,
    force_kill,
    is_alive,
    terminate,
    validate_target,
)


def _spawn_sleeper() -> subprocess.Popen:
    """A child process that ignores SIGTERM, for testing the escalation path."""
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(120)",
        ]
    )


def _spawn_normal() -> subprocess.Popen:
    """A child process with default signal handling, for testing SIGTERM."""
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])


async def _wait_gone(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_alive(pid):
            return True
        await asyncio.sleep(0.05)
    return not is_alive(pid)


# -- guardrails ---------------------------------------------------------


def test_refuses_pid_one_and_pid_zero() -> None:
    for pid in (0, 1):
        with pytest.raises(KillRefused):
            validate_target(pid)


def test_refuses_negative_pids() -> None:
    with pytest.raises(KillRefused):
        validate_target(-1)


def test_refuses_to_kill_our_own_process() -> None:
    with pytest.raises(KillRefused):
        validate_target(os.getpid())


def test_refusal_is_reported_not_raised_to_the_ui() -> None:
    """Screens get a result object, never an exception they must catch."""
    result = asyncio.run(terminate(1, self_pid=999999))
    assert result.outcome is KillOutcome.REFUSED
    assert "never signalled" in result.detail


@pytest.mark.asyncio
async def test_terminate_refuses_pid_one() -> None:
    result = await terminate(1, self_pid=999999)
    assert result.outcome is KillOutcome.REFUSED


async def test_terminate_refuses_our_own_pid() -> None:
    result = await terminate(os.getpid())
    assert result.outcome is KillOutcome.REFUSED
    assert "itself" in result.detail


# -- real signals -------------------------------------------------------


async def test_sigterm_terminates_a_normal_process() -> None:
    child = _spawn_normal()
    try:
        result = await terminate(child.pid)
        assert result.outcome is KillOutcome.TERMINATED
        assert result.signal_number == signal.SIGTERM
        assert await _wait_gone(child.pid)
    finally:
        child.kill()
        child.wait()


@pytest.mark.asyncio
async def test_sigterm_on_a_process_that_ignores_it_reports_ignored() -> None:
    """This is what triggers the explicit SIGKILL escalation prompt."""
    child = _spawn_sleeper()
    await asyncio.sleep(0.4)
    try:
        result = await terminate(child.pid, grace_period=0.6)
        assert result.outcome is KillOutcome.IGNORED
        assert "SIGTERM" in result.detail
        assert is_alive(child.pid), "the process should still be running"
    finally:
        child.kill()
        child.wait()


@pytest.mark.asyncio
async def test_force_kill_stops_a_process_that_ignored_sigterm() -> None:
    child = _spawn_sleeper()
    await asyncio.sleep(0.4)
    try:
        first = await terminate(child.pid, grace_period=0.4)
        assert first.outcome is KillOutcome.IGNORED

        second = await force_kill(child.pid)
        assert second.outcome is KillOutcome.TERMINATED
        assert second.signal_number == signal.SIGKILL
        assert await _wait_gone(child.pid)
    finally:
        child.kill()
        child.wait()


@pytest.mark.asyncio
async def test_terminating_an_already_dead_pid_reports_not_found() -> None:
    child = _spawn_normal()
    pid = child.pid
    child.kill()
    child.wait()

    result = await terminate(pid)
    assert result.outcome is KillOutcome.NOT_FOUND


async def test_is_alive_is_false_for_a_reaped_process() -> None:
    child = _spawn_normal()
    pid = child.pid
    child.kill()
    child.wait()
    await asyncio.sleep(0.1)
    assert is_alive(pid) is False


async def test_is_alive_is_true_for_our_own_process() -> None:
    assert is_alive(os.getpid()) is True
