"""Arrow-key tab cycling and Enter-to-open behaviour on the detail screen."""

from __future__ import annotations

import os

import pytest
from textual.widgets import DataTable, Input, TabbedContent

from ports_explorer.app import PortsExplorerApp
from ports_explorer.screens.process_detail import ProcessDetailScreen
from ports_explorer.screens.process_list import ProcessListScreen
from ports_explorer.screens.resource_browser import ResourceDetailScreen

TAB_ORDER = ProcessDetailScreen.TAB_ORDER

pytestmark = pytest.mark.asyncio


async def _open_self_detail(app, pilot) -> ProcessDetailScreen:
    process = await app.process_collector.process_detail(os.getpid())
    app.push_process_detail(process)
    await pilot.pause(1.2)
    assert isinstance(app.screen, ProcessDetailScreen)
    # Wait for every tab pane's table to actually be mounted. A fixed pause is
    # not enough under load: the detail screen now re-runs lsof on its timer,
    # so the panes can still be composing when a fixed delay expires.
    for _ in range(40):
        mounted = all(
            len(app.screen.query(table_id)) == 1
            for table_id in (
                "#table-network",
                "#table-listeners",
                "#table-files",
                "#table-unix",
                "#table-all",
            )
        )
        if mounted:
            break
        await pilot.pause(0.1)
    else:  # pragma: no cover - only on a badly overloaded machine
        raise AssertionError("detail tab panes never mounted")
    return app.screen


async def test_right_arrow_moves_to_the_next_tab() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_self_detail(app, pilot)
        tabs = screen.query_one("#detail-tabs", TabbedContent)
        assert tabs.active == "tab-overview"

        await pilot.press("right")
        await pilot.pause(0.2)
        assert tabs.active == "tab-network"

        await pilot.press("right")
        await pilot.pause(0.2)
        assert tabs.active == "tab-listeners"


async def test_left_arrow_moves_to_the_previous_tab() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_self_detail(app, pilot)
        tabs = screen.query_one("#detail-tabs", TabbedContent)

        await pilot.press("left")
        await pilot.pause(0.2)
        assert tabs.active == TAB_ORDER[-1]

        await pilot.press("left")
        await pilot.pause(0.2)
        assert tabs.active == TAB_ORDER[-2]


async def test_right_arrow_wraps_from_last_to_first() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_self_detail(app, pilot)
        tabs = screen.query_one("#detail-tabs", TabbedContent)

        for _ in TAB_ORDER:
            await pilot.press("right")
            await pilot.pause(0.15)
        assert tabs.active == "tab-overview"


async def test_left_arrow_wraps_from_first_to_last() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_self_detail(app, pilot)
        tabs = screen.query_one("#detail-tabs", TabbedContent)

        await pilot.press("left")
        await pilot.pause(0.2)
        assert tabs.active == TAB_ORDER[-1]


async def test_right_arrow_reaches_every_tab_in_order() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_self_detail(app, pilot)
        tabs = screen.query_one("#detail-tabs", TabbedContent)

        seen = [tabs.active]
        for _ in range(len(TAB_ORDER) - 1):
            await pilot.press("right")
            await pilot.pause(0.15)
            seen.append(tabs.active)

        assert seen == list(TAB_ORDER)


async def test_right_arrow_does_not_open_resource_detail() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_self_detail(app, pilot)
        await pilot.press("2")
        await pilot.pause(0.3)
        screen.query_one("#table-network").focus_table()
        await pilot.pause()

        await pilot.press("right")
        await pilot.pause(0.3)
        assert isinstance(app.screen, ProcessDetailScreen)
        assert app.screen.query_one("#detail-tabs", TabbedContent).active == "tab-listeners"


async def test_enter_opens_the_selected_resource() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_self_detail(app, pilot)
        await pilot.press("2")
        await pilot.pause(0.3)
        table = screen.query_one("#table-network")
        table.focus_table()
        await pilot.pause()

        # Whether the test process itself holds an IPv4/IPv6 socket depends on
        # what the machine's test runner is doing, so there may be nothing to
        # select. Skip honestly rather than asserting on an empty table.
        if not table._filtered:
            pytest.skip("the test process holds no IPv4/IPv6 sockets to open")

        await pilot.press("enter")
        await pilot.pause(0.4)
        assert isinstance(app.screen, ResourceDetailScreen)


async def test_enter_opens_a_resource_from_the_files_tab() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_self_detail(app, pilot)
        await pilot.press("4")
        await pilot.pause(0.3)
        table = screen.query_one("#table-files")
        table.focus_table()
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause(0.4)
        assert isinstance(app.screen, ResourceDetailScreen)


async def test_left_arrow_inside_an_input_is_not_hijacked() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_self_detail(app, pilot)
        await pilot.press("2")
        await pilot.pause(0.3)
        await pilot.press("slash")
        await pilot.pause()
        assert isinstance(screen.focused, Input)

        await pilot.press("left")
        await pilot.pause()
        assert isinstance(screen.focused, Input)
        assert screen.query_one("#detail-tabs", TabbedContent).active == "tab-network"


async def test_number_keys_still_jump_directly_to_a_tab() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_self_detail(app, pilot)
        tabs = screen.query_one("#detail-tabs", TabbedContent)

        await pilot.press("5")
        await pilot.pause(0.2)
        assert tabs.active == "tab-unix"

        await pilot.press("1")
        await pilot.pause(0.2)
        assert tabs.active == "tab-overview"


async def test_escape_still_returns_to_the_process_list() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await _open_self_detail(app, pilot)
        await pilot.press("right")
        await pilot.pause(0.2)
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, ProcessListScreen)


async def test_tab_order_covers_all_six_categories() -> None:
    assert TAB_ORDER == (
        "tab-overview",
        "tab-network",
        "tab-listeners",
        "tab-files",
        "tab-unix",
        "tab-all",
    )


async def test_cycle_tab_action_wraps() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_self_detail(app, pilot)
        tabs = screen.query_one("#detail-tabs", TabbedContent)

        screen.action_cycle_tab(-1)
        await pilot.pause(0.2)
        assert tabs.active == TAB_ORDER[-1]

        screen.action_cycle_tab(1)
        await pilot.pause(0.2)
        assert tabs.active == "tab-overview"


async def test_right_arrow_on_the_process_list_is_not_hijacked() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.5)
        screen = app.screen
        assert isinstance(screen, ProcessListScreen)
        screen.query_one("#process-grid", DataTable).focus()
        await pilot.pause()

        await pilot.press("right")
        await pilot.pause(0.2)
        assert isinstance(app.screen, ProcessListScreen)


async def test_overview_does_not_bounce_to_the_network_tab() -> None:
    """Regression: landing on Overview must stay on Overview.

    The tab-change handler used to autofocus ``_active_table()``, which fell
    back to ``#table-network`` because the Overview pane has no table. Focusing a
    widget in another tab's pane makes Textual activate that pane, so every trip
    to Overview silently became a trip to Network.
    """
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_self_detail(app, pilot)
        tabs = screen.query_one("#detail-tabs", TabbedContent)

        screen.action_show_tab("tab-files")
        await pilot.pause(0.3)
        assert tabs.active == "tab-files"

        screen.action_show_tab("tab-overview")
        await pilot.pause(0.5)
        assert tabs.active == "tab-overview"


async def test_active_table_is_none_on_the_overview_tab() -> None:
    """Overview has no table; returning one is what caused the tab bounce."""
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_self_detail(app, pilot)
        tabs = screen.query_one("#detail-tabs", TabbedContent)

        tabs.active = "tab-overview"
        await pilot.pause(0.3)
        assert screen._active_table() is None

        tabs.active = "tab-unix"
        await pilot.pause(0.3)
        assert screen._active_table() is not None
        assert screen._active_table().id == "table-unix"
