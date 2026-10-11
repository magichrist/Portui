"""Tests for search and filtering over resources and processes."""

from __future__ import annotations

import pytest

from ports_explorer.collectors.lsof import parse_lsof_output
from ports_explorer.models import ProcessRecord
from ports_explorer.services.resource_search import (
    ResourceFilter,
    filter_resources,
    search_processes,
    search_resources,
    tokenize,
)


@pytest.fixture
def records(states_output: str):
    parsed, _ = parse_lsof_output(states_output)
    return parsed


def test_tokenize_splits_on_whitespace() -> None:
    assert tokenize("a b c") == ["a", "b", "c"]


def test_tokenize_honours_quoted_phrases_and_strips_the_quotes() -> None:
    assert tokenize('"some folder" file') == ["some folder", "file"]


def test_empty_query_yields_no_tokens() -> None:
    assert tokenize("   ") == []


def test_empty_query_returns_everything(records) -> None:
    assert len(search_resources(records, "")) == len(records)


def test_search_by_ip(records) -> None:
    found = search_resources(records, "127.0.0.1")
    assert found
    assert all("127.0.0.1" in r.search_blob for r in found)


def test_search_by_port_string(records) -> None:
    found = search_resources(records, "8080")
    assert found


def test_search_by_state(records) -> None:
    found = search_resources(records, "LISTEN")
    assert len(found) == 3


def test_search_by_protocol(records) -> None:
    found = search_resources(records, "UDP")
    assert len(found) == 1


def test_search_by_resource_type(records) -> None:
    found = search_resources(records, "unix")
    assert len(found) == 2


def test_search_by_file_descriptor(records) -> None:
    found = search_resources(records, "11")
    assert found
    assert any(r.fd == "11" for r in found)


def test_search_by_fd_field_term(records) -> None:
    found = search_resources(records, "fd=10")
    assert len(found) == 1
    assert found[0].fd == "10"


def test_search_by_path_with_spaces_quoted(states_output: str) -> None:
    parsed, _ = parse_lsof_output(states_output)
    found = search_resources(parsed, '"Some Folder"')
    assert len(found) == 1
    assert found[0].resource_type == "DIR"


def test_search_is_case_insensitive(records) -> None:
    assert len(search_resources(records, "listen")) == len(search_resources(records, "LISTEN"))


def test_multiple_free_tokens_use_and_semantics(records) -> None:
    both = search_resources(records, "TCP LISTEN")
    either_only = search_resources(records, "PIPE UNIX")
    assert len(both) == 3
    assert either_only == []


def test_no_matches_returns_empty(records) -> None:
    assert search_resources(records, "zzzz-nonexistent") == []


def test_key_value_port_filter(records) -> None:
    found = search_resources(records, "port=8080")
    assert len(found) == 1


def test_key_value_port_filter_matches_both_states_when_present(
    state_boundaries_output: str,
) -> None:
    """8080 is both a LISTEN and an ESTABLISHED socket in this fixture."""
    parsed, _ = parse_lsof_output(state_boundaries_output)
    found = search_resources(parsed, "port=8080")
    assert len(found) == 3
    assert {r.state for r in found} == {"LISTEN", "ESTABLISHED", "-"}


def test_key_value_state_filter(records) -> None:
    assert len(search_resources(records, "state=LISTEN")) == 3


def test_key_value_protocol_filter(records) -> None:
    assert len(search_resources(records, "proto=UDP")) == 1


def test_key_value_remote_port(records) -> None:
    found = search_resources(records, "rport=443")
    assert len(found) == 1
    assert found[0].remote.port == 443


def test_key_value_local_ip(records) -> None:
    assert search_resources(records, "lip=10.0.0.9")


def test_key_value_remote_ip(records) -> None:
    assert search_resources(records, "rip=93.184.216.34")


def test_unknown_key_falls_back_to_substring(records) -> None:
    """An unrecognised key must not silently swallow the query."""
    assert search_resources(records, "banana=127.0.0.1")


def test_key_and_free_terms_combine(records) -> None:
    found = search_resources(records, "TCP state=LISTEN")
    assert len(found) == 3


def test_key_value_non_numeric_port_finds_nothing(records) -> None:
    assert search_resources(records, "port=abc") == []


def test_inactive_filter_is_a_no_op(records) -> None:
    assert len(filter_resources(records, ResourceFilter())) == len(records)


def test_none_filter_is_a_no_op(records) -> None:
    assert len(filter_resources(records, None)) == len(records)


def test_protocol_filter(records) -> None:
    assert len(filter_resources(records, ResourceFilter(protocol="TCP"))) == 4


def test_state_filter(records) -> None:
    assert len(filter_resources(records, ResourceFilter(state="LISTEN"))) == 3


def test_family_filter(records) -> None:
    assert len(filter_resources(records, ResourceFilter(family="IPv6"))) == 1


def test_local_port_filter(records) -> None:
    assert len(filter_resources(records, ResourceFilter(local_port=8080))) == 1


def test_remote_port_filter(records) -> None:
    assert len(filter_resources(records, ResourceFilter(remote_port=443))) == 1


def test_local_ip_substring_filter(records) -> None:
    assert len(filter_resources(records, ResourceFilter(local_ip="127.0.0.1"))) == 1


def test_listening_only_filter(records) -> None:
    result = filter_resources(records, ResourceFilter(listening_only=True))
    assert len(result) == 3
    assert all(r.is_listening for r in result)


def test_combined_filters_are_intersections(records) -> None:
    criteria = ResourceFilter(protocol="TCP", state="LISTEN", family="IPv6")
    result = filter_resources(records, criteria)
    assert len(result) == 1
    assert result[0].local.port == 8443


def test_impossible_filter_returns_empty_not_everything(records) -> None:
    criteria = ResourceFilter(protocol="UDP", state="LISTEN")
    assert filter_resources(records, criteria) == []


def test_filter_is_active_detection() -> None:
    assert ResourceFilter().is_active() is False
    assert ResourceFilter(listening_only=True).is_active() is True


def test_filter_describe_is_readable() -> None:
    criteria = ResourceFilter(protocol="TCP", local_port=80, listening_only=True)
    described = criteria.describe()
    assert "proto=TCP" in described
    assert "lport=80" in described
    assert "listening" in described


def test_search_applies_filter_before_query(records) -> None:
    criteria = ResourceFilter(protocol="TCP")
    result = search_resources(records, "LISTEN", criteria=criteria)
    assert len(result) == 3
    assert all(r.protocol == "TCP" for r in result)


def test_long_path_is_searchable_by_tail(records) -> None:
    """Truncating a column must never remove the value from the index."""
    found = search_resources(records, "with spaces")
    assert found


def test_full_raw_endpoint_is_searchable(mixed_output: str) -> None:
    parsed, _ = parse_lsof_output(mixed_output)
    found = search_resources(parsed, "93.184.216.34")
    assert found


def _process(**kwargs) -> ProcessRecord:
    """Build a realistic process record; override only the fields under test."""
    defaults: dict = {
        "pid": 1,
        "name": "nginx",
        "ppid": 0,
        "user": "root",
        "exe": "/usr/local/bin/nginx",
        "cmdline": ["nginx", "-g", "daemon off;"],
        "status": "running",
    }
    defaults.update(kwargs)
    if kwargs.get("name") and "exe" not in kwargs:
        defaults["exe"] = f"/usr/local/bin/{kwargs['name']}"
        defaults["cmdline"] = [kwargs["name"]]
    return ProcessRecord(**defaults)


def test_search_processes_empty_query_returns_all() -> None:
    processes = [_process(), _process(pid=2, name="python3")]
    assert len(search_processes(processes, "")) == 2


def test_search_processes_by_name() -> None:
    processes = [_process(), _process(pid=2, name="python3")]
    found = search_processes(processes, "nginx")
    assert len(found) == 1


def test_search_processes_by_pid() -> None:
    processes = [_process(pid=1234)]
    assert search_processes(processes, "1234")


def test_search_processes_by_username() -> None:
    processes = [_process(user="alita")]
    assert search_processes(processes, "alita")


def test_search_processes_by_exe_path() -> None:
    processes = [_process()]
    assert search_processes(processes, "/usr/local/bin")


def test_search_processes_by_command_line_argument() -> None:
    processes = [_process()]
    assert search_processes(processes, "daemon")


def test_search_processes_multiple_tokens_are_anded() -> None:
    processes = [_process(), _process(pid=2, name="python3", user="me")]
    found = search_processes(processes, "nginx root")
    assert len(found) == 1


def test_search_processes_no_match() -> None:
    assert search_processes([_process()], "kubernetes") == []


def test_process_search_handles_missing_fields() -> None:
    sparse = ProcessRecord(pid=99, name="mystery")
    assert search_processes([sparse], "99")
    assert search_processes([sparse], "mystery")
