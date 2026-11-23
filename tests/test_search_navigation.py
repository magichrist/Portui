"""Arrow-key navigation while a search field is focused."""

from __future__ import annotations

import pytest
from tests.conftest import load_fixture
from textual.widgets import DataTable, Input

from ports_explorer.app import PortsExplorerApp
from ports_explorer.collectors.lsof import parse_lsof_output
from ports_explorer.models import ProcessRecord
from ports_explorer.screens.process_detail import ProcessDetailScreen
from ports_explorer.screens.resource_browser import (
    ResourceBrowserScreen,
    ResourceDetailScreen,
)


async def test_main_search_arrows_move_highlight_without_leaving_input() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.7)
        screen = app.screen
        table = screen.query_one("#process-grid", DataTable)
        assert table.row_count >= 3
        table.move_cursor(row=0)

        search = screen.query_one("#process-search", Input)
        search.focus()
        search.value = "a"
        await pilot.pause()

        await pilot.press("down")
        await pilot.pause()
        assert table.cursor_row == 1
        assert screen.focused is search
        assert search.value == "a"

        await pilot.press("up")
        await pilot.pause()
        assert table.cursor_row == 0
        assert screen.focused is search


async def test_alt_arrows_jump_ten_rows_while_typing() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.7)
        screen = app.screen
        table = screen.query_one("#process-grid", DataTable)
        assert table.row_count >= 12
        table.move_cursor(row=0)

        search = screen.query_one("#process-search", Input)
        search.focus()
        await pilot.pause()

        await pilot.press("alt+down")
        await pilot.pause()
        assert table.cursor_row == 10
        assert screen.focused is search

        await pilot.press("alt+up")
        await pilot.pause()
        assert table.cursor_row == 0


@pytest.mark.parametrize("key", ["alt+down", "ctrl+down", "super+down"])
async def test_jump_key_aliases_move_ten_rows(key: str) -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.7)
        screen = app.screen
        table = screen.query_one("#process-grid", DataTable)
        assert table.row_count >= 12
        table.move_cursor(row=0)
        table.focus()
        await pilot.pause()

        await pilot.press(key)
        await pilot.pause()
        assert table.cursor_row == 10


async def test_alt_arrows_jump_ten_rows_with_table_focused() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.7)
        screen = app.screen
        table = screen.query_one("#process-grid", DataTable)
        assert table.row_count >= 12
        table.move_cursor(row=0)
        table.focus()
        await pilot.pause()

        await pilot.press("alt+down")
        await pilot.pause()
        assert table.cursor_row == 10

        await pilot.press("alt+up")
        await pilot.pause()
        assert table.cursor_row == 0


async def test_terminal_escape_arrow_pair_jumps_ten_rows() -> None:
    """macOS terminals report Option+Arrow as an escape followed by an arrow."""
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.7)
        screen = app.screen
        table = screen.query_one("#process-grid", DataTable)
        assert table.row_count >= 12
        table.move_cursor(row=0)
        table.focus()
        await pilot.pause()

        await pilot.press("escape")
        await pilot.press("down")
        await pilot.pause()
        assert table.cursor_row == 10
        assert isinstance(app.screen, type(screen))

        await pilot.press("escape")
        await pilot.press("up")
        await pilot.pause()
        assert table.cursor_row == 0


async def test_browser_search_arrows_move_highlight_without_leaving_input() -> None:
    records, _ = parse_lsof_output(load_fixture("lsof_states.txt"))
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.4)
        app.push_resource_search(records, ProcessRecord(pid=777, name="webserver"), None)
        await pilot.pause(0.3)
        screen = app.screen
        assert isinstance(screen, ResourceBrowserScreen)

        table = screen.query_one("#browser-grid", DataTable)
        table.move_cursor(row=0)
        search = screen.query_one("#browser-search", Input)
        search.focus()
        await pilot.pause()

        await pilot.press("down")
        await pilot.pause()
        assert table.cursor_row == 1
        assert screen.focused is search


async def test_browser_alt_arrows_jump_ten_rows_with_grid_focused() -> None:
    records, _ = parse_lsof_output(load_fixture("lsof_states.txt"))
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.4)
        app.push_resource_search(records, ProcessRecord(pid=777, name="webserver"), None)
        await pilot.pause(0.3)
        screen = app.screen
        assert isinstance(screen, ResourceBrowserScreen)
        grid = screen.query_one("#browser-grid", DataTable)
        grid.move_cursor(row=0)
        grid.focus()
        await pilot.pause()

        await pilot.press("alt+down")
        await pilot.pause()
        # The fixture has only a handful of rows, so the jump clamps to the end.
        assert grid.cursor_row == grid.row_count - 1
        assert grid.row_count < 11


async def test_table_filter_arrows_move_highlight_without_leaving_input() -> None:
    records, _ = parse_lsof_output(load_fixture("lsof_states.txt"))
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.4)
        app.push_process_detail(ProcessRecord(pid=777, name="webserver"))
        await pilot.pause(0.4)
        screen = app.screen
        assert isinstance(screen, ProcessDetailScreen)

        await pilot.press("2")
        await pilot.pause()
        table = screen._active_table()
        assert table is not None
        table.set_records(records, keep_query=False)
        await pilot.pause()
        grid = table.query_one("#resource-grid", DataTable)
        grid.move_cursor(row=0)

        await pilot.press("slash")
        await pilot.pause()
        search = table.query_one("#resource-filter", Input)
        assert screen.focused is search

        await pilot.press("down")
        await pilot.pause()
        assert grid.cursor_row == 1
        assert screen.focused is search

        await pilot.press("up")
        await pilot.pause()
        assert grid.cursor_row == 0
        assert screen.focused is search


async def test_main_search_enter_opens_highlighted_process() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.7)
        screen = app.screen
        table = screen.query_one("#process-grid", DataTable)
        assert table.row_count >= 2
        table.move_cursor(row=1)
        target = screen._filtered[1]

        search = screen.query_one("#process-search", Input)
        search.focus()
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, ProcessDetailScreen)
        assert app.screen.process.pid == target.pid


async def test_browser_search_enter_opens_highlighted_resource() -> None:
    records, _ = parse_lsof_output(load_fixture("lsof_states.txt"))
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.4)
        app.push_resource_search(records, ProcessRecord(pid=777, name="webserver"), None)
        await pilot.pause(0.3)
        screen = app.screen
        assert isinstance(screen, ResourceBrowserScreen)

        grid = screen.query_one("#browser-grid", DataTable)
        grid.move_cursor(row=1)
        target = screen._shown[1]

        search = screen.query_one("#browser-search", Input)
        search.focus()
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, ResourceDetailScreen)
        assert app.screen.record is target


async def test_table_filter_enter_opens_highlighted_resource() -> None:
    records, _ = parse_lsof_output(load_fixture("lsof_states.txt"))
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.4)
        app.push_process_detail(ProcessRecord(pid=777, name="webserver"))
        await pilot.pause(0.4)
        screen = app.screen
        assert isinstance(screen, ProcessDetailScreen)

        await pilot.press("2")
        await pilot.pause()
        table = screen._active_table()
        assert table is not None
        table.set_records(records, keep_query=False)
        await pilot.pause()
        grid = table.query_one("#resource-grid", DataTable)
        grid.move_cursor(row=1)
        target = table._filtered[1]

        await pilot.press("slash")
        await pilot.pause()
        assert screen.focused is table.query_one("#resource-filter", Input)

        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, ResourceDetailScreen)
        assert app.screen.record is target
