"""Dedicated resource search across a process's collected resources."""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import Any, ClassVar

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, DataTable, Input, Static

from ..config import current_theme, with_tokens
from ..models import ProcessRecord, ResourceRecord
from ..services.deduplication import deduplicate
from ..services.resource_search import (
    ResourceFilter,
    filter_resources,
    search_resources,
)
from ..widgets.status_bar import StatusBar
from .common import AppHeader, EmptyState, ResponsiveMixin, ScreenBanner

log = logging.getLogger(__name__)

__all__ = ["ResourceBrowserScreen", "ResourceDetailScreen"]


def _filter_from_tokens(query: str) -> tuple[list[str], ResourceFilter]:
    """Split a raw query into free-text terms and structured filter fields.

    ``port=8080`` and ``state=LISTEN`` become filter criteria; everything else is
    treated as free text.
    """
    terms: list[str] = []
    criteria = ResourceFilter()
    for token in query.split():
        if "=" not in token:
            terms.append(token)
            continue
        key, _, value = token.partition("=")
        key = key.strip().lower()
        value = value.strip()
        if key in ("port", "lport") and value.isdigit():
            criteria.local_port = int(value)
        elif key == "rport" and value.isdigit():
            criteria.remote_port = int(value)
        elif key in ("proto", "protocol"):
            criteria.protocol = value
        elif key == "state":
            criteria.state = value
        elif key in ("family", "type"):
            criteria.family = value
        elif key == "lip":
            criteria.local_ip = value
        elif key == "rip":
            criteria.remote_ip = value
        elif key == "listen" and value.lower() in ("1", "true", "yes"):
            criteria.listening_only = True
        else:
            terms.append(token)
    return terms, criteria


class ResourceBrowserScreen(ResponsiveMixin, Screen[None]):
    """Searchable browser over every resource collected for one process."""

    BINDINGS: ClassVar = [
        ("q", "quit_app", "Quit"),
        ("escape", "close_search", "Back / clear"),
        ("backspace", "go_back", "Back"),
        ("slash", "focus_search", "Search"),
        ("ctrl+f", "focus_search", "Search"),
    ]

    DEFAULT_CSS = with_tokens("""
    ResourceBrowserScreen {
        height: 1fr;
    }
    ResourceBrowserScreen #browser-search-row {
        height: 3;
        padding: 0 1;
    }
    ResourceBrowserScreen #browser-empty {
        display: none;
    }
    ResourceBrowserScreen.-empty #browser-empty {
        display: block;
    }
    """)

    def __init__(
        self,
        records: list[ResourceRecord],
        process: ProcessRecord,
        parent_screen: Any,
        *,
        initial_query: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)

        self.records = deduplicate(records)
        self.process = process
        self.parent_screen = parent_screen
        self._initial_query = initial_query
        self._shown: list[ResourceRecord] = list(self.records)
        self._collapsed = len(records) - len(self.records)

    def compose(self) -> ComposeResult:
        yield AppHeader(id="app-header")
        yield ScreenBanner(
            ("Processes", f"{self.process.name} · {self.process.pid}", "Resource search"),
            id="banner",
        )
        with Horizontal(id="browser-search-row"):
            yield Input(
                placeholder="Search IP, port, state, path, fd…  try  port=8080  state=LISTEN",
                id="browser-search",
            )
        yield DataTable(id="browser-grid", zebra_stripes=True, cursor_type="row")
        yield EmptyState(
            "No matching resources",
            "Nothing in this process matched the search.",
            "Press  Esc  to clear the search and go back.",
            id="browser-empty",
            classes="empty-state",
        )
        yield StatusBar(id="status-bar")

    def on_mount(self) -> None:
        self._apply_breakpoints(self.size.width)
        table = self.query_one("#browser-grid", DataTable)
        table.add_columns("FD", "TYPE", "PROTO", "RESOURCE", "STATE", "FLAGS")

        widths = (8, 8, 5, 56, 12, 8)
        for column, width in zip(table.columns.values(), widths, strict=False):
            column.width = width

        search = self.query_one("#browser-search", Input)
        if self._initial_query:
            search.value = self._initial_query
        self._apply(search.value)
        search.focus()

    def _apply(self, query: str) -> None:
        terms, criteria = _filter_from_tokens(query)
        filtered = filter_resources(self.records, criteria)
        self._shown = search_resources(filtered, " ".join(terms))

        table = self.query_one("#browser-grid", DataTable)
        table.clear()
        for record in self._shown:
            table.add_row(*self._cells(record), key=str(id(record)))

        if not self._shown:
            self.add_class("-empty")
            self.query_one("#browser-empty", EmptyState).set_content(
                "No matching resources",
                f"No resource of {len(self.records)} matched '{query}'.",
                "Press  Esc  to clear the search.",
            )
        else:
            self.remove_class("-empty")

        self._update_status(query)

    def _cells(self, record: ResourceRecord) -> list[Text]:
        flags: list[str] = []
        if record.is_listening:
            flags.append("LISTEN")
        if record.issues:
            flags.append("RAW")
        if record.aliases:
            flags.append(f"+{len(record.aliases) - 1}FD")
        return [
            Text(record.fd, style=current_theme().caramel),
            Text(record.resource_type, style=current_theme().purple),
            Text(record.protocol, style=current_theme().muted),
            Text(record.display_name, style=current_theme().cotton),
            Text(record.state, style=current_theme().state_color(record.state)),
            Text(" ".join(flags), style=current_theme().amber),
        ]

    def _update_status(self, query: str) -> None:
        with suppress(Exception):
            self._apply_status(query)

    def _apply_status(self, query: str) -> None:
        status = self.query_one("#status-bar", StatusBar)
        if query.strip():
            status.set_counts(f"{len(self._shown)} of {len(self.records)} resources match")
        else:
            status.set_counts(
                f"{len(self.records)} unique resources  ·  {self._collapsed} duplicate(s) collapsed"
            )
        status.set_keys("  /  search  ⏎  detail  Esc  clear/back  Q  quit  ")

    def action_close_search(self) -> None:
        search = self.query_one("#browser-search", Input)
        if search.value:
            search.value = ""
            self._apply("")
            return
        self.app.pop_screen()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_quit_app(self) -> None:
        self.app.exit()

    def action_focus_search(self) -> None:
        self.query_one("#browser-search", Input).focus()

    @on(Input.Changed, "#browser-search")
    def _on_changed(self, event: Input.Changed) -> None:
        event.stop()
        self._apply(event.value)

    @on(Input.Submitted, "#browser-search")
    def _on_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.query_one("#browser-grid", DataTable).focus()

    @on(DataTable.RowSelected, "#browser-grid")
    def _on_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        record = self._selected()
        if record is None:
            return
        self.app.push_resource_detail(record, self.process, self)

    @on(Button.Pressed, "#back-button")
    def _on_back(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_go_back()

    def _selected(self) -> ResourceRecord | None:
        table = self.query_one("#browser-grid", DataTable)
        row = table.cursor_row
        if row is None or not (0 <= row < len(self._shown)):
            return None
        return self._shown[row]

    def on_key(self, event: Any) -> None:
        if event.key != "right":
            return
        if isinstance(self.focused, DataTable):
            event.stop()
            record = self._selected()
            if record is not None:
                self.app.push_resource_detail(record, self.process, self)


class ResourceDetailScreen(ResponsiveMixin, Screen[None]):
    """Full detail for a single resource, always including the raw ``lsof`` value."""

    BINDINGS: ClassVar = [
        ("q", "quit_app", "Quit"),
        ("escape", "go_back", "Back"),
        ("backspace", "go_back", "Back"),
        ("left", "go_back", "Back"),
    ]

    DEFAULT_CSS = with_tokens("""
    ResourceDetailScreen {
        height: 1fr;
    }
    ResourceDetailScreen #resource-detail {
        height: 1fr;
    }
    """)

    def __init__(
        self,
        record: ResourceRecord,
        process: ProcessRecord,
        parent_screen: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.record = record
        self.process = process
        self.parent_screen = parent_screen

    def compose(self) -> ComposeResult:
        yield AppHeader(id="app-header")
        yield ScreenBanner(
            (
                "Processes",
                f"{self.process.name} · {self.process.pid}",
                "Resource detail",
            ),
            id="banner",
        )
        yield Static("", id="resource-detail")
        yield StatusBar(id="status-bar")

    def on_mount(self) -> None:
        self._apply_breakpoints(self.size.width)
        self.render_detail()
        self.query_one("#status-bar", StatusBar).set_counts("Resource detail")
        self.query_one("#status-bar", StatusBar).set_keys("  ←  or  Esc  back  Q  quit  ")

    def render_detail(self) -> None:
        record = self.record
        rows: list[tuple[str, str, str]] = [
            (
                "PROCESS",
                f"{record.command}  (PID {record.pid}, user {record.user})",
                current_theme().cotton,
            ),
            ("DESCRIPTOR", record.fd, current_theme().caramel),
            ("TYPE", record.resource_type, current_theme().purple),
            ("PROTOCOL", record.protocol, current_theme().cotton),
            ("STATE", record.state, current_theme().state_color(record.state)),
            ("LOCAL", str(record.local) or "unavailable", current_theme().cyan),
            ("REMOTE", str(record.remote) or "not applicable", current_theme().blue),
            ("NAME", record.name or "unavailable", current_theme().cotton),
            ("RAW lsof NAME", record.raw_name or "unavailable", current_theme().amber),
        ]

        if record.device:
            rows.append(("DEVICE", record.device, current_theme().muted))
        if record.inode is not None:
            rows.append(("INODE", str(record.inode), current_theme().muted))
        if record.size is not None:
            rows.append(("SIZE", str(record.size), current_theme().muted))
        if record.mode:
            rows.append(("MODE", record.mode, current_theme().muted))
        if record.uid is not None:
            rows.append(("UID", str(record.uid), current_theme().muted))
        if record.gid is not None:
            rows.append(("GID", str(record.gid), current_theme().muted))

        if record.aliases:
            rows.append(("ALSO VISIBLE VIA", ", ".join(record.aliases), current_theme().amber))

        if record.issues:
            rows.append(
                (
                    "PARSING NOTES",
                    ", ".join(issue.value for issue in record.issues),
                    current_theme().orange,
                )
            )

        rendered = Text()
        for key, value, style in rows:
            rendered.append(f"{key:<18}", style=f"bold {current_theme().purple}")
            rendered.append(f"{value}\n", style=style)

        rendered.append(
            "\nDeduplication key: command + pid + user + name + state "
            "(file descriptor intentionally excluded)",
            style=current_theme().muted,
        )

        self.query_one("#resource-detail", Static).update(rendered)

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_quit_app(self) -> None:
        self.app.exit()

    @on(Button.Pressed, "#back-button")
    def _on_back(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_go_back()
