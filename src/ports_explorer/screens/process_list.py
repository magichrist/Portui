"""The process browser — the application's entry screen."""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import Any, ClassVar

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import DataTable, Input

from ..collectors.processes import ProcessCollector
from ..config import current_theme, get_settings, with_tokens
from ..models import ProcessRecord, format_bytes, format_percent
from ..services.resource_search import search_processes
from ..widgets.status_bar import StatusBar
from .common import (
    AppHeader,
    AutoUpdateMixin,
    EmptyState,
    KillMixin,
    ResponsiveMixin,
    format_interval,
)

log = logging.getLogger(__name__)

__all__ = ["ProcessListScreen"]

COLUMNS = [
    ("PROCESS", 22),
    ("PID", 7),
    ("PPID", 7),
    ("USER", 12),
    ("CPU", 7),
    ("MEM", 8),
    ("RUNTIME", 10),
    ("STATUS", 10),
    ("EXECUTABLE", 42),
]


class ProcessListScreen(ResponsiveMixin, AutoUpdateMixin, KillMixin, Screen[None]):
    """Searchable, sortable list of every process the user can see."""

    BINDINGS: ClassVar = [
        ("q", "quit_app", "Quit"),
        ("r", "toggle_auto_update", "Auto-update"),
        ("k", "kill_process", "Terminate"),
        ("slash", "focus_search", "Search"),
        ("ctrl+f", "focus_search", "Search"),
        ("escape", "clear_search", "Clear"),
        ("backspace", "go_back", "Back"),
        ("question_mark", "show_help", "Help"),
    ]

    DEFAULT_CSS = with_tokens("""
    ProcessListScreen {
        height: 1fr;
    }
    ProcessListScreen #process-search-row {
        height: 3;
        padding: 0 1;
    }
    ProcessListScreen.-narrow #process-grid {
        scrollbar-size-vertical: 1;
    }
    ProcessListScreen #process-empty {
        display: none;
    }
    ProcessListScreen.-empty #process-empty {
        display: block;
    }
    """)

    def __init__(self, collector: ProcessCollector, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.collector = collector
        self._all: list[ProcessRecord] = []
        self._filtered: list[ProcessRecord] = []
        self._cursor_pid: int | None = None
        self._loading = False
        self._first_load = True

    def compose(self) -> ComposeResult:
        yield AppHeader(id="app-header")
        with Horizontal(id="process-search-row"):
            yield Input(
                placeholder="Search name, PID, user, path or command…",
                id="process-search",
            )
        yield DataTable(id="process-grid", zebra_stripes=True, cursor_type="row")
        yield EmptyState(
            "No processes found",
            "Nothing matched the current search.",
            "Press  R  to toggle auto-update, or Esc to clear the search.",
            id="process-empty",
            classes="empty-state",
        )
        yield StatusBar(id="status-bar")

    def on_mount(self) -> None:
        self._apply_breakpoints(self.size.width)
        self._build_columns()
        self.set_key_hints()
        self.collect_processes(initial=True)
        self._start_auto_update()

    def _build_columns(self) -> None:
        table = self.query_one("#process-grid", DataTable)
        table.clear(columns=True)
        # Add each column as an explicit (label, key) pair. Plain labels get an
        # auto-generated key of None, which in-place cell updates cannot address.
        table.add_columns(*[(label, label) for label, _ in COLUMNS])
        self._apply_column_widths(table)

    def _apply_column_widths(self, table: DataTable | None = None) -> None:
        """Fit columns to the terminal, dropping the least important first.

        Full values stay intact in the underlying records; only the on-screen
        column width shrinks, and the widest remaining column absorbs the rest.
        """
        if table is None:
            table = self.query_one("#process-grid", DataTable)

        columns = list(table.columns.values())
        widths = [width for _, width in COLUMNS][: len(columns)]
        available = max(40, self.size.width - 6)

        while sum(widths) > available and len(widths) > 4:
            widths.pop()

        overflow = available - sum(widths)
        if overflow > 0 and widths:
            widths[-1] += overflow

        for column, width in zip(columns, widths, strict=False):
            column.width = width

    def on_resize(self, event: Any) -> None:
        super().on_resize(event)
        with suppress(Exception):
            self._apply_column_widths()

    def _default_key_hints(self) -> str:
        return "  /  search    R  auto-update    ⏎  inspect    k  terminate    Q  quit  "

    def set_key_hints(self) -> None:
        with suppress(Exception):
            self.query_one("#status-bar", StatusBar).set_keys(self._default_key_hints())

    # -- kill target ------------------------------------------------------

    def _kill_target(self) -> tuple[int, str] | None:
        """The highlighted process, or ``None`` when the list is empty."""
        table = self.query_one("#process-grid", DataTable)
        row = table.cursor_row
        if row is None or not (0 <= row < len(self._filtered)):
            return None
        process = self._filtered[row]
        return process.pid, f"{process.name} (PID {process.pid})"

    def _after_kill(self, result: Any) -> None:
        """Refresh immediately so a terminated process actually disappears."""
        if result.ok:
            self.collect_processes()

    @work(exclusive=True, group="processes")
    async def collect_processes(self, *, initial: bool = False) -> None:
        """Collect the process list without blocking the event loop."""
        self._loading = True
        self._update_status()
        try:
            processes = await self.collector.snapshot()
        except Exception as exc:
            log.exception("process collection failed")
            self._loading = False
            self._show_error(str(exc))
            return

        self._all = processes
        self.collector.store(processes)
        self._loading = False
        self._apply_query()
        if initial and processes:
            self.query_one("#process-grid", DataTable).focus()

    def _show_error(self, message: str) -> None:
        empty = self.query_one("#process-empty", EmptyState)
        empty.set_content(
            "Could not read the process list",
            message,
            "Run with --debug and check the log file for details.",
        )
        self.add_class("-empty")

    def _apply_query(self) -> None:
        query = self._current_query()
        self._filtered = search_processes(self._all, query)
        self._populate()

    def _current_query(self) -> str:
        try:
            return self.query_one("#process-search", Input).value
        except Exception:
            return ""

    def _populate(self) -> None:
        table = self.query_one("#process-grid", DataTable)
        pids = [str(process.pid) for process in self._filtered]

        # Compare against the keys the table actually holds rather than a cached
        # copy, so anything that clears the table out from under us (a theme
        # change, a column rebuild) simply falls through to the rebuild path.
        if [row.key.value for row in table.rows.values()] == pids:
            # Same rows in the same order. Repaint cells in place instead of
            # tearing down and rebuilding the whole table on every tick.
            for process in self._filtered:
                key = str(process.pid)
                for (label, _width), cell in zip(COLUMNS, self._row_cells(process), strict=True):
                    table.update_cell(key, label, cell)
        else:
            table.clear()
            for process in self._filtered:
                table.add_row(*self._row_cells(process), key=str(process.pid))

        self._restore_cursor()

        if not self._filtered:
            self.add_class("-empty")
            if not self._all:
                self.query_one("#process-empty", EmptyState).set_content(
                    "No processes visible",
                    "The operating system returned an empty process list.",
                    "Check permissions, then press R to toggle auto-update and retry.",
                )
            else:
                self.query_one("#process-empty", EmptyState).set_content(
                    "No processes found",
                    "Nothing matched the current search.",
                    "Press  Esc  to clear the search, or R to toggle auto-update.",
                )
        else:
            self.remove_class("-empty")

        self._update_status()

    def _restore_cursor(self) -> None:
        """Keep the user's selection stable across a refresh."""
        table = self.query_one("#process-grid", DataTable)
        if self._cursor_pid is None:
            return
        for index, process in enumerate(self._filtered):
            if process.pid == self._cursor_pid:
                table.move_cursor(row=index)
                return

        table.move_cursor(row=0)

    def _row_cells(self, process: ProcessRecord) -> list[Text]:
        name_style = current_theme().cyan if process.is_system else current_theme().cotton
        return [
            Text(process.name, style=name_style),
            Text(str(process.pid), style=current_theme().purple),
            Text(
                str(process.ppid) if process.ppid is not None else "—",
                style=current_theme().caramel,
            ),
            Text(process.user or "—", style=current_theme().muted),
            Text(format_percent(process.cpu_percent), style=_load_style(process.cpu_percent, 50.0)),
            Text(
                format_percent(process.memory_percent),
                style=_load_style(process.memory_percent, 20.0),
            ),
            Text(process.elapsed or "—", style=current_theme().muted),
            Text(process.status or "—", style=current_theme().muted),
            Text(process.exe or "—", style=current_theme().caramel),
        ]

    def _update_status(self) -> None:
        try:
            status = self.query_one("#status-bar", StatusBar)
        except Exception:
            return

        if self._loading:
            status.set_counts("Collecting processes…")
        elif not self._all:
            status.set_counts("0 processes")
        elif self._current_query().strip():
            status.set_counts(f"{len(self._filtered)} of {len(self._all)} processes")
        else:
            status.set_counts(f"{len(self._all)} processes")

        total_rss = sum(p.memory_rss or 0 for p in self._filtered)
        if total_rss:
            status.set_counts(f"{status.counts}  ·  {format_bytes(total_rss)} resident")

        status.set_counts(f"{status.counts}  ·  {self.auto_update_label()}")

    def _auto_update_pass(self) -> None:
        """Cheap timer-driven pass: the process list is psutil-only."""
        self.collect_processes()

    def action_refresh(self) -> None:
        """Refresh every process. Also the catch-up run when resuming."""
        self.collect_processes()

    def _on_auto_update_toggled(self, *, paused: bool) -> None:
        self._update_status()
        if paused:
            self.notify(
                "Auto-update paused — press R to resume.",
                severity="information",
                timeout=3,
            )
        else:
            self.notify(
                f"Auto-update resumed ({format_interval(get_settings().refresh_interval)}).",
                severity="information",
                timeout=3,
            )

    def action_focus_search(self) -> None:
        self.query_one("#process-search", Input).focus()

    def action_clear_search(self) -> None:
        search = self.query_one("#process-search", Input)
        if search.value:
            search.value = ""
            self._apply_query()
            search.focus()
            return
        self.query_one("#process-grid", DataTable).focus()

    def action_show_help(self) -> None:
        self.notify(
            "↑↓ navigate  ⏎ inspect  / search  R toggle auto-update  "
            "k k terminate  Esc clear  Q quit",
            title="Keyboard",
            severity="information",
            timeout=6,
        )

    def action_go_back(self) -> None:
        """Backspace: clear the search first, then leave to the detail view."""
        search = self.query_one("#process-search", Input)
        if search.value:
            search.value = ""
            self._apply_query()
            return
        self.app.pop_screen()

    def action_quit_app(self) -> None:
        self.app.exit()

    @on(Input.Changed, "#process-search")
    def _on_search_changed(self, event: Input.Changed) -> None:
        event.stop()
        self._apply_query()

    @on(Input.Submitted, "#process-search")
    def _on_search_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.query_one("#process-grid", DataTable).focus()

    @on(DataTable.RowHighlighted, "#process-grid")
    def _on_highlight(self, event: DataTable.RowHighlighted) -> None:
        event.stop()
        row = event.cursor_row
        if row is not None and 0 <= row < len(self._filtered):
            process = self._filtered[row]
            # Moving the cursor onto a different process invalidates any pending
            # terminate, so a confirm can never fire against a stale target.
            if self._kill_armed_pid is not None and self._kill_armed_pid != process.pid:
                self._disarm_kill()
            self._cursor_pid = process.pid

    @on(DataTable.RowSelected, "#process-grid")
    def _on_select(self, event: DataTable.RowSelected) -> None:
        event.stop()
        row = event.cursor_row
        if row is None or not (0 <= row < len(self._filtered)):
            return
        process = self._filtered[row]
        self._cursor_pid = process.pid
        self.app.push_process_detail(process, cursor_pid=self._cursor_pid)

    def on_key(self, event: Any) -> None:
        """Left arrow returns to the previous screen only outside the input.

        Any key other than ``k`` also cancels a pending terminate, so the app
        never sits armed waiting for a confirm.
        """
        if event.key not in ("left", "k"):
            self._disarm_kill()
            return
        focused = self.focused
        if isinstance(focused, (Input, DataTable)):
            return
        if event.key != "left":
            return
        event.stop()
        self.action_go_back()


def _load_style(value: float | None, threshold: float) -> str:
    if value is None:
        return current_theme().muted
    if value >= threshold:
        return current_theme().coral
    if value >= threshold / 2:
        return current_theme().orange
    return current_theme().lime
