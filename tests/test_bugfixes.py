"""Regression tests for the bugs found by the bug-hunter pass."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from textual.widgets import Static

from ports_explorer.app import PortsExplorerApp
from ports_explorer.collectors.lsof import (
    LsofCollector,
    _classify_permission,
    parse_lsof_output,
)
from ports_explorer.config import CATALOG
from ports_explorer.models import CollectionResult, PermissionState, ProcessRecord
from ports_explorer.screens.common import ARM_TIMEOUT
from ports_explorer.screens.process_detail import ProcessDetailScreen
from ports_explorer.screens.resource_browser import ResourceBrowserScreen
from ports_explorer.services.deduplication import deduplicate
from ports_explorer.services.resource_search import search_resources, tokenize

# -- BUG-001: live theme switching --------------------------------------


async def test_live_theme_switch_repaints_header_and_background() -> None:
    app = PortsExplorerApp(theme="ambrosia")
    async with app.run_test(size=(120, 40)) as pilot:
        for _ in range(10):
            await pilot.pause(0.1)
        background_before = app.screen.styles.background
        title = app.screen.query_one("#header-title", Static).render()
        color_before = title.spans[0].style
        assert CATALOG.active.name == "ambrosia"

        app.apply_theme("daylight")
        await pilot.pause(0.2)

        title = app.screen.query_one("#header-title", Static).render()
        color_after = title.spans[0].style
        assert CATALOG.active.name == "daylight"
        assert color_after != color_before, "inline text stayed on the old palette"
        assert app.screen.styles.background != background_before, "CSS colors did not change"


# -- BUG-002: deduplicated counts ---------------------------------------


def test_detail_counts_use_deduplicated_rows() -> None:
    raw, _ = parse_lsof_output(Path("tests/fixtures/lsof_duplicate_fds.txt").read_text())
    screen = ProcessDetailScreen(
        ProcessRecord(pid=4242, name="duplicate"),
        LsofCollector(),
        object(),
    )
    screen._deduped = deduplicate(raw)
    assert screen._display_counts() == (1, 0)


# -- BUG-003 / BUG-004: search ------------------------------------------


def test_fd_filter_uses_exact_numeric_match() -> None:
    records, _ = parse_lsof_output(Path("tests/fixtures/lsof_duplicate_fds.txt").read_text())
    assert search_resources(records, "fd=1") == []
    assert [r.fd for r in search_resources(records, "fd=11")] == ["11"]


def test_quoted_field_value_with_spaces_is_one_token() -> None:
    assert tokenize('path="/Users/me/My Folder/file.txt"') == ["path=/Users/me/My Folder/file.txt"]
    assert tokenize('"/Users/me/My Folder/file.txt"') == ["/Users/me/My Folder/file.txt"]


def test_quoted_path_filter_finds_the_resource() -> None:
    records, _ = parse_lsof_output(Path("tests/fixtures/lsof_mixed.txt").read_text())
    target = next(r for r in records if "data with spaces/file.txt" in r.name)
    found = search_resources(records, f'path="{target.name}"')
    assert any(r.name == target.name for r in found)


# -- BUG-005: kill arm timer --------------------------------------------


async def test_arming_a_kill_schedules_a_disarm_timer() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        for _ in range(15):
            await pilot.pause(0.1)
        screen = app.screen
        screen._kill_timer = None
        screen._arm_kill(4242, "term", "test")
        assert screen._kill_timer is not None
        assert screen._kill_deadline > time.monotonic()
        assert screen._kill_deadline - time.monotonic() <= ARM_TIMEOUT
        screen._disarm_kill()
        assert screen._kill_timer is None


# -- BUG-006: cancellation cleanup --------------------------------------


async def test_cancelled_collection_resets_loading_flag() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        for _ in range(10):
            await pilot.pause(0.1)
        screen = app.screen

        async def never_finishes() -> None:
            await asyncio.sleep(30)

        screen.collector.snapshot = never_finishes  # type: ignore[method-assign]
        worker = screen.collect_processes()
        await pilot.pause(0.2)
        assert screen._loading is True
        worker.cancel()
        await pilot.pause(0.2)
        assert screen._loading is False


# -- BUG-007: resource browser is reachable -----------------------------


async def test_s_key_opens_resource_browser() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        for _ in range(15):
            await pilot.pause(0.1)
        process = app.screen._all[0]
        app.push_process_detail(process)
        await pilot.pause(0.4)
        screen = app.screen
        assert isinstance(screen, ProcessDetailScreen)
        screen._result = CollectionResult(records=[], collected_at=time.time())
        await pilot.press("s")
        await pilot.pause(0.3)
        assert isinstance(app.screen, ResourceBrowserScreen)


# -- BUG-008: permission classification ---------------------------------


def test_empty_inventory_is_not_permission_denied() -> None:
    assert _classify_permission(0, "", [], process_exists=True) is PermissionState.OK


def test_exit_one_empty_inventory_is_still_denied() -> None:
    assert _classify_permission(1, "", [], process_exists=True) is PermissionState.DENIED


def test_gone_process_is_gone() -> None:
    assert _classify_permission(1, "", [], process_exists=False) is PermissionState.GONE
