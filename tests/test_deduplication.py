"""Tests for the centralized deduplication service.

The mandated key is ``(command, pid, user, name, state)``. FD must not appear
in it. These tests pin that contract down from every angle.
"""

from __future__ import annotations

from ports_explorer.collectors.lsof import parse_lsof_output
from ports_explorer.models import ResourceRecord
from ports_explorer.services.deduplication import (
    DeduplicationStats,
    dedup_key,
    deduplicate,
    filter_category,
    filter_files,
    filter_listeners,
    filter_network,
    filter_unix,
)


def make_record(
    *,
    command: str = "app",
    pid: int = 100,
    user: str = "alice",
    fd: str = "f1",
    resource_type: str = "IPv4",
    protocol: str = "TCP",
    name: str = "127.0.0.1:8080",
    state: str = "LISTEN",
    **extra: object,
) -> ResourceRecord:
    return ResourceRecord(
        command=command,
        pid=pid,
        user=user,
        fd=fd,
        resource_type=resource_type,
        protocol=protocol,
        name=name,
        state=state,
        raw_name=name,
        **extra,
    )


def test_key_excludes_fd() -> None:
    a = make_record(fd="f1")
    b = make_record(fd="f99")
    assert dedup_key(a) == dedup_key(b)


def test_key_has_exactly_five_components() -> None:
    key = dedup_key(make_record())
    assert len(key) == 5
    assert isinstance(key[1], int)


def test_key_includes_name_and_state() -> None:
    assert dedup_key(make_record(name="a:1")) != dedup_key(make_record(name="b:1"))
    assert dedup_key(make_record(state="LISTEN")) != dedup_key(make_record(state="ESTABLISHED"))


def test_key_includes_pid_and_user_and_command() -> None:
    base = dedup_key(make_record())
    assert base != dedup_key(make_record(pid=101))
    assert base != dedup_key(make_record(user="bob"))
    assert base != dedup_key(make_record(command="other"))


def test_same_socket_two_fds_collapses_to_one_row() -> None:
    records = [make_record(fd="f1"), make_record(fd="f7")]
    result = deduplicate(records)
    assert len(result) == 1
    assert result[0].fd == "f1"


def test_three_duplicate_fds_from_real_fixture(duplicate_fds_output: str) -> None:
    parsed, _ = parse_lsof_output(duplicate_fds_output)
    assert len(parsed) == 3
    result = deduplicate(parsed)
    assert len(result) == 1

    assert result[0].fd == "10"
    assert set(result[0].aliases) == {"10", "11", "12"}


def test_first_fd_is_preserved_for_display() -> None:
    records = [make_record(fd="c"), make_record(fd="a"), make_record(fd="b")]
    assert deduplicate(records)[0].fd == "c"


def test_all_colliding_fds_are_retained_as_aliases() -> None:
    records = [make_record(fd=f"f{i}") for i in range(5)]
    result = deduplicate(records)
    assert result[0].aliases == ("f0", "f1", "f2", "f3", "f4")


def test_single_record_gets_no_aliases() -> None:
    assert deduplicate([make_record()])[0].aliases == ()


def test_different_endpoints_stay_separate() -> None:
    records = [make_record(name="127.0.0.1:8080"), make_record(name="127.0.0.1:8081")]
    assert len(deduplicate(records)) == 2


def test_same_local_port_different_remote_ip_stays_separate() -> None:
    """Port-only equality must never merge unrelated endpoints."""
    records = [
        make_record(name="10.0.0.1:5000->1.1.1.1:443", state="ESTABLISHED"),
        make_record(name="10.0.0.1:5000->2.2.2.2:443", state="ESTABLISHED"),
    ]
    assert len(deduplicate(records)) == 2


def test_same_endpoint_different_states_stay_separate() -> None:
    records = [
        make_record(name="127.0.0.1:8080", state="LISTEN"),
        make_record(name="127.0.0.1:8080", state="ESTABLISHED"),
    ]
    assert len(deduplicate(records)) == 2


def test_different_pids_stay_separate(multi_process_output: str) -> None:
    parsed, _ = parse_lsof_output(multi_process_output)
    assert len(deduplicate(parsed)) == len(parsed)


def test_different_usernames_stay_separate() -> None:
    records = [make_record(user="alice"), make_record(user="bob")]
    assert len(deduplicate(records)) == 2


def test_different_commands_stay_separate() -> None:
    records = [make_record(command="a"), make_record(command="b")]
    assert len(deduplicate(records)) == 2


def test_different_resource_types_with_same_name_merge_per_the_specified_key() -> None:
    """The mandated key has no TYPE component, so these collapse to one row.

    This is deliberate: the specification fixes the key at
    ``(command, pid, user, name, state)``. In practice an IPv and a UNIX record
    can never share both ``name`` and ``state``, so the collision cannot occur
    with real ``lsof`` data. The test documents the contract rather than
    contradicting it.
    """
    records = [
        make_record(resource_type="IPv4", protocol="TCP", name="x:1"),
        make_record(resource_type="unix", protocol="-", name="x:1"),
    ]
    assert len(deduplicate(records)) == 1


def test_real_unix_and_ipv_records_never_collide(states_output: str) -> None:
    parsed, _ = parse_lsof_output(states_output)
    assert len(deduplicate(parsed)) == len(parsed)


def test_input_order_is_preserved() -> None:
    records = [
        make_record(name="c:1"),
        make_record(name="a:1"),
        make_record(name="b:1"),
    ]
    assert [r.name for r in deduplicate(records)] == ["c:1", "a:1", "b:1"]


def test_stats_report_collapsed_count() -> None:
    stats: list[DeduplicationStats] = []
    deduplicate([make_record(fd=f"f{i}") for i in range(4)], stats=stats)
    assert stats[0].input_count == 4
    assert stats[0].output_count == 1
    assert stats[0].collapsed == 3


def test_stats_are_optional() -> None:
    assert deduplicate([make_record()]) is not None


def test_empty_input_returns_empty_output() -> None:
    assert deduplicate([]) == []


def test_generator_input_is_supported() -> None:
    gen = (make_record(fd=f"f{i}") for i in range(3))
    assert len(deduplicate(gen)) == 1


def test_every_category_view_uses_the_same_dedup_semantics(mixed_output: str) -> None:
    """Deduplicating globally, then filtering, equals filtering, then deduplicating."""
    parsed, _ = parse_lsof_output(mixed_output)

    for category in ("network", "listeners", "files", "unix", "all"):
        global_then_filter = filter_category(deduplicate(parsed), category)
        filter_then_dedup = deduplicate(filter_category(parsed, category))

        keys_global = [dedup_key(r) for r in global_then_filter]
        keys_local = [dedup_key(r) for r in filter_then_dedup]

        assert keys_global == keys_local, f"mismatch in category {category}"
        assert len(keys_global) == len(set(keys_global)), f"duplicates leaked in {category}"


def test_file_paths_with_spaces_are_not_merged(mixed_output: str) -> None:
    parsed, _ = parse_lsof_output(mixed_output)
    files = filter_files(deduplicate(parsed))
    paths = [record.name for record in files if "data with spaces" in record.name]
    assert len(paths) == 2
    assert paths[0] != paths[1]


def test_two_processes_with_identical_endpoints_are_kept(
    multi_process_output: str,
) -> None:
    parsed, _ = parse_lsof_output(multi_process_output)
    result = deduplicate(parsed)
    assert len(result) == len(parsed)
    assert len({record.pid for record in result}) == 2


def test_filter_network_excludes_unix_and_files(states_output: str) -> None:
    parsed, _ = parse_lsof_output(states_output)
    for record in filter_network(deduplicate(parsed)):
        assert record.is_inet


def test_filter_listeners_only_returns_listening(states_output: str) -> None:
    parsed, _ = parse_lsof_output(states_output)
    listeners = filter_listeners(deduplicate(parsed))
    assert len(listeners) == 3
    assert all(r.state == "LISTEN" for r in listeners)


def test_filter_unix(states_output: str) -> None:
    parsed, _ = parse_lsof_output(states_output)
    assert len(filter_unix(deduplicate(parsed))) == 2


def test_filter_unknown_category_returns_everything(mixed_output: str) -> None:
    parsed, _ = parse_lsof_output(mixed_output)
    deduped = deduplicate(parsed)
    assert len(filter_category(deduped, "nonsense")) == len(deduped)


def test_counts_match_filter_lengths(states_output: str) -> None:
    parsed, _ = parse_lsof_output(states_output)
    deduped = deduplicate(parsed)
    assert len(filter_network(deduped)) + len(filter_files(deduped)) == len(deduped)
