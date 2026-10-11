"""``lsof`` collection and structured field parsing.

Design notes
------------
* We always request **field output** (``-F``) with an explicit field set so we
  never have to guess column positions out of a whitespace-aligned table.
* Field records use a leading-character tag, and file records are delimited by
  the ``f`` (file descriptor) tag. The ``p`` (process) tag starts a new process.
* The TCP state arrives as ``TST=ESTABLISHED``. Because ``T`` is also used for
  unrelated socket queue counters (``TQR=0``/``TQS=0``), only the ``ST`` sub-field
  is interpreted; anything else is recorded as a parse issue rather than being
  allowed to leak into the next descriptor's state.
* State is stored **per file record**. A stale state from a previous descriptor
  must never be inherited by the next one, which was a real bug in the original
  Bash implementation's single-pass parser.
* stdout and stderr are captured separately so a permission warning can be
  reported honestly instead of being mixed into the data stream.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from dataclasses import dataclass, field

from ..models import (
    CollectionResult,
    Endpoint,
    ParseIssue,
    ParseIssueRecord,
    PermissionState,
    ResourceRecord,
    split_endpoint,
)

log = logging.getLogger(__name__)

__all__ = ["FIELD_ARGUMENT", "LsofCollector", "LsofFieldParser", "parse_lsof_output"]


FIELD_ARGUMENT = "pcfntPTDismlug"


_UNAVAILABLE = "-"


class LsofError(RuntimeError):
    """Raised when lsof itself cannot be executed."""


@dataclass(slots=True)
class _FileState:
    """Mutable accumulator for the file record currently being parsed."""

    fd: str = ""
    resource_type: str = ""
    protocol: str = ""
    name: str = ""
    state: str = ""
    device: str | None = None
    inode: int | None = None
    size: int | None = None
    mode: str | None = None
    link_count: int | None = None
    uid: int | None = None
    gid: int | None = None
    issues: list[ParseIssue] = field(default_factory=list)


@dataclass(slots=True)
class _ProcessState:
    pid: int = 0
    command: str = ""
    user: str = ""


class LsofFieldParser:
    """Incremental parser for ``lsof -F`` output.

    Feed it with :meth:`feed` and read the finished records with :meth:`records`.
    The parser is deliberately tolerant: a malformed line is reported through
    :attr:`issues` and parsing continues with the next line.
    """

    def __init__(self, *, default_user: str = "") -> None:
        self._default_user = default_user
        self._process = _ProcessState()
        self._file: _FileState | None = None
        self._records: list[ResourceRecord] = []
        self._issues: list[ParseIssueRecord] = []
        self._line_number = 0
        self.saw_process_tag = False

    def _flush_file(self) -> None:
        """Convert the pending file record into a ResourceRecord and reset state."""
        current = self._file

        self._file = None
        if current is None:
            return

        if not current.name:
            current.issues.append(ParseIssue.TRUNCATED_RECORD)

        if not current.resource_type:
            current.issues.append(ParseIssue.UNKNOWN_TYPE)
            current.resource_type = _UNAVAILABLE

        if current.protocol in ("TCP", "UDP") and not current.state:
            current.issues.append(ParseIssue.MISSING_STATE)

        local, remote, endpoint_issue = _parse_socket_name(current.name)
        if endpoint_issue is not None:
            current.issues.append(endpoint_issue)

        self._records.append(
            ResourceRecord(
                command=self._process.command or "unknown",
                pid=self._process.pid,
                user=self._process.user or self._default_user or _UNAVAILABLE,
                fd=current.fd,
                resource_type=current.resource_type,
                protocol=current.protocol or _UNAVAILABLE,
                name=current.name,
                state=current.state or _UNAVAILABLE,
                local=local,
                remote=remote,
                device=current.device,
                inode=current.inode,
                size=current.size,
                mode=current.mode,
                link_count=current.link_count,
                uid=current.uid,
                gid=current.gid,
                issues=tuple(dict.fromkeys(current.issues)),
                raw_name=current.name,
            )
        )

    def feed(self, line: str) -> None:
        """Consume a single line of ``lsof -F`` output."""
        self._line_number += 1
        stripped = line.rstrip("\n")
        if not stripped:
            return

        tag = stripped[0]
        value = stripped[1:]

        if tag == "p":
            self._flush_file()
            self._file = None
            self.saw_process_tag = True
            pid_text = value.strip()
            if pid_text.isdigit():
                self._process = _ProcessState(pid=int(pid_text))
            else:
                self._issues.append(
                    ParseIssueRecord(
                        ParseIssue.TRUNCATED_RECORD,
                        f"Non-numeric PID tag: {value!r}",
                        self._line_number,
                    )
                )
                self._process = _ProcessState()
            return

        if tag == "c":
            self._process.command = value.strip()
            return

        if tag == "f":
            self._flush_file()
            self._file = _FileState(fd=value.strip())
            return

        if self._file is None:
            return

        current = self._file
        match tag:
            case "t":
                current.resource_type = value.strip()
            case "P":
                current.protocol = value.strip()
            case "n":
                current.name = value
            case "T":
                state_field, separator, state_value = value.partition("=")
                if separator and state_field == "ST":
                    current.state = state_value.strip()
            case "D":
                current.device = value.strip()
            case "i":
                current.inode = int(value) if value.strip().isdigit() else None
            case "s":
                current.size = int(value) if value.strip().isdigit() else None
            case "m":
                current.mode = value.strip()
            case "l":
                current.link_count = int(value) if value.strip().isdigit() else None
            case "u":
                current.uid = int(value) if value.strip().isdigit() else None
            case "g":
                current.gid = int(value) if value.strip().isdigit() else None
            case _:
                pass

    def feed_all(self, text: str) -> None:
        for line in text.splitlines():
            self.feed(line)

    def close(self) -> list[ResourceRecord]:
        """Flush the trailing record and return everything parsed so far."""
        self._flush_file()
        return list(self._records)

    @property
    def records(self) -> list[ResourceRecord]:
        return self._records

    @property
    def issues(self) -> list[ParseIssueRecord]:
        return self._issues


def _parse_socket_name(name: str) -> tuple[Endpoint, Endpoint, ParseIssue | None]:
    """Split an ``lsof`` NAME field into local and remote endpoints.

    Recognised shapes::

        127.0.0.1:8931                          local only
        *:8000                                  wildcard local listener
        240.0.0.2:64277->139.162.187.236:123    local -> remote
        [::1]:443                               bracketed IPv6
        [::1]:443->[::2]:52000                  bracketed IPv6 both ends
    """
    if not name:
        return Endpoint(None, None), Endpoint(None, None), None

    if "->" not in name:
        return split_endpoint(name), Endpoint(None, None), None

    left, _, right = name.partition("->")
    local = split_endpoint(left.strip())
    remote = split_endpoint(right.strip())
    return local, remote, None


def parse_lsof_output(
    text: str,
    *,
    default_user: str = "",
) -> tuple[list[ResourceRecord], list[ParseIssueRecord]]:
    """Parse complete ``lsof -F`` output. Convenience wrapper used by tests."""
    parser = LsofFieldParser(default_user=default_user)
    parser.feed_all(text)
    records = parser.close()
    return records, list(parser.issues)


class LsofCollector:
    """Runs ``lsof`` safely and returns parsed, honest results.

    Never uses ``shell=True``, never escalates privileges on its own, and always
    separates stderr from stdout.
    """

    def __init__(self, *, binary: str = "lsof", timeout: float = 20.0) -> None:
        self.binary = binary
        self.timeout = timeout

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def missing_binary_error(self) -> str | None:
        if self.available():
            return None
        return (
            f"'{self.binary}' was not found on PATH. Ports Explorer needs it to list "
            "open files, sockets and network state. Install it with: brew install lsof"
        )

    def build_command(self, pid: int | None = None) -> list[str]:
        """Argument array for a collection run. No shell involved, ever."""
        command = [self.binary, "-nP", "-F" + FIELD_ARGUMENT]
        if pid is not None:
            command += ["-p", str(pid)]
        return command

    async def collect(self, pid: int | None = None) -> CollectionResult:
        """Run lsof off the event loop with a timeout, and parse the result."""
        started = time.monotonic()
        missing = self.missing_binary_error()
        if missing is not None:
            return CollectionResult(
                permission=PermissionState.FAILED,
                error=missing,
                duration_seconds=0.0,
                collected_at=time.time(),
            )

        command = self.build_command(pid)
        log.info("running lsof: %s", command)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            return CollectionResult(
                permission=PermissionState.FAILED,
                error=f"Could not execute lsof: {exc}",
                collected_at=time.time(),
                duration_seconds=time.monotonic() - started,
            )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=self.timeout
            )
        except TimeoutError:
            with _suppress_process_errors():
                process.kill()
                await process.wait()
            return CollectionResult(
                permission=PermissionState.FAILED,
                error=f"lsof timed out after {self.timeout:.0f}s",
                collected_at=time.time(),
                duration_seconds=time.monotonic() - started,
                truncated=True,
            )
        except asyncio.CancelledError:
            with _suppress_process_errors():
                process.kill()
                await process.wait()
            raise

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        return_code = process.returncode

        user, alive = await _resolve_user(pid)
        records, issues = parse_lsof_output(stdout, default_user=user)
        permission = _classify_permission(return_code, stderr, records, alive)

        result = CollectionResult(
            records=records,
            permission=permission,
            duration_seconds=time.monotonic() - started,
            collected_at=time.time(),
            stderr=stderr.strip(),
            issues=issues,
        )

        if permission is PermissionState.FAILED and not records:
            result.error = _first_error_line(stderr) or f"lsof exited with status {return_code}"
        return result


def _first_error_line(stderr: str) -> str | None:
    for line in stderr.splitlines():
        line = line.strip()
        if line:
            return line
    return None


def _classify_permission(
    return_code: int | None,
    stderr: str,
    records: list[ResourceRecord],
    process_exists: bool,
) -> PermissionState:
    """Decide how much of the inventory is trustworthy.

    macOS ``lsof`` exits 1 with no rows and no stderr both when the PID is gone
    and when the process belongs to another user; only a liveness check can tell
    them apart, so the caller supplies ``process_exists``.
    """
    lowered = stderr.lower()
    denied_markers = ("permission denied", "can't be accessed", "cannot be accessed")
    has_denial = any(marker in lowered for marker in denied_markers)

    if return_code not in (0, 1) and not records:
        return PermissionState.FAILED
    if has_denial and records:
        return PermissionState.PARTIAL
    if has_denial:
        return PermissionState.DENIED
    if not records:
        if not process_exists:
            return PermissionState.GONE
        return PermissionState.DENIED
    if return_code == 1:
        return PermissionState.PARTIAL
    return PermissionState.OK


async def _resolve_user(pid: int | None) -> tuple[str, bool]:
    """Best-effort username plus a liveness flag for the inspected process."""
    if pid is None:
        return "", True
    return await asyncio.to_thread(_resolve_user_blocking, pid)


def _resolve_user_blocking(pid: int) -> tuple[str, bool]:
    try:
        import psutil

        process = psutil.Process(pid)
        username = process.username() or ""
    except Exception:
        return "", False
    return username, True


class _suppress_process_errors:
    """Tiny context manager: killing an already-dead subprocess is not an error."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:
        return exc_type is not None and issubclass(exc_type, (ProcessLookupError, OSError))
