"""The two-key arming contract on the ``k`` binding.

Killing a process is irreversible, so the UI must never signal anything on a
single keystroke. These tests drive the real screens and assert that the first
``k`` only arms, that a second ``k`` is required, and that any other input
cancels the pending action.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import psutil
import pytest
from textual.widgets import DataTable, Input

from ports_explorer.app import PortsExplorerApp
from ports_explorer.screens.common import ARM_TIMEOUT
from ports_explorer.screens.process_detail import ProcessDetailScreen
from ports_explorer.screens.process_list import ProcessListScreen

pytestmark = pytest.mark.asyncio


def _spawn() -> subprocess.Popen:
    """A real, expendable child process so the tests can verify it dies."""
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])


async def _wait_gone(child: subprocess.Popen, timeout: float = 5.0) -> bool:
    """Wait for the child to be reaped.

    ``pid_exists`` is the wrong tool here: a terminated child stays visible as a
    zombie until its parent waits on it, so polling the PID reports "still
    alive" for a process that is in fact already dead. Waiting on the Popen
    reaps it and gives the real answer.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            child.wait(timeout=0.1)
            return True
        except subprocess.TimeoutExpired:
            continue
    return child.poll() is not None


async def test_single_k_does_not_signal_anything() -> None:
    """The first press must arm only — nothing may be sent to the process."""
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _wait_for_list(pilot)
        row = _find_row(screen, "python")
        screen.query_one("#process-grid", DataTable).move_cursor(row=row)
        await pilot.pause(0.2)
        pid = screen._filtered[row].pid

        await pilot.press("k")
        await pilot.pause(0.3)

        assert screen._kill_armed_pid == pid
        assert screen._kill_stage == "term"


async def test_arming_expires_after_the_timeout() -> None:
    """A stale arm must not leave one stray keypress enough to kill something."""
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _wait_for_list(pilot)
        row = _find_row(screen, "python")
        screen.query_one("#process-grid", DataTable).move_cursor(row=row)
        await pilot.pause(0.2)

        await pilot.press("k")
        await pilot.pause(0.2)
        assert screen._kill_armed_pid is not None

        # Push the deadline into the past rather than sleeping for 5s.
        screen._kill_deadline = time.monotonic() - 1
        assert screen._kill_expired() is True

        # The next k re-arms instead of firing, because the old one lapsed.
        await pilot.press("k")
        await pilot.pause(0.2)
        assert screen._kill_stage == "term"


async def test_another_key_cancels_a_pending_terminate() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _wait_for_list(pilot)
        row = _find_row(screen, "python")
        screen.query_one("#process-grid", DataTable).move_cursor(row=row)
        await pilot.pause(0.2)

        await pilot.press("k")
        await pilot.pause(0.2)
        assert screen._kill_armed_pid is not None

        await pilot.press("j")  # a harmless, non-action key
        await pilot.pause(0.2)
        assert screen._kill_armed_pid is None


async def test_moving_the_cursor_cancels_a_pending_terminate() -> None:
    """Confirming must never fire against a process other than the one armed."""
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _wait_for_list(pilot)
        row = _find_row(screen, "python")
        grid = screen.query_one("#process-grid", DataTable)
        grid.move_cursor(row=row)
        await pilot.pause(0.2)
        armed = screen._filtered[row].pid

        await pilot.press("k")
        await pilot.pause(0.2)
        assert screen._kill_armed_pid == armed

        grid.move_cursor(row=0)
        await pilot.pause(0.3)
        assert screen._kill_armed_pid is None


async def test_typing_k_in_the_search_box_does_not_arm() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _wait_for_list(pilot)
        screen.action_focus_search()
        await pilot.pause(0.2)
        assert isinstance(screen.focused, Input)

        await pilot.press("k")
        await pilot.pause(0.2)

        assert screen._kill_armed_pid is None
        assert screen.query_one("#process-search", Input).value == "k"


async def test_double_k_terminates_the_target_process() -> None:
    """The one test that really kills something: a disposable child."""
    child = _spawn()
    try:
        app = PortsExplorerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            screen = await _wait_for_list(pilot)
            row = _find_row(screen, str(child.pid))
            screen.query_one("#process-grid", DataTable).move_cursor(row=row)
            await pilot.pause(0.3)
            pid = screen._filtered[row].pid
            assert pid == child.pid

            await pilot.press("k")
            await pilot.pause(0.4)
            assert screen._kill_armed_pid == pid, "first k should only arm"

            await pilot.press("k")
            await pilot.pause(1.5)

            assert await _wait_gone(child), "the child should have been terminated"
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()


async def test_detail_screen_kills_its_own_process() -> None:
    child = _spawn()
    try:
        app = PortsExplorerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await _wait_for_list(pilot)
            record = None
            for process in app.screen._all:
                if process.pid == child.pid:
                    record = process
                    break
            assert record is not None, "the spawned child should be listed"

            app.push_process_detail(record)
            await pilot.pause(1.2)
            screen = await _wait_for_detail(pilot)

            await pilot.press("k")
            await pilot.pause(0.4)
            assert screen._kill_armed_pid == child.pid

            await pilot.press("k")
            await pilot.pause(1.5)

            assert await _wait_gone(child)
            # The screen describes a process that no longer exists, so it steps
            # back to the list rather than showing a ghost.
            assert isinstance(app.screen, ProcessListScreen)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()


async def test_detail_screen_k_does_nothing_when_armed_on_another_pid() -> None:
    """A confirm is bound to the armed PID, not to whatever is on screen."""
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_for_list(pilot)
        process = app.screen._all[0]
        app.push_process_detail(process)
        await pilot.pause(1.2)
        screen = await _wait_for_detail(pilot)

        # Arm against a PID that is not this screen's process.
        screen._kill_armed_pid = process.pid + 100000
        screen._kill_stage = "term"
        screen._kill_deadline = time.monotonic() + ARM_TIMEOUT

        await pilot.press("k")
        await pilot.pause(0.3)

        # The stale arm was discarded and a fresh one created for this process,
        # rather than the other PID being signalled.
        assert screen._kill_armed_pid == process.pid
        assert psutil.pid_exists(process.pid), "the inspected process was killed"


# -- helpers ------------------------------------------------------------


async def _wait_for_list(pilot, timeout: float = 6.0) -> ProcessListScreen:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await pilot.pause(0.15)
        screen = pilot.app.screen if hasattr(pilot, "app") else None
        if isinstance(screen, ProcessListScreen) and screen._all:
            return screen
    raise AssertionError("the process list never populated")


async def _wait_for_detail(pilot, timeout: float = 6.0) -> ProcessDetailScreen:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await pilot.pause(0.15)
        if isinstance(pilot.app.screen, ProcessDetailScreen):
            return pilot.app.screen
    raise AssertionError("the detail screen never mounted")


def _find_row(screen: ProcessListScreen, needle: str) -> int:
    for index, process in enumerate(screen._filtered):
        if needle in process.search_blob:
            return index
    raise AssertionError(f"no row matching {needle!r} in the process list")


@pytest.mark.asyncio
async def test_this_module_never_kills_the_test_runner() -> None:
    """A guard on the guard: the runner's own PID must always be refused."""
    from ports_explorer.services.kill_process import KillRefused, validate_target

    with pytest.raises(KillRefused):
        validate_target(os.getpid())
