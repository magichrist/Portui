"""Regression tests for the second-pass audit (NEW-001..016)."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest
from textual.widgets import Input

from ports_explorer import config
from ports_explorer.__main__ import build_parser, main
from ports_explorer.app import PortsExplorerApp
from ports_explorer.collectors.lsof import _classify_permission, parse_lsof_output
from ports_explorer.collectors.processes import ProcessCollector
from ports_explorer.models import (
    CollectionResult,
    Endpoint,
    PermissionState,
    ProcessRecord,
    ResourceRecord,
)
from ports_explorer.screens.process_detail import ProcessDetailScreen
from ports_explorer.screens.process_list import ProcessListScreen
from ports_explorer.services.deduplication import deduplicate, filter_listeners
from ports_explorer.services.resource_search import (
    ResourceFilter,
    filter_resources,
    search_resources,
    tokenize,
)
from ports_explorer.widgets.resource_table import ResourceTable


@pytest.fixture(autouse=True)
def _restore_globals():
    settings = config.get_settings()
    snapshot = (
        settings.debug,
        settings.log_file,
        settings.refresh_interval,
        settings.lsof_timeout,
        config.CATALOG.active.name,
    )
    try:
        yield
    finally:
        config.build_settings(
            debug=snapshot[0],
            log_file=snapshot[1],
            refresh_interval=snapshot[2],
            lsof_timeout=snapshot[3],
            theme=snapshot[4],
        )


def _resource(**overrides: object) -> ResourceRecord:
    values: dict[str, object] = {
        "command": "app",
        "pid": 2,
        "user": "u",
        "fd": "10",
        "resource_type": "IPv4",
        "protocol": "TCP",
        "name": "127.0.0.1:8080",
        "state": "LISTEN",
        "raw_name": "127.0.0.1:8080",
        "local": Endpoint("127.0.0.1", 8080),
    }
    values.update(overrides)
    return ResourceRecord(**values)  # type: ignore[arg-type]


# -- services -------------------------------------------------------------


def test_zero_port_filter_is_active_and_honored() -> None:
    criteria = ResourceFilter(local_port=0)
    assert criteria.is_active() is True
    assert "lport=0" in criteria.describe()
    assert filter_resources([_resource()], criteria) == []


def test_quoted_phrase_containing_equals_stays_literal() -> None:
    tokens = tokenize('"foo=bar/baz"')
    assert tokens == ["foo=bar/baz"]
    assert getattr(tokens[0], "literal", False) is True

    exact = _resource(name="/x/foo=bar/baz/file", raw_name="/x/foo=bar/baz/file")
    loose = _resource(name="/x/bar/baz/file", raw_name="/x/bar/baz/file")
    assert search_resources([exact], '"foo=bar/baz"') == [exact]
    assert search_resources([loose], '"foo=bar/baz"') == []


def test_collapsed_fd_alias_is_searchable_as_free_text() -> None:
    records, _ = parse_lsof_output(
        (Path(__file__).parent / "fixtures" / "lsof_duplicate_fds.txt").read_text()
    )
    deduped = deduplicate(records)
    assert deduped[0].aliases == ("10", "11", "12")
    assert deduped[0] in search_resources(deduped, "11")
    assert deduped[0] in search_resources(deduped, "fd=11")


# -- data layer -----------------------------------------------------------


def test_process_first_seen_after_prime_reports_unavailable_cpu() -> None:
    collector = ProcessCollector()
    collector.prime()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        record = next(r for r in collector.snapshot_blocking() if r.pid == child.pid)
        assert record.cpu_percent is None
        second = next(r for r in collector.snapshot_blocking() if r.pid == child.pid)
        assert isinstance(second.cpu_percent, float)
    finally:
        child.terminate()
        child.wait()


async def test_fatal_lsof_exit_with_rows_is_not_reported_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _resource()
    state = _classify_permission(2, "", [record], True)
    assert state is PermissionState.FAILED

    # A fatal exit must also produce a visible warning, even when lsof emitted
    # rows before it failed.
    from ports_explorer.collectors import lsof as lsof_module

    class FakeProcess:
        returncode = 2

        async def communicate(self):
            return b"p2\ncx\nf0\ntIPv4\nPTCP\nn*:80\nTST=LISTEN\n", b""

        def kill(self):
            return None

        async def wait(self):
            return 2

    async def fake_exec(*args, **kwargs):
        return FakeProcess()

    monkeypatch.setattr(lsof_module.LsofCollector, "available", lambda self: True)
    monkeypatch.setattr(lsof_module.asyncio, "create_subprocess_exec", fake_exec)
    result = await lsof_module.LsofCollector().collect(pid=2)
    assert result.records
    assert result.permission is PermissionState.FAILED
    assert result.error is not None
    assert result.warning() is not None


def test_gone_process_wins_over_denial_stderr() -> None:
    stderr = "lsof: status error on /dev/ttys001: Permission denied"
    assert _classify_permission(0, stderr, [], False) is PermissionState.GONE


def test_udp_listen_is_not_a_tcp_listener() -> None:
    udp = _resource(protocol="UDP", state="LISTEN")
    assert udp.is_inet is True
    assert udp.is_listening is False
    assert filter_listeners([udp]) == []


# -- CLI / config ---------------------------------------------------------


def test_no_color_does_not_set_invalid_textual_color_system(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(PortsExplorerApp, "run", lambda self: None)
    saved_no_color = os.environ.pop("NO_COLOR", None)
    saved_color_system = os.environ.pop("TEXTUAL_COLOR_SYSTEM", None)
    try:
        assert main(["--no-color"]) == 0
        assert os.environ.get("NO_COLOR") == "1"
        assert "TEXTUAL_COLOR_SYSTEM" not in os.environ
    finally:
        if saved_no_color is None:
            os.environ.pop("NO_COLOR", None)
        else:
            os.environ["NO_COLOR"] = saved_no_color
        if saved_color_system is not None:
            os.environ["TEXTUAL_COLOR_SYSTEM"] = saved_color_system


def test_nonpositive_timeout_is_rejected() -> None:
    for value in ("0", "-1"):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["--timeout", value])


def test_build_settings_clamps_nonpositive_timeout() -> None:
    assert config.build_settings(lsof_timeout=-1).lsof_timeout == 0.001


def test_set_theme_updates_settings_too() -> None:
    assert config.set_theme("tokyo") is not None
    assert config.get_settings().theme == "tokyo"
    assert config.CATALOG.active.name == "tokyo"


def test_log_file_enables_diagnostic_level(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "debug.log"
    root = logging.getLogger()
    package = logging.getLogger("ports_explorer")
    before = (root.level, package.level, list(root.handlers), list(package.handlers))
    try:
        config.configure_logging(debug=False, log_file=path)
        package.info("INFO-MESSAGE")
        package.warning("WARN-MESSAGE")
        for handler in logging.getLogger().handlers:
            handler.flush()
        text = path.read_text()
        assert "INFO-MESSAGE" in text
        assert "WARN-MESSAGE" in text
    finally:
        root.setLevel(before[0])
        package.setLevel(before[1])
        root.handlers[:] = before[2]
        package.handlers[:] = before[3]


# -- TUI ------------------------------------------------------------------


async def _open_detail(app: PortsExplorerApp, pilot) -> ProcessDetailScreen:
    for _ in range(8):
        await pilot.pause(0.05)
    process = app.screen._all[0]
    app.push_process_detail(process)
    await pilot.pause(0.3)
    return app.screen


async def test_detail_status_ignores_counts_from_hidden_tables() -> None:
    config.build_settings(refresh_interval=0.0)
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_detail(app, pilot)
        screen._result = CollectionResult(records=[])
        screen._update_status()
        before = screen.query_one("#status-bar").counts

        hidden = screen.query_one("#table-all", ResourceTable)
        hidden.post_message(ResourceTable.RecordCountChanged(0, 41))
        await pilot.pause(0.1)
        assert screen.query_one("#status-bar").counts == before


async def test_kill_hint_survives_a_status_refresh() -> None:
    config.build_settings(refresh_interval=0.0)
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_detail(app, pilot)
        screen._arm_kill(screen.process.pid, "term", "probe")
        await pilot.pause(0.05)
        keys = screen.query_one("#status-bar")._keys
        assert "armed" in keys
        screen._apply_status()
        await pilot.pause(0.05)
        assert "armed" in screen.query_one("#status-bar")._keys
        screen._disarm_kill()


async def test_backspace_on_root_screen_is_a_noop() -> None:
    config.build_settings(refresh_interval=0.0)
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        for _ in range(8):
            await pilot.pause(0.05)
        assert isinstance(app.screen, ProcessListScreen)
        await pilot.press("backspace")
        await pilot.pause(0.1)
        assert isinstance(app.screen, ProcessListScreen)
        assert len(app.screen_stack) == 2


async def test_kill_worker_completes_after_navigating_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple] = []
    original = ProcessDetailScreen.notify

    def counting(self, *args, **kwargs):
        calls.append(args)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ProcessDetailScreen, "notify", counting)
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)",
        ]
    )
    config.build_settings(refresh_interval=0.0)
    app = PortsExplorerApp()
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            for _ in range(8):
                await pilot.pause(0.05)
            app.push_process_detail(ProcessRecord(pid=child.pid, name="stubborn"))
            await pilot.pause(0.3)
            await pilot.press("k")
            await pilot.press("k")
            await pilot.press("escape")
            await pilot.pause(2.5)
            assert isinstance(app.screen, ProcessListScreen)
            # Arm notice plus the result notice: the app-owned worker survived
            # the screen change and delivered its outcome.
            assert len(calls) >= 2
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


async def test_ctrl_c_quits_like_ctrl_q() -> None:
    config.build_settings(refresh_interval=0.0)
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        for _ in range(6):
            await pilot.pause(0.05)
        await pilot.press("ctrl+c")
        await pilot.pause(0.2)
        assert app.is_running is False


async def test_ctrl_c_quits_even_with_search_focused() -> None:
    config.build_settings(refresh_interval=0.0)
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        for _ in range(6):
            await pilot.pause(0.05)
        app.screen.query_one("#process-search", Input).focus()
        await pilot.press("ctrl+c")
        await pilot.pause(0.2)
        assert app.is_running is False
