"""Smoke tests that drive the real Textual app headlessly.

These verify that screens compose, that a real process snapshot populates the
table, and that navigation actually changes screens rather than being a stub.
"""

from __future__ import annotations

import os
import re

import pytest
from textual.app import App, ComposeResult
from textual.widgets import DataTable, Input

from ports_explorer.app import PortsExplorerApp
from ports_explorer.collectors.lsof import LsofCollector, parse_lsof_output
from ports_explorer.models import ResourceCategory
from ports_explorer.screens.process_detail import MetricGrid, ProcessDetailScreen
from ports_explorer.screens.process_list import ProcessListScreen
from ports_explorer.screens.resource_browser import (
    ResourceBrowserScreen,
    ResourceDetailScreen,
)
from ports_explorer.services.deduplication import deduplicate
from ports_explorer.widgets.resource_table import ResourceTable

pytestmark = pytest.mark.asyncio

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def widget_text(widget, selector: str) -> str:
    from rich.text import Text

    node = widget.query_one(selector)
    renderable = node.render()
    return renderable.plain if isinstance(renderable, Text) else str(renderable)


def visible(app: App) -> str:
    lines = []
    compositor = getattr(app.screen, "_compositor", None)
    strips = compositor.render_strips() if compositor is not None else []
    for strip in strips or []:
        lines.append("".join(seg.text for seg in strip).rstrip())
    return ANSI.sub("", "\n".join(lines))


@pytest.fixture
def records():
    from tests.conftest import load_fixture

    parsed, _ = parse_lsof_output(load_fixture("lsof_states.txt"))
    return parsed


async def test_app_starts_and_shows_processes() -> None:
    app = PortsExplorerApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause(0.4)
        text = visible(app)
        assert "PORTS EXPLORER" in text
        assert "PROCESS" in text
        assert "PID" in text


async def test_process_table_is_populated_from_the_real_system() -> None:
    app = PortsExplorerApp()
    async with app.run_test() as pilot:
        await pilot.pause(0.4)
        screen = app.screen
        table = screen.query_one("#process-grid", DataTable)
        assert table.row_count > 10, "expected a real, populated process list"


async def test_selecting_a_process_pushes_the_real_detail_screen() -> None:
    app = PortsExplorerApp()
    async with app.run_test() as pilot:
        await pilot.pause(0.4)
        list_screen = app.screen
        assert isinstance(list_screen, ProcessListScreen)

        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause(0.6)

        assert isinstance(app.screen, ProcessDetailScreen)
        assert "Overview" in visible(app)


async def test_detail_screen_shows_real_overview_metrics() -> None:
    app = PortsExplorerApp()
    async with app.run_test() as pilot:
        await pilot.pause(0.4)
        await pilot.press("enter")
        await pilot.pause(0.8)

        screen = app.screen
        assert isinstance(screen, ProcessDetailScreen)
        assert screen.process.pid > 0
        assert screen.process.name
        grid = screen.query_one("#metric-grid", MetricGrid)
        overview = grid.metric_text()

        assert "PID" in overview
        assert "RUNTIME" in overview
        assert "UNIQUE NETWORK" in overview
        assert "LISTENING" in overview
        assert str(screen.process.pid) in overview
        assert widget_text(screen, "#command-line")

        tiles = list(grid.children)
        assert len(tiles) == 12
        assert all(tile.render() is not None for tile in tiles)


async def test_detail_screen_collects_resources_without_raising() -> None:
    app = PortsExplorerApp()
    async with app.run_test() as pilot:
        await pilot.pause(0.4)
        await pilot.press("enter")
        await pilot.pause(1.2)

        screen = app.screen
        assert isinstance(screen, ProcessDetailScreen)
        assert screen._result is not None, "collection never completed"

        assert screen._result.network_count + screen._result.file_count == len(
            screen._result.records
        )


async def test_backspace_returns_to_the_process_list() -> None:
    app = PortsExplorerApp()
    async with app.run_test() as pilot:
        await pilot.pause(0.4)
        await pilot.press("enter")
        await pilot.pause(0.6)
        assert isinstance(app.screen, ProcessDetailScreen)

        await pilot.press("backspace")
        await pilot.pause()
        assert isinstance(app.screen, ProcessListScreen)


async def test_escape_returns_to_the_process_list() -> None:
    app = PortsExplorerApp()
    async with app.run_test() as pilot:
        await pilot.pause(0.4)
        await pilot.press("enter")
        await pilot.pause(0.6)

        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, ProcessListScreen)


async def test_search_filters_the_process_list() -> None:
    app = PortsExplorerApp()
    async with app.run_test() as pilot:
        await pilot.pause(0.4)
        screen = app.screen
        assert isinstance(screen, ProcessListScreen)
        before = screen.query_one("#process-grid", DataTable).row_count
        assert before > 10

        search = screen.query_one("#process-search", Input)
        search.value = "zzzz-no-such-process"
        await pilot.pause()
        assert screen.query_one("#process-grid", DataTable).row_count == 0

        search.value = ""
        await pilot.pause()
        assert screen.query_one("#process-grid", DataTable).row_count == before


async def test_resource_search_screen_filters_and_reports_counts(records) -> None:
    from ports_explorer.models import ProcessRecord

    process = ProcessRecord(pid=777, name="webserver")
    app = PortsExplorerApp()
    async with app.run_test() as pilot:
        app.push_resource_search(records, process, None)
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, ResourceBrowserScreen)
        assert screen.query_one("#browser-grid", DataTable).row_count == len(records)

        screen.query_one("#browser-search", Input).value = "state=LISTEN"
        await pilot.pause()
        assert screen.query_one("#browser-grid", DataTable).row_count == 3
        assert "3 of 9" in visible(app)


async def test_resource_detail_shows_the_raw_lsof_value(records) -> None:
    from ports_explorer.models import ProcessRecord

    process = ProcessRecord(pid=777, name="webserver")
    record = records[0]
    app = PortsExplorerApp()
    async with app.run_test() as pilot:
        app.push_resource_detail(record, process, None)
        await pilot.pause()
        assert isinstance(app.screen, ResourceDetailScreen)
        text = visible(app)
        assert "RAW lsof NAME" in text
        assert record.raw_name in text


async def test_resource_table_renders_and_filters(records) -> None:
    class TableHost(App[None]):
        def compose(self) -> ComposeResult:
            yield ResourceTable(category=ResourceCategory.NETWORK)

    app = TableHost()
    async with app.run_test() as pilot:
        table = app.screen.query_one(ResourceTable)
        table.set_records(deduplicate(records))
        await pilot.pause()
        assert table.query_one("#resource-grid", DataTable).row_count == len(deduplicate(records))

        table.toggle_search()
        await pilot.pause()
        table.query_one("#resource-filter", Input).value = "LISTEN"
        await pilot.pause()
        assert table.query_one("#resource-grid", DataTable).row_count == 3


async def test_quit_key_exits_cleanly() -> None:
    app = PortsExplorerApp()
    async with app.run_test() as pilot:
        await pilot.pause(0.3)
        await pilot.press("q")
        await pilot.pause()


async def test_real_end_to_end_collection_of_this_process() -> None:
    """lsof against our own PID, through the real collector."""
    if not LsofCollector().available():
        pytest.skip("lsof unavailable")

    collector = LsofCollector()
    result = await collector.collect(os.getpid())
    assert result.records
    assert all(record.pid == os.getpid() for record in result.records)
    assert any(record.resource_type == "REG" for record in result.records)
    assert result.duration_seconds >= 0


async def test_missing_lsof_binary_is_surfaced_in_the_ui() -> None:
    app = PortsExplorerApp()
    app.lsof_collector = LsofCollector(binary="not-a-real-binary-xyz")
    async with app.run_test() as pilot:
        await pilot.pause(0.4)
        await pilot.press("enter")
        await pilot.pause(0.6)

        screen = app.screen
        assert isinstance(screen, ProcessDetailScreen)
        assert screen._result is not None
        assert screen._result.error is not None
        assert "not found on PATH" in screen._result.error
