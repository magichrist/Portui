"""Keyboard navigation and back-behaviour contracts.

These drive the real app through Textual's pilot so the bindings, not just the
handlers, are exercised.
"""

from __future__ import annotations

import os
from collections.abc import Callable

import pytest
from textual.widgets import Button, DataTable, Input, TabbedContent

from ports_explorer.app import PortsExplorerApp
from ports_explorer.screens.process_detail import ProcessDetailScreen
from ports_explorer.screens.process_list import ProcessListScreen
from ports_explorer.screens.resource_browser import (
    ResourceBrowserScreen,
    ResourceDetailScreen,
)

pytestmark = pytest.mark.asyncio


async def _open_detail(app, pilot) -> ProcessDetailScreen:
    await pilot.pause(0.5)
    await pilot.press("enter")
    await pilot.pause(1.2)
    assert isinstance(app.screen, ProcessDetailScreen)
    return app.screen


async def _push_detail_for_self(app, pilot) -> ProcessDetailScreen:
    process = await app.process_collector.process_detail(os.getpid())
    app.push_process_detail(process)
    await pilot.pause(1.2)
    return app.screen


async def test_up_down_navigates_process_rows() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.5)
        screen = app.screen
        table = screen.query_one("#process-grid", DataTable)
        table.focus()
        start = table.cursor_row
        await pilot.press("down")
        await pilot.press("down")
        assert table.cursor_row == min(start + 2, table.row_count - 1)
        await pilot.press("up")
        assert table.cursor_row == max(start + 1, 0)


async def test_slash_focuses_search_and_escape_returns_to_table() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.5)
        screen = app.screen
        screen.query_one("#process-grid", DataTable).focus()

        await pilot.press("slash")
        await pilot.pause()
        assert isinstance(screen.focused, Input)

        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(screen.focused, DataTable)


async def test_ctrl_f_also_focuses_search() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.5)
        screen = app.screen
        screen.query_one("#process-grid", DataTable).focus()
        await pilot.press("ctrl+f")
        await pilot.pause()
        assert isinstance(screen.focused, Input)


async def test_escape_clears_query_before_leaving_the_screen() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.5)
        screen = app.screen
        search = screen.query_one("#process-search", Input)
        search.value = "launchd"
        await pilot.pause()
        assert search.value == "launchd"

        await pilot.press("escape")
        await pilot.pause()
        assert search.value == ""
        assert isinstance(app.screen, ProcessListScreen)


async def test_enter_opens_the_selected_process() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.5)
        screen = app.screen
        table = screen.query_one("#process-grid", DataTable)
        table.focus()
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause(1.0)
        assert isinstance(app.screen, ProcessDetailScreen)


async def test_escape_returns_from_detail_to_list() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await _open_detail(app, pilot)
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, ProcessListScreen)


async def test_backspace_returns_from_detail_to_list() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await _open_detail(app, pilot)
        await pilot.press("backspace")
        await pilot.pause()
        assert isinstance(app.screen, ProcessListScreen)


async def test_back_button_is_visible_and_works_on_detail_screen() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_detail(app, pilot)
        button = screen.query_one("#back-button", Button)
        assert button.display
        await pilot.click("#back-button")
        await pilot.pause()
        assert isinstance(app.screen, ProcessListScreen)


async def test_breadcrumb_is_rendered_on_detail_screen() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_detail(app, pilot)
        assert screen.query_one("#breadcrumb")
        assert screen.query_one("#banner")


async def test_back_navigation_restores_the_selected_process() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.5)
        screen = app.screen
        table = screen.query_one("#process-grid", DataTable)
        table.focus()
        await pilot.press("down")
        await pilot.press("down")
        chosen_pid = screen._filtered[table.cursor_row].pid

        await pilot.press("enter")
        await pilot.pause(0.9)
        assert isinstance(app.screen, ProcessDetailScreen)
        assert app.screen.process.pid == chosen_pid

        await pilot.press("escape")
        await pilot.pause(0.3)
        assert isinstance(app.screen, ProcessListScreen)
        restored = app.screen.query_one("#process-grid", DataTable)
        assert app.screen._filtered[restored.cursor_row].pid == chosen_pid


async def test_number_keys_switch_detail_tabs() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _push_detail_for_self(app, pilot)
        tabs = screen.query_one("#detail-tabs", TabbedContent)

        for key, expected in (
            ("1", "tab-overview"),
            ("2", "tab-network"),
            ("3", "tab-listeners"),
            ("4", "tab-files"),
            ("5", "tab-unix"),
            ("6", "tab-all"),
        ):
            await pilot.press(key)
            await pilot.pause(0.25)
            assert tabs.active == expected, f"key {key} did not open {expected}"


async def test_slash_opens_in_table_filter_on_detail_screen() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _push_detail_for_self(app, pilot)
        await pilot.press("2")
        await pilot.pause(0.2)
        await pilot.press("slash")
        await pilot.pause()
        table = screen.query_one("#table-network")
        assert table.has_class("-searching")


async def test_escape_closes_table_filter_before_leaving_screen() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _push_detail_for_self(app, pilot)
        await pilot.press("2")
        await pilot.pause(0.2)
        await pilot.press("slash")
        await pilot.pause()

        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, ProcessDetailScreen)
        assert not screen.query_one("#table-network").has_class("-searching")

        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, ProcessListScreen)


async def test_right_arrow_inside_a_table_is_not_back_navigation() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _push_detail_for_self(app, pilot)
        await pilot.press("2")
        await pilot.pause(0.3)
        table = screen.query_one("#table-network")
        table.focus_table()
        await pilot.pause()

        await pilot.press("right")
        await pilot.pause()
        assert isinstance(app.screen, ProcessDetailScreen)


def _count_calls(screen) -> Callable[[], int]:
    """Count calls to a screen's worker without awaiting the returned Worker.

    ``@work`` turns the method into something that returns a Worker rather than
    a coroutine, so the spy has to be a plain function to run eagerly.
    """
    original = screen.collect_processes
    calls = 0

    def counting(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    screen.collect_processes = counting
    return lambda: calls


async def test_r_toggles_auto_update_on_the_detail_screen(auto_update) -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _push_detail_for_self(app, pilot)
        before = len(screen._deduped)
        assert screen.auto_update_enabled is True

        # First press pauses: no re-collection, and we stay put.
        await pilot.press("r")
        await pilot.pause(1.2)
        assert isinstance(app.screen, ProcessDetailScreen)
        assert screen.auto_update_enabled is False
        assert len(screen._deduped) == before

        # Second press resumes and catches up with a full lsof collection.
        await pilot.press("r")
        await pilot.pause(1.2)
        assert isinstance(app.screen, ProcessDetailScreen)
        assert screen.auto_update_enabled is True
        assert len(screen._deduped) == before


async def test_r_toggles_auto_update_on_the_process_list(auto_update) -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.6)
        screen = app.screen
        assert len(screen._all) > 0
        assert screen.auto_update_enabled is True

        await pilot.press("r")
        await pilot.pause(0.4)
        assert isinstance(app.screen, ProcessListScreen)
        assert screen.auto_update_enabled is False

        await pilot.press("r")
        await pilot.pause(0.8)
        assert isinstance(app.screen, ProcessListScreen)
        assert screen.auto_update_enabled is True
        assert len(screen._all) > 0


async def test_resuming_auto_update_recollects_resources(auto_update) -> None:
    """Resuming must actually refresh, not just restart the timer."""
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _push_detail_for_self(app, pilot)
        await pilot.press("r")
        await pilot.pause(0.4)
        assert screen.auto_update_enabled is False

        first = screen._result.collected_at
        await pilot.press("r")
        await pilot.pause(1.2)
        assert screen.auto_update_enabled is True
        assert screen._result.collected_at >= first


async def test_auto_update_stops_ticking_while_paused(auto_update) -> None:
    """A paused screen must not repopulate on its own."""
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.6)
        screen = app.screen
        await pilot.press("r")
        await pilot.pause(0.2)
        assert screen.auto_update_enabled is False

        calls = _count_calls(screen)
        await pilot.pause(1.0)
        assert calls() == 0


async def test_auto_update_ticks_when_running(auto_update) -> None:
    """The live timer must actually repopulate the list, repeatedly."""
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.6)
        screen = app.screen
        calls = _count_calls(screen)
        await pilot.pause(1.0)
        assert calls() > 0
        # set_timer would fire exactly once here; the timer must repeat.
        await pilot.pause(1.0)
        assert calls() > 1


async def test_r_is_a_plain_refresh_when_auto_update_is_disabled() -> None:
    """With --refresh 0 there is no timer, so R must not claim to be live."""
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.6)
        screen = app.screen
        assert screen.auto_update_available is False

        await pilot.press("r")
        await pilot.pause(0.8)
        assert isinstance(app.screen, ProcessListScreen)
        assert len(screen._all) > 0


async def test_tab_moves_focus_between_controls() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _push_detail_for_self(app, pilot)
        await pilot.press("1")
        await pilot.pause(0.3)
        screen.query_one("#back-button", Button).focus()
        await pilot.press("tab")
        await pilot.pause()
        assert screen.focused is not None


async def test_resource_search_escape_returns_to_its_parent() -> None:
    from tests.conftest import load_fixture

    from ports_explorer.collectors.lsof import parse_lsof_output
    from ports_explorer.models import ProcessRecord

    records, _ = parse_lsof_output(load_fixture("lsof_states.txt"))
    process = ProcessRecord(pid=777, name="webserver")

    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.4)
        app.push_resource_search(records, process, None)
        await pilot.pause(0.3)
        assert isinstance(app.screen, ResourceBrowserScreen)

        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, ProcessListScreen)


async def test_resource_search_escape_clears_query_first() -> None:
    from tests.conftest import load_fixture

    from ports_explorer.collectors.lsof import parse_lsof_output
    from ports_explorer.models import ProcessRecord

    records, _ = parse_lsof_output(load_fixture("lsof_states.txt"))
    process = ProcessRecord(pid=777, name="webserver")

    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.4)
        app.push_resource_search(records, process, None)
        await pilot.pause(0.3)
        search = app.screen.query_one("#browser-search", Input)
        search.value = "LISTEN"
        await pilot.pause()

        await pilot.press("escape")
        await pilot.pause()
        assert search.value == ""
        assert isinstance(app.screen, ResourceBrowserScreen)


async def test_resource_detail_back_returns_to_search() -> None:
    from tests.conftest import load_fixture

    from ports_explorer.collectors.lsof import parse_lsof_output
    from ports_explorer.models import ProcessRecord

    records, _ = parse_lsof_output(load_fixture("lsof_states.txt"))
    process = ProcessRecord(pid=777, name="webserver")

    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.4)
        app.push_resource_detail(records[0], process, None)
        await pilot.pause(0.3)
        assert isinstance(app.screen, ResourceDetailScreen)

        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, ProcessListScreen)


async def test_q_does_not_quit_while_typing_in_an_input() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.5)
        screen = app.screen
        search = screen.query_one("#process-search", Input)
        search.focus()
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
        assert search.value == "q"
        assert isinstance(app.screen, ProcessListScreen)


async def test_question_mark_shows_keyboard_help() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.5)
        await pilot.press("question_mark")
        await pilot.pause()
        assert app._notifications


async def test_narrow_terminal_toggles_responsive_classes() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.5)
        screen = app.screen
        assert not screen.has_class("-narrow")

        await pilot.resize_terminal(70, 30)
        await pilot.pause(0.3)
        assert screen.has_class("-narrow")
        assert screen.has_class("-compact")

        await pilot.resize_terminal(120, 40)
        await pilot.pause(0.3)
        assert not screen.has_class("-narrow")
