"""Filtering and tokenized search over collected resources and processes.

Kept free of Textual imports so the matching rules are unit-testable in isolation
and can be reused by any view.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ..models import ProcessRecord, ResourceRecord

__all__ = [
    "ResourceFilter",
    "filter_resources",
    "search_processes",
    "search_resources",
    "tokenize",
]


_TERM = re.compile(r'"[^"]+"|\S+')
_KV = re.compile(r"^(?P<key>[a-z]+)=(?P<value>.+)$", re.IGNORECASE)

_SUPPORTED_KEYS = frozenset(
    {
        "fd",
        "type",
        "proto",
        "protocol",
        "state",
        "port",
        "lport",
        "rport",
        "lip",
        "rip",
        "user",
        "pid",
        "name",
        "path",
    }
)


def tokenize(query: str) -> list[str]:
    """Split a query into tokens, honouring double-quoted phrases.

    Quotes are stripped from the returned tokens so a phrase matches the same
    text a user sees in the table (a quoted path must not require the quotes to
    be present in the haystack).
    """
    tokens: list[str] = []
    for match in _TERM.findall(query or ""):
        if match.startswith('"') and match.endswith('"') and len(match) >= 2:
            match = match[1:-1]
        if match:
            tokens.append(match)
    return tokens


@dataclass(slots=True)
class ResourceFilter:
    """Explicit filter controls for the resource browser.

    Every field is optional. An empty value means "no constraint" — never "match
    nothing" and never a silent zero.
    """

    protocol: str | None = None
    state: str | None = None
    family: str | None = None
    local_port: int | None = None
    remote_port: int | None = None
    local_ip: str | None = None
    remote_ip: str | None = None
    resource_type: str | None = None
    listening_only: bool = False

    def is_active(self) -> bool:
        return any(
            value not in (None, "", False)
            for value in (
                self.protocol,
                self.state,
                self.family,
                self.local_port,
                self.remote_port,
                self.local_ip,
                self.remote_ip,
                self.resource_type,
                self.listening_only,
            )
        )

    def describe(self) -> str:
        """Short human readable summary for the status bar."""
        parts: list[str] = []
        if self.protocol:
            parts.append(f"proto={self.protocol}")
        if self.state:
            parts.append(f"state={self.state}")
        if self.family:
            parts.append(f"family={self.family}")
        if self.local_port is not None:
            parts.append(f"lport={self.local_port}")
        if self.remote_port is not None:
            parts.append(f"rport={self.remote_port}")
        if self.local_ip:
            parts.append(f"lip={self.local_ip}")
        if self.remote_ip:
            parts.append(f"rip={self.remote_ip}")
        if self.resource_type:
            parts.append(f"type={self.resource_type}")
        if self.listening_only:
            parts.append("listening")
        return " ".join(parts)


def _apply_filter(record: ResourceRecord, criteria: ResourceFilter) -> bool:
    if criteria.protocol and record.protocol.upper() != criteria.protocol.upper():
        return False
    if criteria.state and record.state.upper() != criteria.state.upper():
        return False
    if criteria.family and record.resource_type.upper() != criteria.family.upper():
        return False
    if criteria.resource_type and record.resource_type.upper() != criteria.resource_type.upper():
        return False
    if criteria.listening_only and not record.is_listening:
        return False
    if criteria.local_port is not None and record.local.port != criteria.local_port:
        return False
    if criteria.remote_port is not None and record.remote.port != criteria.remote_port:
        return False
    if criteria.local_ip and criteria.local_ip not in (record.local.host or ""):
        return False
    return not (criteria.remote_ip and criteria.remote_ip not in (record.remote.host or ""))


def filter_resources(
    records: Iterable[ResourceRecord],
    criteria: ResourceFilter | None = None,
) -> list[ResourceRecord]:
    """Apply an explicit filter. Does not deduplicate — callers do that first."""
    if criteria is None or not criteria.is_active():
        return list(records)
    return [record for record in records if _apply_filter(record, criteria)]


def _token_hits_record(record: ResourceRecord, token: str) -> bool:
    key_match = _KV.match(token)
    if key_match:
        key = key_match.group("key").lower()
        value = key_match.group("value").strip().lower().strip('"')
        if key not in _SUPPORTED_KEYS:
            return value in record.search_blob
        return _field_hits(record, key, value)
    return token.lower() in record.search_blob


def _field_hits(record: ResourceRecord, key: str, value: str) -> bool:
    match key:
        case "fd":
            return value in record.fd.lower() or any(
                value in alias.lower() for alias in record.aliases
            )
        case "type":
            return value in record.resource_type.lower()
        case "proto" | "protocol":
            return value in record.protocol.lower()
        case "state":
            return value in record.state.lower()
        case "port" | "lport":
            return value.isdigit() and record.local.port == int(value)
        case "rport":
            return value.isdigit() and record.remote.port == int(value)
        case "lip":
            return value in (record.local.host or "").lower()
        case "rip":
            return value in (record.remote.host or "").lower()
        case "user":
            return value in record.user.lower()
        case "pid":
            return value.isdigit() and record.pid == int(value)
        case "name" | "path":
            return value in record.name.lower() or value in record.raw_name.lower()
        case _:
            return value in record.search_blob


def search_resources(
    records: Sequence[ResourceRecord],
    query: str,
    *,
    criteria: ResourceFilter | None = None,
) -> list[ResourceRecord]:
    """Filter then search. Supports ``port=8080``, ``state=LISTEN`` style terms."""
    candidates = filter_resources(records, criteria)
    tokens = tokenize(query)

    if not tokens:
        return list(candidates)

    free_tokens = [token for token in tokens if not _KV.match(token)]
    key_tokens = [token for token in tokens if _KV.match(token)]

    results: list[ResourceRecord] = []
    for record in candidates:
        if free_tokens and not all(token.lower() in record.search_blob for token in free_tokens):
            continue
        if key_tokens and not all(_token_hits_record(record, token) for token in key_tokens):
            continue
        results.append(record)
    return results


def search_processes(
    processes: Sequence[ProcessRecord],
    query: str,
) -> list[ProcessRecord]:
    """Search by name, PID, PPID, username, exe and full command line."""
    tokens = tokenize(query)
    if not tokens:
        return list(processes)

    results: list[ProcessRecord] = []
    for process in processes:
        blob = process.search_blob
        if all(token.lower() in blob for token in tokens):
            results.append(process)
    return results
