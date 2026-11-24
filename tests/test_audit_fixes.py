"""Regression tests for the second audit (BUG-009 … BUG-016)."""

from __future__ import annotations

import asyncio
import socket
from types import SimpleNamespace

import pytest
from textual.widgets import DataTable, TabbedContent

from ports_explorer import config
from ports_explorer.__main__ import build_parser
from ports_explorer.app import PortsExplorerApp
from ports_explorer.models import Endpoint, ProcessRecord, ResourceRecord
from ports_explorer.screens.process_detail import ProcessDetailScreen
from ports_explorer.services import search_tags
from ports_explorer.services.resource_search import search_processes, search_resources
from ports_explorer.services.search_tags import split_process_tags, split_tags


def _resource(**overrides: object) -> ResourceRecord:
    values: dict[str, object] = {
        "command": "nginx",
        "pid": 4242,
        "user": "root",
        "fd": "9",
        "resource_type": "IPv4",
        "protocol": "TCP",
        "name": "127.0.0.1:8080",
        "state": "LISTEN",
        "local": Endpoint("127.0.0.1", 8080),
        "raw_name": "127.0.0.1:8080",
    }
    values.update(overrides)
    return ResourceRecord(**values)  # type: ignore[arg-type]


# BUG-009 — documented key:value field syntax


def test_colon_separator_works_for_resource_fields() -> None:
    record = _resource()
    for query in ("port=8080", "port:8080", "lport:8080", "state:LISTEN", "proto:TCP"):
        assert search_resources([record], query) == [record], query
    assert search_resources([record], "port:8081") == []


def test_colon_separator_with_quoted_value() -> None:
    record = _resource(name="/tmp/a  b", raw_name="/tmp/a  b")
    assert search_resources([record], 'path:"/tmp/a  b"') == [record]


def test_non_field_colon_tokens_stay_literal() -> None:
    record = _resource(name="http://example.com/x", raw_name="http://example.com/x")
    assert search_resources([record], "http://example.com") == [record]


# BUG-010 — process-list field filters


def test_process_list_field_filters() -> None:
    process = ProcessRecord(
        pid=4242, name="nginx", user="root", exe="/usr/sbin/nginx", status="sleeping"
    )
    for query in (
        "pid=4242",
        "pid:4242",
        "user=root",
        "name=nginx",
        "exe=/usr/sbin/nginx",
        "status=sleeping",
    ):
        assert search_processes([process], query) == [process], query
    assert search_processes([process], "pid=4243") == []
    assert search_processes([process], 'name="nginx"') == [process]


# BUG-011 — partial psutil results must not suppress the lsof fallback


async def test_partial_psutil_pids_still_use_lsof_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = SimpleNamespace(
        pid=1,
        type=socket.SOCK_STREAM,
        status="ESTABLISHED",
        laddr=SimpleNamespace(port=5000),
    )
    monkeypatch.setattr(search_tags.psutil, "net_connections", lambda kind="inet": [conn])

    async def fake_lsof() -> dict[str, set[int]]:
        return {"port:open": {99}, "connection:alive": {99}}

    monkeypatch.setattr(search_tags, "_lsof_pid_sets", fake_lsof)
    pids = await search_tags.network_pid_sets()
    assert 1 in pids["port:open"]
    assert 99 in pids["port:open"], "other users' sockets were dropped"
    assert 99 in pids["connection:alive"]


# BUG-012 — theme switch repaints visible table cells


async def test_theme_switch_repaints_process_list_and_browser() -> None:
    app = PortsExplorerApp(theme="ambrosia")
    async with app.run_test(size=(140, 45)) as pilot:
        for _ in range(10):
            await pilot.pause(0.05)
        table = app.screen.query_one("#process-grid", DataTable)
        before = table.get_row_at(0)[0].style
        app.apply_theme("daylight")
        await pilot.pause(0.2)
        after = app.screen.query_one("#process-grid", DataTable).get_row_at(0)[0].style
        assert after != before, "process-list cells kept the old palette"

        app.push_resource_search(
            [_resource()], ProcessRecord(pid=4242, name="nginx"), app.screen
        )
        await pilot.pause(0.2)
        browser = app.screen.query_one("#browser-grid", DataTable)
        before_browser = browser.get_row_at(0)[1].style
        app.apply_theme("gummy")
        await pilot.pause(0.2)
        after_browser = app.screen.query_one("#browser-grid", DataTable).get_row_at(0)[1].style
        assert after_browser != before_browser, "resource-browser cells kept the old palette"


# BUG-013 — Escape fallback must be cancelled by other keys


async def test_other_key_cancels_pending_escape_fallback() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.5)
        app.push_process_detail(ProcessRecord(pid=4242, name="demo"))
        await pilot.pause(0.2)
        await pilot.press("escape")
        await pilot.press("2")
        await pilot.pause(0.3)
        assert isinstance(app.screen, ProcessDetailScreen)
        assert app.screen.query_one("#detail-tabs", TabbedContent).active == "tab-network"


# BUG-014 — lsof fallback timeout and cleanup


async def test_lsof_fallback_respects_timeout_and_kills_child(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeProcess:
        returncode: int | None = None
        killed = False

        async def communicate(self) -> tuple[bytes, bytes]:
            await asyncio.sleep(10)
            return b"", b""

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

        async def wait(self) -> int:
            return -9

    child = FakeProcess()

    async def fake_exec(*args: object, **kwargs: object) -> FakeProcess:
        return child

    monkeypatch.setattr(search_tags.asyncio, "create_subprocess_exec", fake_exec)
    settings = config.get_settings()
    snapshot = (
        settings.debug,
        settings.log_file,
        settings.refresh_interval,
        settings.lsof_timeout,
        settings.theme,
    )
    config.build_settings(lsof_timeout=0.05)
    try:
        assert await search_tags._lsof_pid_sets() == search_tags._empty_pid_sets()
        assert child.killed, "the timed-out lsof child was left running"
    finally:
        config.build_settings(
            debug=snapshot[0],
            log_file=snapshot[1],
            refresh_interval=snapshot[2],
            lsof_timeout=snapshot[3],
            theme=snapshot[4],
        )


# BUG-015 — quoted whitespace survives tag splitting


def test_split_tags_preserves_whitespace_inside_quotes() -> None:
    query, predicates = split_tags('path="/tmp/a  b" port:open')
    assert query == 'path="/tmp/a  b"'
    assert len(predicates) == 1


def test_split_process_tags_preserves_whitespace_inside_quotes() -> None:
    query, predicates = split_process_tags('name="a  b" p:o', pids={"port:open": {1}})
    assert query == 'name="a  b"'
    assert len(predicates) == 1


# BUG-016 — pause/resume key in help and empty-state text


def test_cli_help_names_space_as_the_pause_key() -> None:
    help_text = build_parser().format_help()
    assert "Press Space in the app to pause or resume it" in help_text
    assert "Press R in the app to pause" not in help_text
