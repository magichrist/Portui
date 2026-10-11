"""The single, centralized deduplication service.

Contract (identical to the original Bash tool, and enforced by tests):

    key = (command, pid, user, name, state)

FD, TYPE and PROTO are **excluded** from the key. The same socket reached
through several descriptors therefore collapses to one row, while a genuinely
different endpoint or a different connection state stays a separate row.

Every screen must go through :func:`deduplicate`. No screen implements its own
version, so the semantics cannot drift between views.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace

from ..models import ResourceRecord

__all__ = [
    "DeduplicationStats",
    "dedup_key",
    "deduplicate",
    "filter_category",
    "filter_files",
    "filter_listeners",
    "filter_network",
    "filter_unix",
]


def dedup_key(record: ResourceRecord) -> tuple[str, int, str, str, str]:
    """The canonical deduplication key.

    Note the deliberate absence of ``fd`` and of the parsed local/remote pair:
    two descriptors showing the same ``name`` and ``state`` are one resource.
    """
    return (record.command, record.pid, record.user, record.name, record.state)


class DeduplicationStats:
    """How many rows collapsed, so the UI can state it honestly."""

    __slots__ = ("collapsed", "input_count", "output_count")

    def __init__(self, input_count: int, output_count: int) -> None:
        self.input_count = input_count
        self.output_count = output_count
        self.collapsed = input_count - output_count

    def __repr__(self) -> str:
        return (
            f"DeduplicationStats(input={self.input_count}, "
            f"output={self.output_count}, collapsed={self.collapsed})"
        )


def deduplicate(
    records: Iterable[ResourceRecord],
    *,
    stats: list[DeduplicationStats] | None = None,
) -> list[ResourceRecord]:
    """Collapse records sharing ``(command, pid, user, name, state)``.

    The **first** record encountered is the representative row and its FD is the
    one displayed. Every colliding FD is retained in ``aliases`` so no
    diagnostic information is destroyed.

    Order is preserved from the input, which keeps socket ordering predictable
    for the user.
    """
    seen: dict[tuple[str, int, str, str, str], list[str]] = {}
    representatives: dict[tuple[str, int, str, str, str], ResourceRecord] = {}
    order: list[tuple[str, int, str, str, str]] = []
    input_count = 0

    for record in records:
        input_count += 1
        key = dedup_key(record)
        if key not in seen:
            seen[key] = [record.fd]
            representatives[key] = record
            order.append(key)
            continue
        seen[key].append(record.fd)

    results: list[ResourceRecord] = []
    for key in order:
        record = representatives[key]
        aliases = seen[key]
        if len(aliases) > 1:
            record = replace(record, aliases=tuple(aliases))
        results.append(record)

    if stats is not None:
        stats.append(DeduplicationStats(input_count, len(results)))
    return results


def filter_network(records: Sequence[ResourceRecord]) -> list[ResourceRecord]:
    """IPv4/IPv6 sockets only, regardless of listening or connection state."""
    return [record for record in records if record.is_inet]


def filter_listeners(records: Sequence[ResourceRecord]) -> list[ResourceRecord]:
    """Only sockets actually in the LISTEN state.

    An open port is not automatically a listener: an ESTABLISHED socket with a
    local port is an outbound connection, not a bound service.
    """
    return [record for record in records if record.is_listening]


def filter_files(records: Sequence[ResourceRecord]) -> list[ResourceRecord]:
    """Regular files, directories, pipes, devices and everything non-IPv*."""
    return [record for record in records if not record.is_inet]


def filter_unix(records: Sequence[ResourceRecord]) -> list[ResourceRecord]:
    """UNIX-domain sockets only."""
    return [record for record in records if record.is_unix_socket]


def filter_category(records: Sequence[ResourceRecord], category: str) -> list[ResourceRecord]:
    """Dispatch by the category name used across the detail screen.

    All views deduplicate identically *before* reaching this point; these
    functions only narrow an already-deduplicated list.
    """
    match category:
        case "network":
            return filter_network(records)
        case "listeners":
            return filter_listeners(records)
        case "files":
            return filter_files(records)
        case "unix":
            return filter_unix(records)
        case _:
            return list(records)
