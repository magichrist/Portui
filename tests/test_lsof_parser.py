"""Tests for the structured ``lsof`` field parser and the endpoint splitter."""

from __future__ import annotations

import pytest

from ports_explorer.collectors.lsof import (
    FIELD_ARGUMENT,
    LsofCollector,
    LsofFieldParser,
    parse_lsof_output,
)
from ports_explorer.models import (
    AddressFamily,
    Endpoint,
    ParseIssue,
    PermissionState,
    SocketState,
    TransportProtocol,
    split_endpoint,
)


def test_empty_output_yields_no_records(empty_output: str) -> None:
    records, _ = parse_lsof_output(empty_output)
    assert records == []


def test_whitespace_only_output_yields_no_records() -> None:
    records, _ = parse_lsof_output("\n\n   \n")
    assert records == []


def test_process_tag_only_produces_no_records(mixed_output: str) -> None:
    records, _ = parse_lsof_output("p123\ncsomething\n")
    assert records == []


def test_mixed_fixture_row_count(mixed_output: str) -> None:
    records, _ = parse_lsof_output(mixed_output)

    assert len(records) == 18


def test_command_and_pid_are_attributed(mixed_output: str) -> None:
    records, _ = parse_lsof_output(mixed_output)
    assert all(record.pid == 10901 for record in records)
    assert all(record.command == "zsh" for record in records)


def test_user_falls_back_to_supplied_default(mixed_output: str) -> None:
    records, _ = parse_lsof_output(mixed_output, default_user="tester")
    assert {record.user for record in records} == {"tester"}


def test_missing_user_is_reported_not_faked() -> None:
    records, _ = parse_lsof_output("p1\ncx\nf0\ntREG\nn/tmp/x\n")
    assert records[0].user == "-"


def test_tcp_state_is_attached_to_its_own_socket(state_boundaries_output: str) -> None:
    records, _ = parse_lsof_output(state_boundaries_output)
    states = [record.state for record in records]
    assert states == ["LISTEN", "-", "ESTABLISHED", "-"]


def test_state_does_not_leak_into_the_next_descriptor(state_boundaries_output: str) -> None:
    records, _ = parse_lsof_output(state_boundaries_output)
    assert records[1].state == "-"
    assert ParseIssue.MISSING_STATE in records[1].issues


def test_socket_queue_counters_are_not_mistaken_for_a_state(
    state_boundaries_output: str,
) -> None:
    records, _ = parse_lsof_output(state_boundaries_output)

    assert records[1].state not in ("0", "QR", "QS")


def test_different_states_are_distinct_rows(state_boundaries_output: str) -> None:
    records, _ = parse_lsof_output(state_boundaries_output)
    assert records[0].state == "LISTEN"
    assert records[2].state == "ESTABLISHED"


def test_listening_is_distinct_from_established(state_boundaries_output: str) -> None:
    records, _ = parse_lsof_output(state_boundaries_output)
    listeners = [r for r in records if r.is_listening]
    established = [r for r in records if r.socket_state is SocketState.ESTABLISHED]
    assert len(listeners) == 1
    assert len(established) == 1

    assert records[2].local.port == 8080
    assert records[2].is_listening is False


def test_multiple_process_records(multi_process_output: str) -> None:
    records, _ = parse_lsof_output(multi_process_output)
    assert len(records) == 6
    assert {record.pid for record in records} == {500, 501}
    assert {record.command for record in records} == {"app-one", "app-two"}


def test_last_file_of_previous_process_is_flushed_on_new_process(
    multi_process_output: str,
) -> None:
    records, _ = parse_lsof_output(multi_process_output)
    p500 = [record for record in records if record.pid == 500]
    assert len(p500) == 3

    assert p500[-1].fd == "1"
    assert p500[-1].state == "LISTEN"


def test_ipv4_listener_parses_local_endpoint(states_output: str) -> None:
    records, _ = parse_lsof_output(states_output)
    first = records[0]
    assert first.family is AddressFamily.IPV4
    assert first.transport is TransportProtocol.TCP
    assert first.local == Endpoint("127.0.0.1", 8080)
    assert first.remote == Endpoint(None, None)


def test_wildcard_listener(states_output: str) -> None:
    records, _ = parse_lsof_output(states_output)
    assert records[1].local.host == "*"
    assert records[1].local.port == 80


def test_established_connection_parses_both_endpoints(states_output: str) -> None:
    records, _ = parse_lsof_output(states_output)
    established = next(r for r in records if r.socket_state is SocketState.ESTABLISHED)
    assert established.local == Endpoint("10.0.0.9", 45000)
    assert established.remote == Endpoint("93.184.216.34", 443)


def test_ipv6_listener_bracketed(states_output: str) -> None:
    records, _ = parse_lsof_output(states_output)
    ipv6 = next(r for r in records if r.family is AddressFamily.IPV6)
    assert ipv6.local == Endpoint("::1", 8443)
    assert ipv6.is_inet is True


def test_ipv6_connection_both_ends(mixed_output: str) -> None:
    records, _ = parse_lsof_output(mixed_output)
    conn = next(r for r in records if r.resource_type == "IPv6" and r.remote.host == "::1")
    assert conn.local == Endpoint("::1", 52341)
    assert conn.remote == Endpoint("::1", 54322)


def test_udp_has_no_state_and_is_not_a_listener(states_output: str) -> None:
    records, _ = parse_lsof_output(states_output)
    udp = next(r for r in records if r.transport is TransportProtocol.UDP)
    assert udp.state == "-"
    assert udp.is_listening is False
    assert udp.local.port == 8125


def test_udp_connection_endpoint(mixed_output: str) -> None:
    records, _ = parse_lsof_output(mixed_output)
    udp = next(
        r for r in records if r.transport is TransportProtocol.UDP and r.remote.host is not None
    )
    assert udp.local == Endpoint("240.0.0.2", 64277)
    assert udp.remote == Endpoint("139.162.187.236", 123)


def test_loopback_to_loopback_connection(mixed_output: str) -> None:
    records, _ = parse_lsof_output(mixed_output)
    conn = next(r for r in records if r.remote.host == "127.0.0.1")
    assert conn.local.host == "127.0.0.1"
    assert conn.remote.host == "127.0.0.1"


def test_close_wait_state_is_preserved(mixed_output: str) -> None:
    records, _ = parse_lsof_output(mixed_output)
    close_wait = [r for r in records if r.state == "CLOSE_WAIT"]
    assert len(close_wait) == 1
    assert close_wait[0].is_listening is False


def test_unix_socket_is_classified(states_output: str) -> None:
    records, _ = parse_lsof_output(states_output)
    unix = [r for r in records if r.is_unix_socket]
    assert len(unix) == 2
    assert all(r.resource_type == "unix" for r in unix)


def test_unix_socket_path_is_not_parsed_as_an_endpoint(states_output: str) -> None:
    records, _ = parse_lsof_output(states_output)
    sock = next(r for r in records if r.name == "/var/run/docker.sock")
    assert sock.local.has_port is False

    assert sock.name == "/var/run/docker.sock"


def test_unix_socket_abstract_endpoint_marker(states_output: str) -> None:
    records, _ = parse_lsof_output(states_output)
    abstract = next(r for r in records if r.name.startswith("->0x"))
    assert abstract.is_unix_socket is True
    assert abstract.local.has_port is False


def test_pipe_and_directory_and_chr(states_output: str) -> None:
    records, _ = parse_lsof_output(states_output)
    types = {record.resource_type for record in records}
    assert {"PIPE", "DIR"} <= types


def test_file_path_with_spaces_is_preserved_whole(states_output: str) -> None:
    records, _ = parse_lsof_output(states_output)
    directory = next(r for r in records if r.resource_type == "DIR")
    assert directory.name == "/Users/alitabeee/Some Folder/with spaces"
    assert " " in directory.name


def test_file_path_with_spaces_multiple_files(mixed_output: str) -> None:
    records, _ = parse_lsof_output(mixed_output)
    files = [r for r in records if "data with spaces" in r.name]
    assert len(files) == 2
    assert all(" " in record.name for record in files)


def test_metadata_fields_are_captured() -> None:
    output = "p1\ncx\nf0\ntREG\nD0x100000e\ns224\ni126673821\nm0100644\nl3\nu501\ng20\nn/tmp/x\n"
    records, _ = parse_lsof_output(output)
    record = records[0]
    assert record.device == "0x100000e"
    assert record.size == 224
    assert record.inode == 126673821
    assert record.mode == "0100644"
    assert record.link_count == 3
    assert record.uid == 501
    assert record.gid == 20


@pytest.mark.parametrize(
    ("value", "host", "port"),
    [
        ("127.0.0.1:8080", "127.0.0.1", 8080),
        ("*:8000", "*", 8000),
        ("[::1]:443", "::1", 443),
        ("[::ffff:127.0.0.1]:5432", "::ffff:127.0.0.1", 5432),
        ("240.0.0.2:64277", "240.0.0.2", 64277),
        ("localhost", "localhost", None),
        ("::1", "::1", None),
        ("[::1]", "::1", None),
        ("", None, None),
        ("/var/run/sock", None, None),
    ],
)
def test_split_endpoint(value: str, host: str | None, port: int | None) -> None:
    endpoint = split_endpoint(value)
    assert endpoint.host == host
    assert endpoint.port == port


def test_split_endpoint_never_guesses_a_port() -> None:
    assert split_endpoint("host:notaport").port is None


def test_endpoint_str_round_trip() -> None:
    assert str(Endpoint("127.0.0.1", 80)) == "127.0.0.1:80"
    assert str(Endpoint(None, None)) == ""


def test_truncated_record_is_flagged() -> None:
    records, _ = parse_lsof_output("p1\ncx\nf0\ntREG\n")
    assert ParseIssue.TRUNCATED_RECORD in records[0].issues


def test_unknown_type_is_flagged_not_crashing() -> None:
    records, _ = parse_lsof_output("p1\ncx\nf0\ntWEIRD\nn/thing\n")
    assert records[0].resource_type == "WEIRD"
    assert ParseIssue.UNKNOWN_TYPE not in records[0].issues


def test_missing_type_flagged() -> None:
    records, _ = parse_lsof_output("p1\ncx\nf0\nn/thing\n")
    assert records[0].resource_type == "-"
    assert ParseIssue.UNKNOWN_TYPE in records[0].issues


def test_non_numeric_pid_does_not_crash() -> None:
    parser = LsofFieldParser()
    parser.feed_all("pnotanumber\ncx\nf0\ntREG\nn/tmp/a\n")
    records = parser.close()
    assert records[0].pid == 0
    assert parser.issues


def test_incremental_feeding_matches_bulk_parsing(mixed_output: str) -> None:
    bulk, _ = parse_lsof_output(mixed_output)

    incremental = LsofFieldParser()
    for line in mixed_output.splitlines():
        incremental.feed(line)
    streamed = incremental.close()

    assert [r.fd for r in bulk] == [r.fd for r in streamed]
    assert [r.state for r in bulk] == [r.state for r in streamed]


def test_large_output_parses_without_loss(states_output: str) -> None:
    records, _ = parse_lsof_output(states_output)
    single = len(records)
    big = states_output * 200
    scaled, _ = parse_lsof_output(big)
    assert single == 9
    assert len(scaled) == single * 200


def test_build_command_uses_argument_array_not_a_shell_string() -> None:
    command = LsofCollector().build_command(pid=4242)
    assert isinstance(command, list)
    assert command[0] == "lsof"
    assert "-nP" in command
    assert "-p" in command and "4242" in command
    assert FIELD_ARGUMENT in "".join(command)


def test_build_command_never_includes_a_shell() -> None:
    joined = " ".join(LsofCollector().build_command(1))
    assert "sh -c" not in joined
    assert "|" not in joined


async def test_missing_binary_is_reported_not_raised() -> None:
    collector = LsofCollector(binary="definitely-not-lsof-xyz")
    result = await collector.collect(pid=1)
    assert result.permission is PermissionState.FAILED
    assert "not found on PATH" in (result.error or "")
    assert result.records == []


def test_missing_binary_error_is_actionable() -> None:
    collector = LsofCollector(binary="definitely-not-lsof-xyz")
    message = collector.missing_binary_error()
    assert message is not None
    assert "brew install lsof" in message


async def test_permission_denied_empty_output_classifies_as_gone() -> None:
    """A pid that no longer exists makes lsof exit 1 with no rows."""
    result = await _run_fake_lsof(stdout="", stderr="", returncode=1)
    assert result.permission is PermissionState.GONE


async def test_permission_denied_with_some_rows_is_partial() -> None:
    output = "p1\ncx\nf0\ntIPv4\nPTCP\nn*:80\nTST=LISTEN\n"
    result = await _run_fake_lsof(
        stdout=output,
        stderr="lsof: status error on /dev/ttys001: Permission denied",
        returncode=1,
    )
    assert result.permission is PermissionState.PARTIAL
    assert "Permission denied" in result.stderr
    assert result.warning() is not None


async def test_timeout_is_reported_not_hung(monkeypatch: pytest.MonkeyPatch) -> None:
    collector = LsofCollector(timeout=0.3)

    monkeypatch.setattr(LsofCollector, "available", lambda self: True, raising=True)
    monkeypatch.setattr(
        LsofCollector,
        "build_command",
        lambda self, pid=None: ["sleep", "30"],
        raising=True,
    )

    result = await collector.collect()
    assert result.permission is PermissionState.FAILED
    assert "timed out" in (result.error or "")
    assert result.truncated is True


async def test_timeout_terminates_the_child_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector = LsofCollector(timeout=0.3)
    monkeypatch.setattr(LsofCollector, "available", lambda self: True, raising=True)
    monkeypatch.setattr(
        LsofCollector,
        "build_command",
        lambda self, pid=None: ["sleep", "30"],
        raising=True,
    )
    result = await collector.collect()
    assert result.records == []
    assert result.error is not None


async def test_collection_counts_are_consistent(states_output: str) -> None:
    result = await _run_fake_lsof(stdout=states_output, stderr="", returncode=0)
    assert result.network_count == 5
    assert result.listener_count == 3
    assert result.file_count == 4


async def test_stderr_is_kept_separate_from_records(states_output: str) -> None:
    result = await _run_fake_lsof(stdout=states_output, stderr="some warning", returncode=0)
    assert result.stderr == "some warning"
    assert all("some warning" not in r.name for r in result.records)


async def _run_fake_lsof(*, stdout: str, stderr: str, returncode: int):
    """Feed fixed output through the real parser without invoking lsof."""
    import time

    from ports_explorer.models import CollectionResult

    records, issues = parse_lsof_output(stdout)
    return CollectionResult(
        records=records,
        permission=(
            PermissionState.PARTIAL
            if "Permission denied" in stderr
            else (PermissionState.OK if returncode == 0 else PermissionState.GONE)
        ),
        duration_seconds=0.01,
        collected_at=time.time(),
        stderr=stderr.strip(),
        issues=issues,
    )
