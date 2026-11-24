"""Tests for semantic resource-search tags and their abbreviations."""

from __future__ import annotations

import socket
from dataclasses import replace
from types import SimpleNamespace

import psutil
import pytest
from textual.widgets import DataTable, Input

from ports_explorer.app import PortsExplorerApp
from ports_explorer.collectors.lsof import parse_lsof_output
from ports_explorer.models import ProcessRecord
from ports_explorer.screens.resource_browser import ResourceBrowserScreen
from ports_explorer.services.search_tags import (
    TAGS,
    _empty_pid_sets,
    _lsof_local_port,
    apply_tags,
    network_pid_sets,
    resolve_tag,
    split_process_tags,
    split_tags,
)


@pytest.fixture
def records(states_output: str):
    parsed, _ = parse_lsof_output(states_output)
    return parsed


@pytest.mark.parametrize(
    ("key", "value", "label"),
    [
        ("port", "open", "port:open"),
        ("p", "o", "port:open"),
        ("por", "op", "port:open"),
        ("po", "open", "port:open"),
        ("P", "O", "port:open"),
        ("connection", "alive", "connection:alive"),
        ("c", "a", "connection:alive"),
        ("con", "ali", "connection:alive"),
        ("co", "alive", "connection:alive"),
        ("connection", "waiting", "connection:waiting"),
        ("c", "w", "connection:waiting"),
        ("conn", "wai", "connection:waiting"),
        ("connection", "closing", "connection:closing"),
        ("c", "cl", "connection:closing"),
        ("co", "closing", "connection:closing"),
        ("connection", "new", "connection:new"),
        ("c", "n", "connection:new"),
        ("conn", "ne", "connection:new"),
    ],
)
def test_prefix_spellings_resolve(key: str, value: str, label: str) -> None:
    tag = resolve_tag(key, value)
    assert tag is not None
    assert tag.label == label


def test_unknown_or_ambiguous_pair_does_not_resolve() -> None:
    assert resolve_tag("p", "a") is None  # port/open vs connection/alive
    assert resolve_tag("z", "open") is None
    assert resolve_tag("port", "") is None


def test_all_tags_have_unique_labels() -> None:
    assert len({tag.label for tag in TAGS}) == len(TAGS)
    assert {tag.label for tag in TAGS} == {
        "port:open",
        "connection:alive",
        "connection:waiting",
        "connection:closing",
        "connection:new",
    }


def test_connection_state_tags_select_the_right_states(records) -> None:
    base = records[0]

    def variant(state: str):
        return replace(base, state=state, name=f"record-{state}")

    groups = {
        "LISTEN": [variant("LISTEN")],
        "ESTABLISHED": [variant("ESTABLISHED")],
        "waiting": [variant("CLOSE_WAIT"), variant("TIME_WAIT")],
        "closing": [
            variant("FIN_WAIT_1"),
            variant("FIN_WAIT_2"),
            variant("CLOSING"),
            variant("LAST_ACK"),
        ],
        "new": [variant("SYN_SENT"), variant("SYN_RECV")],
    }
    everything = [record for group in groups.values() for record in group]

    # Every variant inherits the base record's local port, so port:open now
    # matches all of them regardless of TCP state.
    assert apply_tags(everything, "port:open") == everything
    assert apply_tags(everything, "c:a") == groups["ESTABLISHED"]
    assert apply_tags(everything, "c:w") == groups["waiting"]
    assert apply_tags(everything, "c:cl") == groups["closing"]
    assert apply_tags(everything, "c:n") == groups["new"]


def test_split_tags_removes_only_recognized_tags() -> None:
    query, predicates = split_tags("8080 po:open c:a")
    assert query == "8080"
    assert len(predicates) == 2


def test_split_tags_keeps_unknown_field_tokens() -> None:
    query, predicates = split_tags("state:LISTEN zzz:open")
    assert predicates == []
    assert query == "state:LISTEN zzz:open"


def test_split_tags_accepts_the_equal_separator() -> None:
    query, predicates = split_tags("port=open LISTEN")
    assert query == "LISTEN"
    assert len(predicates) == 1


def test_split_tags_ignores_quoted_phrases() -> None:
    query, predicates = split_tags('"port:open"')
    assert predicates == []
    # The quotes stay so the ordinary tokenizer can still strip them later.
    assert query == '"port:open"'


def test_port_open_selects_every_bound_local_port(records) -> None:
    found = apply_tags(records, "port:open")
    assert found
    assert all(record.local.port is not None for record in found)
    # Client-only sockets have no LISTEN descriptor but do have a local port.
    assert any(record.state == "ESTABLISHED" for record in found)


def test_connection_alive_selects_only_established(records) -> None:
    found = apply_tags(records, "connection:alive")
    assert found
    assert all(record.state == "ESTABLISHED" for record in found)


@pytest.mark.parametrize("spelling", ["port:open", "p:o", "por:op", "po:open", "PORT=OPEN"])
def test_every_spelling_finds_the_same_listeners(records, spelling: str) -> None:
    assert apply_tags(records, spelling) == apply_tags(records, "port:open")


def test_abbreviations_find_the_same_established_connection(records) -> None:
    assert apply_tags(records, "c:a") == apply_tags(records, "connection:alive")


def test_tags_combine_with_free_text(records) -> None:
    listeners = apply_tags(records, "port:open")
    assert apply_tags(records, "8080 po:open") == [
        record for record in listeners if "8080" in record.search_blob
    ]


def test_two_tags_are_anded(records) -> None:
    # Established sockets have a local port, so the two tags intersect here.
    assert apply_tags(records, "p:o c:a") == apply_tags(records, "c:a")
    # Waiting sockets also have a local port, but no established connection.
    assert apply_tags(records, "p:o c:w") == []


async def test_browser_search_bar_accepts_abbreviated_tags(states_output: str) -> None:
    records, _ = parse_lsof_output(states_output)
    process = ProcessRecord(pid=777, name="webserver")
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.4)
        app.push_resource_search(records, process, None)
        await pilot.pause(0.3)
        screen = app.screen
        assert isinstance(screen, ResourceBrowserScreen)

        search = screen.query_one("#browser-search", Input)
        search.value = "c:a"
        await pilot.pause()

        table = screen.query_one("#browser-grid", DataTable)
        assert table.row_count == 1
        assert "established" in screen._shown[0].search_blob


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("*:80", 80),
        ("127.0.0.1:8080", 8080),
        ("[::1]:8443", 8443),
        ("10.0.0.9:45000->93.184.216.34:443", 45000),
        ("[fe80::1]:1024->[fe80::2]:1024", 1024),
        ("*:*", None),
        ("", None),
    ],
)
def test_lsof_local_port_parsing(field: str, expected: int | None) -> None:
    assert _lsof_local_port(field) == expected


async def test_network_pid_sets_classifies_only_tcp_states(monkeypatch) -> None:
    def conn(pid, status, *, kind=socket.SOCK_STREAM, port=5000):
        return SimpleNamespace(
            pid=pid,
            type=kind,
            status=status,
            laddr=SimpleNamespace(port=port),
        )

    connections = [
        conn(10, psutil.CONN_LISTEN),
        conn(20, psutil.CONN_ESTABLISHED),
        conn(20, psutil.CONN_CLOSE_WAIT),
        conn(40, "FIN_WAIT1"),
        conn(50, "SYN_RECV"),
        conn(30, None, kind=socket.SOCK_DGRAM, port=8125),
        conn(None, psutil.CONN_LISTEN),
    ]
    monkeypatch.setattr(
        "ports_explorer.services.search_tags.psutil.net_connections",
        lambda kind="inet": connections,
    )

    async def no_lsof() -> dict[str, set[int]]:
        return _empty_pid_sets()

    monkeypatch.setattr("ports_explorer.services.search_tags._lsof_pid_sets", no_lsof)
    pids = await network_pid_sets()
    # Every bound local port counts, including the UDP-only PID 30.
    assert pids["port:open"] == {10, 20, 30, 40, 50}
    assert pids["connection:alive"] == {20}
    assert pids["connection:waiting"] == {20}
    assert pids["connection:closing"] == {40}
    assert pids["connection:new"] == {50}


async def test_network_pid_sets_falls_back_to_lsof_on_macos(monkeypatch) -> None:
    monkeypatch.setattr(
        "ports_explorer.services.search_tags.psutil.net_connections",
        lambda kind="inet": [],
    )

    async def fake_lsof() -> dict[str, set[int]]:
        return {"port:open": {101}, "connection:alive": {202}}

    monkeypatch.setattr("ports_explorer.services.search_tags._lsof_pid_sets", fake_lsof)
    expected = _empty_pid_sets()
    expected["port:open"].add(101)
    expected["connection:alive"].add(202)
    assert await network_pid_sets() == expected


def test_split_process_tags_resolves_abbreviations() -> None:
    pids = {
        "port:open": {1, 2},
        "connection:alive": {3},
        "connection:waiting": {4},
        "connection:closing": {5},
        "connection:new": {6},
    }
    query, predicates = split_process_tags("p:o c:a c:w c:cl c:n web", pids=pids)
    assert query == "web"
    assert len(predicates) == 5
    checks = zip(predicates, (1, 3, 4, 5, 6), strict=True)
    assert all(predicate(ProcessRecord(pid=pid, name="x")) for predicate, pid in checks)


async def test_main_search_bar_filters_by_open_port_tag() -> None:
    app = PortsExplorerApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.6)
        screen = app.screen
        assert len(screen._all) >= 2
        listener, other = screen._all[0], screen._all[1]
        screen._tag_pids = {
            "port:open": {listener.pid},
            "connection:alive": {other.pid},
        }

        search = screen.query_one("#process-search", Input)
        search.focus()
        await pilot.pause()
        for character in "p:o":
            await pilot.press(character)
        await pilot.pause()

        assert screen._filtered
        assert all(process.pid == listener.pid for process in screen._filtered)
        assert screen.query_one("#process-grid", DataTable).row_count == len(screen._filtered)
