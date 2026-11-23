"""Semantic tags for the resource search bars.

A tag is ``key:value`` (``key=value`` also works) where both halves may be any
unambiguous, case-insensitive prefix of the canonical spelling. So all of these
select the same records::

    port:open   p:o   por:op   po:open   PORT=OPEN

Two tags ship today:

``port:open``
    A TCP port in ``LISTEN`` state — a bound, listening service.
``connection:alive``
    A TCP connection in ``ESTABLISHED`` state — a live, established connection.

Tags are deliberately resolved *before* the ordinary field search so that
``port:open`` never degenerates into a literal substring lookup. This module has
no Textual imports and is shared by the dedicated resource browser and the
per-table filters.
"""

from __future__ import annotations

import asyncio
import re
import socket
from collections.abc import Callable, Sequence
from dataclasses import dataclass

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


def _is_open_port(record: ResourceRecord) -> bool:
    """Bound TCP port in LISTEN state — a service waiting for connections."""
    return (
        record.is_inet
        and record.transport is TransportProtocol.TCP
        and record.socket_state is SocketState.LISTEN
    )


def _is_alive_connection(record: ResourceRecord) -> bool:
    """Established TCP socket — a connection that is actually alive."""
    return (
        record.is_inet
        and record.transport is TransportProtocol.TCP
        and record.socket_state is SocketState.ESTABLISHED
    )


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


def _psutil_pid_sets() -> tuple[set[int], set[int]]:
    """Fast path: one system-wide psutil call, when the OS exposes PIDs."""
    listening: set[int] = set()
    established: set[int] = set()
    try:
        connections = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, psutil.Error, OSError, NotImplementedError):
        return listening, established
    for connection in connections:
        if connection.pid is None or connection.pid <= 0:
            continue
        if connection.type != socket.SOCK_STREAM:
            continue
        status = (connection.status or "").upper()
        if status == psutil.CONN_LISTEN:
            listening.add(connection.pid)
        elif status == psutil.CONN_ESTABLISHED:
            established.add(connection.pid)
    return listening, established


async def _lsof_pid_sets() -> tuple[set[int], set[int]]:
    """macOS fallback: unprivileged psutil hides PIDs, but lsof reports them."""

    async def collect(state: str) -> set[int]:
        try:
            process = await asyncio.create_subprocess_exec(
                "lsof",
                "-nP",
                "-iTCP",
                f"-sTCP:{state}",
                "-Fp",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await process.communicate()
        except (OSError, asyncio.SubprocessError):
            return set()
        return {
            int(line[1:])
            for line in stdout.decode(errors="replace").splitlines()
            if line.startswith("p") and line[1:].isdigit()
        }

    listening, established = await asyncio.gather(collect("LISTEN"), collect("ESTABLISHED"))
    return listening, established


async def network_pid_sets() -> tuple[set[int], set[int]]:
    """Return ``(listening_pids, established_pids)`` for TCP connections.

    psutil is tried first (one cheap system-wide call). On macOS an unprivileged
    process sees no PIDs there, so we fall back to two narrow ``lsof`` queries —
    one per state — rather than reporting an empty, misleading result.
    """
    listening, established = _psutil_pid_sets()
    if listening or established:
        return listening, established
    return await _lsof_pid_sets()


ProcessPredicate = Callable[[ProcessRecord], bool]


def split_process_tags(
    query: str,
    *,
    listening: set[int],
    established: set[int],
) -> tuple[str, list[ProcessPredicate]]:
    """Strip tags from a process-list query, returning PID-based predicates."""
    predicates: list[ProcessPredicate] = []

    def replace(match: re.Match[str]) -> str:
        tag = resolve_tag(match.group("key"), match.group("value"))
        if tag is None:
            return match.group(0)
        pids = listening if tag.key == "port" else established
        predicates.append(lambda process, pids=pids: process.pid in pids)
        return " "

    remaining = _TAG_TOKEN.sub(replace, query or "")
    return " ".join(remaining.split()), predicates
