"""Normalized data records shared across collectors, services, screens and widgets.

These models are deliberately free of Textual and psutil imports so they can be
constructed directly inside unit tests without touching the operating system.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, replace
from typing import Any

__all__ = [
    "AddressFamily",
    "CollectionResult",
    "Endpoint",
    "ParseIssue",
    "ParseIssueRecord",
    "PermissionState",
    "ProcessRecord",
    "ResourceCategory",
    "ResourceRecord",
    "SocketState",
    "TransportProtocol",
    "format_bytes",
    "format_percent",
    "split_endpoint",
]


class AddressFamily(enum.StrEnum):
    """Address family as reported by ``lsof`` field output (TYPE field).

    macOS ``lsof`` reports IPv4/IPv6 capitalised but ``unix`` in lower case, so
    lookups must be case-insensitive.
    """

    IPV4 = "IPv4"
    IPV6 = "IPv6"
    UNIX = "unix"
    UNKNOWN = "Unknown"

    @property
    def is_inet(self) -> bool:
        return self in (AddressFamily.IPV4, AddressFamily.IPV6)

    @classmethod
    def parse(cls, value: str) -> AddressFamily:
        """Case-insensitive parse of an lsof TYPE value."""
        normalized = (value or "").strip()
        for member in cls:
            if member.value.lower() == normalized.lower():
                return member
        return cls.UNKNOWN


class TransportProtocol(enum.StrEnum):
    """Transport protocol reported by ``lsof`` field output (PROTO field)."""

    TCP = "TCP"
    UDP = "UDP"
    UNKNOWN = "-"

    @property
    def is_inet(self) -> bool:
        return self in (TransportProtocol.TCP, TransportProtocol.UDP)


class SocketState(enum.StrEnum):
    """TCP connection states, plus the states we can observe for non-TCP sockets."""

    LISTEN = "LISTEN"
    ESTABLISHED = "ESTABLISHED"
    CLOSE_WAIT = "CLOSE_WAIT"
    SYN_SENT = "SYN_SENT"
    SYN_RECV = "SYN_RECV"
    FIN_WAIT_1 = "FIN_WAIT_1"
    FIN_WAIT_2 = "FIN_WAIT_2"
    TIME_WAIT = "TIME_WAIT"
    CLOSING = "CLOSING"
    LAST_ACK = "LAST_ACK"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "-"


class ResourceCategory(enum.StrEnum):
    """High level grouping used by the process detail screen."""

    NETWORK = "network"
    LISTENERS = "listeners"
    FILES = "files"
    UNIX = "unix"
    ALL = "all"

    @property
    def label(self) -> str:
        return {
            ResourceCategory.NETWORK: "Network connections",
            ResourceCategory.LISTENERS: "TCP listening ports",
            ResourceCategory.FILES: "Files, directories & pipes",
            ResourceCategory.UNIX: "UNIX sockets & other",
            ResourceCategory.ALL: "All open descriptors",
        }[self]


class PermissionState(enum.StrEnum):
    """Whether a collection succeeded, partially succeeded, or was denied."""

    OK = "ok"
    PARTIAL = "partial"
    DENIED = "denied"
    FAILED = "failed"
    GONE = "gone"


class ParseIssue(enum.StrEnum):
    """Recoverable parsing problems surfaced in the UI instead of being swallowed."""

    MISSING_STATE = "missing-state"
    TRUNCATED_RECORD = "truncated-record"
    UNKNOWN_TYPE = "unknown-type"


@dataclass(frozen=True, slots=True)
class Endpoint:
    """A parsed ``host:port`` pair from an ``lsof`` NAME field.

    ``None`` means *not determinable*, which is meaningfully different from
    zero or an empty string. Callers must never substitute a fake value.
    """

    host: str | None
    port: int | None

    @property
    def has_port(self) -> bool:
        return self.port is not None

    def __str__(self) -> str:
        if self.host is None and self.port is None:
            return ""
        if self.host is None:
            return f":{self.port}"
        if self.port is None:
            return self.host
        return f"{self.host}:{self.port}"


def split_endpoint(value: str) -> Endpoint:
    """Split an ``lsof`` endpoint string into host and port.

    Handles the shapes macOS ``lsof -nP -F`` actually emits:

    * ``127.0.0.1:8931``            -> host + port
    * ``*:8000``                    -> wildcard host + port
    * ``[::1]:443``                 -> bracketed IPv6 host + port
    * ``240.0.0.2:64277->139.162.187.236:123`` -> handled by the caller, not here
    * ``[::ffff:127.0.0.1]:5432``   -> IPv4-mapped IPv6
    * ``/var/run/sock``             -> not an endpoint (UNIX socket), returns empty

    Never guesses. If a port cannot be determined the port stays ``None``.
    """
    text = (value or "").strip()
    if not text:
        return Endpoint(host=None, port=None)

    if text.startswith("["):
        closing = text.find("]")
        if closing == -1:
            return Endpoint(host=text, port=None)
        host = text[1:closing]
        remainder = text[closing + 1 :]
        if remainder.startswith(":") and remainder[1:].isdigit():
            return Endpoint(host=host, port=int(remainder[1:]))
        return Endpoint(host=host, port=None)

    if text.count(":") > 1:
        return Endpoint(host=text, port=None)

    if text.startswith("/"):
        return Endpoint(host=None, port=None)

    if ":" not in text:
        return Endpoint(host=text, port=None)

    host, _, port_text = text.rpartition(":")
    if port_text.isdigit():
        return Endpoint(host=host, port=int(port_text))

    return Endpoint(host=text, port=None)


@dataclass(slots=True)
class ProcessRecord:
    """A single row of the process browser."""

    pid: int
    name: str
    ppid: int | None = None
    user: str | None = None
    cpu_percent: float | None = None
    memory_percent: float | None = None
    memory_rss: int | None = None
    num_threads: int | None = None
    status: str | None = None
    create_time: float | None = None
    elapsed: str | None = None
    exe: str | None = None
    cmdline: list[str] = field(default_factory=list)

    @property
    def command_line(self) -> str:
        """Full command line, or a clear unavailable marker when unreadable."""
        if not self.cmdline:
            return self.exe or self.name
        return " ".join(self.cmdline)

    @property
    def search_blob(self) -> str:
        """Lower-cased haystack used by the process search input."""
        parts: list[Any] = [
            self.name,
            str(self.pid),
            self.ppid,
            self.user,
            self.exe,
            self.status,
            *self.cmdline,
        ]
        return " ".join(str(part) for part in parts if part).lower()

    @property
    def is_system(self) -> bool:
        return (self.pid or 0) <= 2


@dataclass(frozen=True, slots=True)
class ResourceRecord:
    """One open resource (file, socket, pipe) held by a process.

    ``fd`` is *excluded* from the deduplication key by design: the same socket
    is frequently reachable through several descriptors and the user only wants
    to see it once. The full list of aliases is retained in ``aliases`` so no
    diagnostic information is lost.
    """

    command: str
    pid: int
    user: str
    fd: str
    resource_type: str
    protocol: str
    name: str
    state: str
    local: Endpoint = field(default_factory=lambda: Endpoint(None, None))
    remote: Endpoint = field(default_factory=lambda: Endpoint(None, None))
    device: str | None = None
    inode: int | None = None
    size: int | None = None
    mode: str | None = None
    link_count: int | None = None
    uid: int | None = None
    pgid: int | None = None
    issues: tuple[ParseIssue, ...] = ()
    raw_name: str = ""
    aliases: tuple[str, ...] = ()

    @property
    def family(self) -> AddressFamily:
        return AddressFamily.parse(self.resource_type)

    @property
    def transport(self) -> TransportProtocol:
        normalized = (self.protocol or "").strip().upper()
        for member in TransportProtocol:
            if member.value.upper() == normalized:
                return member
        return TransportProtocol.UNKNOWN

    @property
    def socket_state(self) -> SocketState:
        try:
            return SocketState(self.state)
        except ValueError:
            return SocketState.UNKNOWN

    @property
    def is_inet(self) -> bool:
        return self.family.is_inet and self.transport.is_inet

    @property
    def is_listening(self) -> bool:
        return self.is_inet and self.socket_state is SocketState.LISTEN

    @property
    def is_unix_socket(self) -> bool:
        return self.family is AddressFamily.UNIX

    @property
    def display_name(self) -> str:
        """Prefer the parsed local/remote pair, fall back to the raw value."""
        if self.is_inet:
            local = str(self.local)
            remote = str(self.remote)
            if local and remote:
                return f"{local} → {remote}"
            return local or remote or self.raw_name or self.name
        return self.name

    @property
    def search_blob(self) -> str:
        parts = [
            self.command,
            str(self.pid),
            self.user,
            self.fd,
            self.resource_type,
            self.protocol,
            self.name,
            self.state,
            self.raw_name,
            str(self.local.host or ""),
            str(self.local.port or ""),
            str(self.remote.host or ""),
            str(self.remote.port or ""),
            *(issue.value for issue in self.issues),
        ]
        return " ".join(part for part in parts if part).lower()

    def with_aliases(self, aliases: list[str]) -> ResourceRecord:
        """Return a copy carrying every FD that resolved to this same resource."""
        return replace(self, aliases=tuple(aliases))


@dataclass(frozen=True, slots=True)
class ParseIssueRecord:
    """A non-fatal parsing problem worth surfacing in the UI."""

    kind: ParseIssue
    detail: str
    line_number: int | None = None


@dataclass(slots=True)
class CollectionResult:
    """Outcome of an ``lsof`` collection run."""

    records: list[ResourceRecord] = field(default_factory=list)
    permission: PermissionState = PermissionState.OK
    duration_seconds: float = 0.0
    collected_at: float = 0.0
    stderr: str = ""
    error: str | None = None
    issues: list[ParseIssueRecord] = field(default_factory=list)
    truncated: bool = False

    @property
    def network_count(self) -> int:
        return sum(1 for record in self.records if record.is_inet)

    @property
    def listener_count(self) -> int:
        return sum(1 for record in self.records if record.is_listening)

    @property
    def file_count(self) -> int:
        return sum(1 for record in self.records if not record.is_inet)

    def warning(self) -> str | None:
        """Human readable explanation of what could not be collected, if anything."""
        if self.permission is PermissionState.DENIED:
            return (
                "Permission denied — the operating system hid this process's descriptors. "
                "Run with elevated privileges yourself if you need the full inventory."
            )
        if self.permission is PermissionState.PARTIAL:
            return "Partial results — some descriptors were hidden by macOS permissions."
        if self.error:
            return f"Collection failed: {self.error}"
        return None


_UNAVAILABLE = "—"


def format_bytes(value: int | None) -> str:
    """Human readable byte size, or an explicit unavailable marker."""
    if value is None:
        return _UNAVAILABLE
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return _UNAVAILABLE


def format_percent(value: float | None) -> str:
    """Percent with one decimal, or an explicit unavailable marker (never 0.0)."""
    if value is None:
        return _UNAVAILABLE
    return f"{value:.1f}%"
