"""Semantic tags for the resource search bars.

A tag is ``key:value`` (``key=value`` also works) where both halves may be any
unambiguous, case-insensitive prefix of the canonical spelling. So all of these
select the same records::

    port:open   p:o   por:op   po:open   PORT=OPEN

Five tags ship today:

``port:open``
    Any resource with a bound local port — listening *or* an active client
    connection (e.g. Notes has established connections but no listener).
``connection:alive``
    A TCP connection in ``ESTABLISHED`` state — a live, established connection.
``connection:waiting``
    ``CLOSE_WAIT`` / ``TIME_WAIT`` — the peer is gone or the close is pending.
``connection:closing``
    ``FIN_WAIT_1`` / ``FIN_WAIT_2`` / ``CLOSING`` / ``LAST_ACK``.
``connection:new``
    ``SYN_SENT`` / ``SYN_RECV`` — a handshake still in progress.

Tags are deliberately resolved *before* the ordinary field search so that
``port:open`` never degenerates into a literal substring lookup. This module has
no Textual imports and is shared by the dedicated resource browser and the
per-table filters.
"""

from __future__ import annotations

import asyncio
import re
import socket
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from subprocess import SubprocessError

import psutil

from ..models import ProcessRecord, ResourceRecord, SocketState, TransportProtocol
from .resource_search import ResourceFilter, search_resources

__all__ = [
    "TAGS",
    "SearchTag",
    "apply_tags",
    "network_pid_sets",
    "resolve_tag",
    "split_process_tags",
    "split_tags",
]

Predicate = Callable[[ResourceRecord], bool]


_WAITING_STATES = frozenset({SocketState.CLOSE_WAIT, SocketState.TIME_WAIT})
_CLOSING_STATES = frozenset(
    {
        SocketState.FIN_WAIT_1,
        SocketState.FIN_WAIT_2,
        SocketState.CLOSING,
        SocketState.LAST_ACK,
    }
)
_NEW_STATES = frozenset({SocketState.SYN_SENT, SocketState.SYN_RECV})


def _in_states(record: ResourceRecord, states: frozenset[SocketState]) -> bool:
    """True for a TCP socket in one of ``states`` (non-TCP sockets never match)."""
    return (
        record.is_inet
        and record.transport is TransportProtocol.TCP
        and record.socket_state in states
    )


def _is_open_port(record: ResourceRecord) -> bool:
    """Any bound local port — listener, established client socket or UDP.

    A client-only app such as Notes has no LISTEN descriptor, so restricting
    this tag to LISTEN hid processes that very obviously have a port open.
    """
    return record.local.port is not None


def _is_alive_connection(record: ResourceRecord) -> bool:
    """Established TCP socket — a connection that is actually alive."""
    return _in_states(record, frozenset({SocketState.ESTABLISHED}))


@dataclass(frozen=True, slots=True)
class SearchTag:
    """One canonical tag and the predicate that implements it."""

    key: str
    value: str
    description: str
    matches: Predicate

    @property
    def label(self) -> str:
        return f"{self.key}:{self.value}"


TAGS: tuple[SearchTag, ...] = (
    SearchTag("port", "open", "TCP port in LISTEN state", _is_open_port),
    SearchTag("connection", "alive", "TCP connection in ESTABLISHED state", _is_alive_connection),
    SearchTag(
        "connection",
        "waiting",
        "CLOSE_WAIT / TIME_WAIT connection",
        lambda record: _in_states(record, _WAITING_STATES),
    ),
    SearchTag(
        "connection",
        "closing",
        "FIN_WAIT / CLOSING / LAST_ACK connection",
        lambda record: _in_states(record, _CLOSING_STATES),
    ),
    SearchTag(
        "connection",
        "new",
        "SYN_SENT / SYN_RECV handshake",
        lambda record: _in_states(record, _NEW_STATES),
    ),
)

# A standalone key:value / key=value token. Quoted values are supported, and a
# quoted phrase like "port:open" is deliberately not matched (its leading quote
# breaks the left boundary), so literal text is never mistaken for a tag.
_TAG_TOKEN = re.compile(r'(?<!\S)(?P<key>[A-Za-z]+)[:=](?P<value>"[^"]*"|[^\s]+)(?!\S)')


def resolve_tag(key: str, value: str) -> SearchTag | None:
    """Resolve an abbreviated key/value pair to exactly one tag.

    Both halves are prefix-matched against the canonical spelling, so ``p``
    expands to ``port`` and ``op`` to ``open``. If an abbreviation matches more
    than one tag it is treated as ordinary text instead of guessing.
    """
    needle_key = key.strip().lower()
    needle_value = value.strip().strip('"').lower()
    if not needle_key or not needle_value:
        return None
    found = [
        tag for tag in TAGS if tag.key.startswith(needle_key) and tag.value.startswith(needle_value)
    ]
    return found[0] if len(found) == 1 else None


def split_tags(query: str) -> tuple[str, list[Predicate]]:
    """Strip recognized tags from ``query`` and return their predicates.

    Unknown ``key:value`` tokens are left untouched so they still reach the
    normal search (and so the UI never silently drops a filter it cannot parse).
    """
    predicates: list[Predicate] = []

    def replace(match: re.Match[str]) -> str:
        tag = resolve_tag(match.group("key"), match.group("value"))
        if tag is None:
            return match.group(0)
        predicates.append(tag.matches)
        return " "

    remaining = _TAG_TOKEN.sub(replace, query or "")
    # Collapse the gaps left by removed tags; keep quoted phrases intact.
    return " ".join(remaining.split()), predicates


def apply_tags(
    records: Sequence[ResourceRecord],
    query: str,
    *,
    criteria: ResourceFilter | None = None,
) -> list[ResourceRecord]:
    """Run the ordinary search, then narrow it by any recognized tags."""
    remaining, predicates = split_tags(query)
    found = search_resources(records, remaining, criteria=criteria)
    if predicates:
        found = [record for record in found if all(predicate(record) for predicate in predicates)]
    return found


def _normalize_status(status: str | None) -> str:
    """Canonical form for a state coming from psutil or lsof."""
    return (status or "").upper().replace("_", "").replace("-", "")


# psutil and lsof spell the same TCP states slightly differently; normalising
# both lets one table drive either source.
_STATUS_TAG = {
    "LISTEN": "port:open",
    "ESTABLISHED": "connection:alive",
    "CLOSEWAIT": "connection:waiting",
    "TIMEWAIT": "connection:waiting",
    "FINWAIT1": "connection:closing",
    "FINWAIT2": "connection:closing",
    "CLOSING": "connection:closing",
    "LASTACK": "connection:closing",
    "SYNSENT": "connection:new",
    "SYNRECV": "connection:new",
}


def _empty_pid_sets() -> dict[str, set[int]]:
    return {tag.label: set() for tag in TAGS}


def _collect_pid(sets: dict[str, set[int]], pid: int | None, status: str | None) -> None:
    if pid is None or pid <= 0:
        return
    label = _STATUS_TAG.get(_normalize_status(status))
    if label is not None:
        sets[label].add(pid)


def _psutil_pid_sets() -> dict[str, set[int]]:
    """Fast path: one system-wide psutil call, when the OS exposes PIDs."""
    sets = _empty_pid_sets()
    try:
        connections = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, psutil.Error, OSError, NotImplementedError):
        return sets
    for connection in connections:
        pid = getattr(connection, "pid", None)
        # port:open means any bound local port, including UDP and client-side
        # established sockets that never enter LISTEN.
        laddr = getattr(connection, "laddr", None)
        if pid is not None and pid > 0 and getattr(laddr, "port", None) is not None:
            sets["port:open"].add(pid)
        if connection.type != socket.SOCK_STREAM:
            continue
        _collect_pid(sets, connection.pid, connection.status)
    return sets


def _lsof_local_port(value: str) -> int | None:
    """Extract the local port from an lsof ``n`` field (``*:80``, ``[::1]:443``…)."""
    local = value.split("->", 1)[0]
    if local.startswith("["):
        closing = local.find("]")
        if closing == -1:
            return None
        local = local[closing + 1 :]
    if ":" not in local:
        return None
    port = local.rsplit(":", 1)[1]
    return int(port) if port.isdigit() else None


async def _lsof_pid_sets() -> dict[str, set[int]]:
    """macOS fallback: unprivileged psutil hides PIDs, but lsof reports them."""
    sets = _empty_pid_sets()
    try:
        process = await asyncio.create_subprocess_exec(
            "lsof",
            "-nP",
            "-i",
            "-FpnT",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await process.communicate()
    except (OSError, SubprocessError):
        return sets

    pid: int | None = None
    for line in stdout.decode(errors="replace").splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith("n") and pid is not None:
            if _lsof_local_port(line[1:]) is not None:
                sets["port:open"].add(pid)
        elif line.startswith("TST=") and pid is not None:
            _collect_pid(sets, pid, line[4:])
    return sets


async def network_pid_sets() -> dict[str, set[int]]:
    """Map each tag to the PIDs currently in its TCP state group.

    psutil is tried first (one cheap system-wide call). On macOS an unprivileged
    process sees no PIDs there, so we fall back to a single ``lsof`` query that
    reports both the PID and the state of every TCP descriptor.
    """
    sets = _psutil_pid_sets()
    if any(sets.values()):
        return sets
    return await _lsof_pid_sets()


ProcessPredicate = Callable[[ProcessRecord], bool]


def split_process_tags(
    query: str,
    *,
    pids: Mapping[str, set[int]],
) -> tuple[str, list[ProcessPredicate]]:
    """Strip tags from a process-list query, returning PID-based predicates."""
    predicates: list[ProcessPredicate] = []

    def replace(match: re.Match[str]) -> str:
        tag = resolve_tag(match.group("key"), match.group("value"))
        if tag is None:
            return match.group(0)
        tag_pids = pids.get(tag.label, set())
        predicates.append(lambda process, tag_pids=tag_pids: process.pid in tag_pids)
        return " "

    remaining = _TAG_TOKEN.sub(replace, query or "")
    return " ".join(remaining.split()), predicates
