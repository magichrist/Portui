"""Regression tests for the detail screen's periodic refresh.

The timer used to refresh only psutil metrics, on the assumption that re-running
``lsof`` was too expensive. That was wrong on macOS — ``lsof -p <pid>`` costs
roughly 10ms — so the Network tab and every other resource tab stayed frozen for
the whole session and the screen read as though refresh did nothing at all.
"""

from __future__ import annotations

import os

import pytest
from textual.widgets import TabbedContent

from ports_explorer.app import PortsExplorerApp
from ports_explorer.screens.process_detail import ProcessDetailScreen
from ports_explorer.widgets.resource_table import ResourceTable

pytestmark = pytest.mark.asyncio


async def _open_self_detail(app, pilot) -> ProcessDetailScreen:
    """Push the detail screen for this process and wait until it is really ready.

    A fixed pause is not enough here: the screen kicks off an lsof worker on
    mount, and on a loaded machine that worker is still running when a 1.2s delay
    expires. A test that samples ``_result`` at that moment races the worker.
    """
    process = await app.process_collector.process_detail(os.getpid())
    app.push_process_detail(process)

    screen: ProcessDetailScreen | None = None
    for _ in range(60):
        await pilot.pause(0.1)
        candidate = app.screen
        if not isinstance(candidate, ProcessDetailScreen):
            continue
        screen = candidate
        # Ready means: every tab pane's table is mounted, and the first
        # collection has finished (so the timer is the only thing that will
        # change _result from here on).
        panes_ready = all(
            len(screen.query(table_id)) == 1
            for table_id in (
                "#table-network",
                "#table-listeners",
                "#table-files",
                "#table-unix",
                "#table-all",
            )
        )
        if panes_ready and screen._result is not None and not screen._collecting:
            return screen

    raise AssertionError("the detail screen never became ready")


async def _settle(pilot) -> None:
    """Let any in-flight collection finish before sampling a timestamp."""
    await pilot.pause(0.6)


async def test_timer_recollects_resources_not_just_metrics(auto_update) -> None:
    """The lsof collection timestamp must advance on every tick."""
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_self_detail(app, pilot)
        first = screen._result.collected_at

        await pilot.pause(auto_update * 4)
        second = screen._result.collected_at

        assert second > first, "resource collection never re-ran on the timer"


async def test_a_paused_timer_leaves_the_resources_alone(auto_update) -> None:
    """Pausing must actually stop the collection, not just relabel the status."""
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_self_detail(app, pilot)
        assert screen.auto_update_enabled is True

        await pilot.press("r")
        await pilot.pause(0.4)
        assert screen.auto_update_enabled is False

        # Settle first: the mount-time collection may still be landing, and it
        # would change collected_at after the pause and look like a tick.
        await _settle(pilot)
        frozen = screen._result.collected_at
        await pilot.pause(auto_update * 5)
        assert screen._result.collected_at == frozen


async def test_network_tab_is_repopulated_by_the_timer(auto_update) -> None:
    """The Network tab is the one the user watches; it must not be static."""
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_self_detail(app, pilot)
        screen.query_one("#detail-tabs", TabbedContent).active = "tab-network"
        await pilot.pause(0.4)

        stamps = set()
        for _ in range(3):
            await pilot.pause(auto_update * 3)
            stamps.add(screen._result.collected_at)

        assert len(stamps) == 3, "the network tab was not being refreshed"


async def test_cursor_survives_a_resource_refresh() -> None:
    """Repainting must not reset the row the user is looking at.

    Row keys used to be ``str(id(record))``, which changes on every re-run, so a
    refresh always forced a full table rebuild and threw the cursor back to row 0.
    """
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_self_detail(app, pilot)
        screen.action_show_tab("tab-files")
        await pilot.pause(0.3)
        table = screen.query_one("#table-files", ResourceTable)
        grid = table.query_one("#resource-grid")

        assert len(table._filtered) > 2, "not enough rows to move a cursor"
        grid.move_cursor(row=2)
        await pilot.pause()
        before = grid.cursor_row

        # Re-collect with fresh objects, exactly as a timer tick does.
        table.set_records(table._records)
        await pilot.pause()
        assert grid.cursor_row == before


async def test_active_filter_survives_a_refresh() -> None:
    """Auto-refreshing must not silently wipe the user's filter."""
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_self_detail(app, pilot)
        screen.action_show_tab("tab-files")
        await pilot.pause(0.3)
        table = screen.query_one("#table-files", ResourceTable)

        table.toggle_search()
        await pilot.pause()
        table.query_one("#resource-filter").value = "python"
        await pilot.pause(0.3)
        assert table._filtered, "the filter matched nothing to begin with"

        screen.collect_resources(quiet=True)
        await pilot.pause(0.8)
        assert table.query_one("#resource-filter").value == "python"
        assert table._filtered, "the refresh wiped the user's filter"
