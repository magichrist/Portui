"""Process detail: overview, categories, and per-resource navigation."""

from __future__ import annotations

import logging
import time
from contextlib import suppress
from typing import Any, ClassVar

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Container, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, DataTable, Input, Static, TabbedContent, TabPane

from ..collectors.lsof import LsofCollector
from ..config import current_theme, get_settings, with_tokens
from ..models import (
    CollectionResult,
    PermissionState,
    ProcessRecord,
    ResourceCategory,
    format_bytes,
    format_percent,
)
from ..services.deduplication import deduplicate, filter_category
from ..widgets.resource_table import ResourceTable
from ..widgets.status_bar import StatusBar
from .common import (
    COMPACT_WIDTH,
    NARROW_WIDTH,
    AppHeader,
    AutoUpdateMixin,
    KillMixin,
    ResponsiveMixin,
    ScreenBanner,
    format_interval,
)

log = logging.getLogger(__name__)

__all__ = ["ProcessDetailScreen"]


class MetricGrid(Container):
    """Grid of :class:`Metric` tiles, rebuilt whenever metrics change."""

    DEFAULT_CSS = with_tokens(
        """
    MetricGrid {
        layout: grid;
        grid-size: 4;
        grid-columns: 1fr 1fr;
        grid-rows: 2;
        grid-gutter: 0 2;
        height: auto;
        margin: 0 0 1 0;
    }
    MetricGrid.-compact {
        grid-size: 2;
    }
    MetricGrid.-narrow {
        grid-size: 1;
    }
    """
    )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._metrics: list[tuple[str, str | None, str]] = []

    def set_metrics(self, metrics: list[tuple[str, str | None, str]]) -> None:
        self._metrics = list(metrics)
        self.call_after_refresh(self._rebuild)

    def _rebuild(self) -> None:
        if not self.is_mounted:
            return

        tiles = list(self.children)
        if len(tiles) == len(self._metrics) and all(isinstance(tile, Metric) for tile in tiles):
            # Same shape as last time — repaint the existing tiles in place.
            # Rebuilding here would unmount and remount every tile on each
            # refresh tick, which is what made this screen feel sluggish.
            for tile, (label, value, style) in zip(tiles, self._metrics, strict=True):
                tile.update_values(label, value, style)
            return

        self.remove_children()
        self.mount_all([Metric(*item) for item in self._metrics])

    def on_resize(self, event: Any) -> None:
        width = getattr(event, "size", None)
        width = width.width if width is not None else getattr(event, "width", None)
        if width is None:
            return
        self.set_class(width < COMPACT_WIDTH, "-compact")
        self.set_class(width < NARROW_WIDTH, "-narrow")

    def metric_text(self) -> str:
        parts = []
        for label, value, _style in self._metrics:
            parts.append(f"{label}: {value if value is not None else 'unavailable'}")
        return "  ".join(parts)


class Metric(Static):
    """One label/value tile on the overview grid.

    A value of ``None`` renders as an explicit *unavailable* marker, which is
    deliberately styled differently from a real value of zero.
    """

    DEFAULT_CSS = with_tokens(
        """
    Metric {
        height: 2;
        padding: 0 1;
        border-left: thick $purple;
        background: $surface-alt;
        content-align: left middle;
        overflow: hidden;
    }
    Metric .metric-label {
        color: $muted;
    }
    Metric .metric-value {
        text-style: bold;
    }
    Metric.-missing {
        border-left: thick $muted;
    }
    """
    )

    def __init__(self, label: str, value: str | None, style: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._label = label
        self._value = value
        self._style = style

    def on_mount(self) -> None:
        self.refresh_value()

    def update_values(self, label: str, value: str | None, style: str) -> None:
        """Reuse an existing tile instead of unmounting and remounting it."""
        self._label = label
        self._value = value
        self._style = style
        self.refresh_value()

    def refresh_value(self) -> None:
        rendered = Text()
        if self._value is None or self._value == "\u2014":
            self.add_class("-missing")
            rendered.append(f"{self._label}  ", style=current_theme().muted)
            rendered.append("unavailable", style=f"italic {current_theme().muted}")
        else:
            # The tile may have been showing a previous unavailable value, and
            # reused tiles are never remounted, so clear the marker here.
            self.remove_class("-missing")
            rendered.append(f"{self._label}  ", style=current_theme().muted)
            rendered.append(self._value, style=f"bold {self._style or current_theme().cotton}")
        self.update(rendered, layout=False)


CATEGORIES: list[tuple[ResourceCategory, str]] = [
    (ResourceCategory.NETWORK, "Network"),
    (ResourceCategory.LISTENERS, "Listening"),
    (ResourceCategory.FILES, "Files"),
    (ResourceCategory.UNIX, "UNIX"),
    (ResourceCategory.ALL, "All FDs"),
]


class ProcessDetailScreen(ResponsiveMixin, AutoUpdateMixin, KillMixin, Screen[None]):
    """Everything known about one process, with independent refresh paths."""

    BINDINGS: ClassVar = [
        ("q", "quit_app", "Quit"),
        ("r", "toggle_auto_update", "Auto-update"),
        ("k", "kill_process", "Terminate"),
        ("slash", "focus_search", "Search"),
        ("ctrl+f", "focus_search", "Search"),
        ("escape", "go_back", "Back"),
        ("backspace", "go_back", "Back"),
        ("1", "show_tab('tab-overview')", "Overview"),
        ("2", "show_tab('tab-network')", "Network"),
        ("3", "show_tab('tab-listeners')", "Listening"),
        ("4", "show_tab('tab-files')", "Files"),
        ("5", "show_tab('tab-unix')", "UNIX"),
        ("6", "show_tab('tab-all')", "All FDs"),
    ]

    TAB_ORDER: ClassVar = (
        "tab-overview",
        "tab-network",
        "tab-listeners",
        "tab-files",
        "tab-unix",
        "tab-all",
    )

    DEFAULT_CSS = with_tokens("""
    ProcessDetailScreen {
        height: 1fr;
    }
    ProcessDetailScreen #detail-tabs {
        height: 1fr;
    }
    ProcessDetailScreen #overview-pane {
        padding: 0 1;
    }
    """)

    def __init__(
        self,
        process: ProcessRecord,
        lsof: LsofCollector,
        collector: Any,
        *,
        cursor_pid: int | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.process = process
        self.lsof = lsof
        self.process_collector = collector
        self.cursor_pid = cursor_pid

        self._result: CollectionResult | None = None
        self._initialising = True
        self._deduped: list = []
        self._collecting = False
        self._collapsed = 0
        self._category_cache: dict[str, list] = {}

    def compose(self) -> ComposeResult:
        yield AppHeader(id="app-header")
        yield ScreenBanner(
            ("Processes", f"{self.process.name} · PID {self.process.pid}"),
            id="banner",
        )

        with TabbedContent(id="detail-tabs"):
            with (
                TabPane("Overview", id="tab-overview"),
                VerticalScroll(id="overview-pane"),
            ):
                yield Static("", id="detail-title")
                yield MetricGrid(id="metric-grid")
                yield Static("", id="command-line")
                yield Static("", id="collection-note")
            for category, label in CATEGORIES:
                with TabPane(label, id=f"tab-{category.value}"):
                    yield ResourceTable(category=category, id=f"table-{category.value}")

        yield StatusBar(id="status-bar")

    def on_mount(self) -> None:
        self._apply_breakpoints(self.size.width)
        self.query_one("#detail-tabs", TabbedContent).active = "tab-overview"
        self.render_overview()
        self._update_status()
        self.collect_resources()
        self.refresh_process_metrics()
        self.set_timer(0.05, self._end_initialisation)
        self._start_auto_update()

    def _end_initialisation(self) -> None:
        self._initialising = False

    def render_overview(self) -> None:
        process = self.process
        self.query_one("#detail-title", Static).update(
            Text(f"{process.name}  ·  PID {process.pid}", style=f"bold {current_theme().coral}")
        )

        result = self._result
        metrics: list[tuple[str, str | None, str]] = [
            ("PID", str(process.pid), current_theme().cotton),
            (
                "PPID",
                str(process.ppid) if process.ppid is not None else None,
                current_theme().cotton,
            ),
            ("USER", process.user, current_theme().cotton),
            ("STATUS", process.status, current_theme().cotton),
            (
                "CPU",
                format_percent(process.cpu_percent).rstrip("%")
                + ("%" if process.cpu_percent is not None else ""),
                None if process.cpu_percent is None else current_theme().orange,
            ),
            (
                "MEMORY",
                format_percent(process.memory_percent),
                None if process.memory_percent is None else current_theme().cyan,
            ),
            (
                "RSS",
                format_bytes(process.memory_rss),
                None if process.memory_rss is None else current_theme().cyan,
            ),
            (
                "THREADS",
                str(process.num_threads) if process.num_threads is not None else None,
                current_theme().cotton,
            ),
            ("RUNTIME", process.elapsed, current_theme().muted),
            ("UNIQUE NETWORK", str(result.network_count) if result else None, current_theme().cyan),
            ("LISTENING", str(result.listener_count) if result else None, current_theme().orange),
            ("OTHER RESOURCES", str(result.file_count) if result else None, current_theme().purple),
        ]

        grid = self.query_one("#metric-grid", MetricGrid)
        grid.set_metrics(metrics)

        self.query_one("#command-line", Static).update(
            Text(process.command_line, style=current_theme().cotton)
        )
        self._render_collection_note(result)

    def _render_collection_note(self, result: CollectionResult | None) -> None:
        note = self.query_one("#collection-note", Static)
        note.remove_class("-error")

        if result is None:
            note.update(Text("◌ Collecting resources…", style=current_theme().purple))
            return

        lines: list[Text] = []
        collected = time.strftime("%H:%M:%S", time.localtime(result.collected_at))
        lines.append(
            Text(
                f"Collected at {collected} in {result.duration_seconds:.2f}s "
                f"· {len(result.records)} raw rows → {len(self._deduped)} unique "
                f"(dedup key: command+pid+user+name+state, FD excluded)",
                style=current_theme().muted,
            )
        )

        warning = result.warning()
        if warning:
            note.add_class("-error")
            lines.append(Text(f"\n⚠  {warning}", style=current_theme().coral))

        if result.issues:
            lines.append(
                Text(
                    f"\n{len(result.issues)} record(s) could not be fully parsed; "
                    "raw values are still shown.",
                    style=current_theme().amber,
                )
            )
        note.update(Text("\n").join(lines))

    @work(exclusive=True, group="resources")
    async def collect_resources(self, *, quiet: bool = False) -> None:
        """Run lsof off the event loop, then deduplicate once for every view.

        ``quiet`` suppresses the transient "Collecting…" status and the
        placeholder note. The timer passes it because collection is now a
        routine ~10ms background pass; without it the status bar would flash on
        every single tick and the collection note would visibly flicker.
        """
        if not quiet:
            self._collecting = True
            self._update_status()
            self._render_collection_note(None)

        try:
            result = await self.lsof.collect(self.process.pid)
        except Exception as exc:
            log.exception("lsof collection failed")
            self._collecting = False
            self._show_collection_failure(str(exc))
            return

        stats: list = []
        self._deduped = deduplicate(result.records, stats=stats)
        self._result = result
        self._collecting = False
        if stats:
            self._collapsed = stats[0].collapsed

        self._category_cache.clear()
        self._populate_tables()
        self.render_overview()
        self._update_status()

        if result.permission is PermissionState.GONE:
            self.notify(
                "This process exited before its resources could be collected.",
                title="Process exited",
                severity="warning",
            )

    def _show_collection_failure(self, message: str) -> None:
        note = self.query_one("#collection-note", Static)
        note.add_class("-error")
        note.update(Text(f"⚠  Collection failed: {message}", style=current_theme().coral))
        self._result = CollectionResult(permission=PermissionState.FAILED, error=message)
        self.render_overview()
        self._update_status()

    def _rows_for(self, category: ResourceCategory) -> list:
        key = category.value
        if key not in self._category_cache:
            self._category_cache[key] = filter_category(self._deduped, key)
        return self._category_cache[key]

    def _populate_tables(self) -> None:
        for category, _label in CATEGORIES:
            table = self.query_one(f"#table-{category.value}", ResourceTable)
            # Keep any active per-table filter across a refresh; clearing it on
            # every tick would make the filter unusable while auto-update runs.
            table.set_records(self._rows_for(category), keep_query=True)

    @work(exclusive=True, group="metrics")
    async def refresh_process_metrics(self) -> None:
        """Refresh psutil metrics independently of socket collection."""
        refreshed = await self.process_collector.process_detail(self.process.pid)
        if refreshed is None:
            self.notify(
                "This process has exited.",
                title="Gone",
                severity="warning",
            )
            return
        self.process = refreshed
        self.render_overview()

    def _auto_update_pass(self) -> None:
        """Timer-driven pass: psutil metrics *and* a fresh lsof collection.

        An earlier version refreshed only the psutil metrics, on the assumption
        that re-running lsof was too expensive to do on a timer. Measured on
        macOS, ``lsof -p <pid>`` costs roughly 10ms, so that assumption was
        wrong: the resource tabs stayed frozen for the whole session and the
        Network tab in particular never changed, which read as "refresh is
        broken". Both now refresh together.
        """
        self.refresh_process_metrics()
        self.collect_resources(quiet=True)

    def action_refresh(self) -> None:
        """Full refresh: psutil metrics *and* a fresh lsof collection.

        This is what runs when R resumes the timer, so a single press both
        updates everything and puts the screen back on the timer. Unlike the
        timer pass it is user-initiated, so it shows the collecting status.
        """
        self.refresh_process_metrics()
        self.collect_resources()

    def _on_auto_update_toggled(self, *, paused: bool) -> None:
        self._update_status()
        if paused:
            self.notify(
                "Auto-update paused — press R to resume and re-collect.",
                severity="information",
                timeout=3,
            )
        else:
            self.notify(
                f"Auto-update resumed ({format_interval(get_settings().refresh_interval)}).",
                severity="information",
                timeout=3,
            )

    # -- kill target ------------------------------------------------------

    def _default_key_hints(self) -> str:
        return "  ←→  tabs  1-6  jump  ⏎  detail  R  auto-update  k  terminate  Q  quit  "

    def _kill_target(self) -> tuple[int, str] | None:
        """This screen only ever shows one process, so the target is fixed."""
        return self.process.pid, f"{self.process.name} (PID {self.process.pid})"

    def _after_kill(self, result: Any) -> None:
        """Return to the list once the process this screen describes is gone."""
        if result.ok:
            self.app.pop_screen()

    def _update_status(self) -> None:
        with suppress(Exception):
            self._apply_status()

    def _apply_status(self) -> None:
        status = self.query_one("#status-bar", StatusBar)

        if self._collecting:
            status.set_counts("Collecting open resources…")
        elif self._result is None:
            status.set_counts("No resources collected yet")
        else:
            status.set_counts(
                f"{self._result.network_count} network  ·  "
                f"{self._result.listener_count} listening  ·  "
                f"{self._result.file_count} other"
            )
        status.set_keys(self._default_key_hints())
        if self._result is not None:
            status.set_collection(self._result)

        status.set_counts(f"{status.counts}  ·  {self.auto_update_label()}")

    def action_go_back(self) -> None:
        """Close any open search box first, then return to the process list."""
        for category, _ in CATEGORIES:
            table = self.query_one(f"#table-{category.value}", ResourceTable)
            if table.close_search():
                return
        self.app.pop_screen()

    def action_quit_app(self) -> None:
        self.app.exit()

    def action_show_tab(self, tab_id: str) -> None:
        with suppress(Exception):
            self.query_one("#detail-tabs", TabbedContent).active = tab_id

    def action_cycle_tab(self, step: int = 1) -> None:
        """Move to the next or previous category tab, wrapping at both ends."""
        tabs = self.query_one("#detail-tabs", TabbedContent)
        current = tabs.active
        if current not in self.TAB_ORDER:
            self.action_show_tab(self.TAB_ORDER[0])
            return
        index = (self.TAB_ORDER.index(current) + step) % len(self.TAB_ORDER)
        self.action_show_tab(self.TAB_ORDER[index])

    def action_focus_search(self) -> None:
        table = self._active_table()
        if table is not None:
            table.toggle_search()

    def action_focus_table(self) -> None:
        table = self._active_table()
        if table is not None:
            table.focus_table()

    def _active_table(self) -> ResourceTable | None:
        """The table belonging to the active category tab, or ``None`` on Overview.

        There is deliberately no fallback. The Overview pane has no table, and
        returning ``#table-network`` from here would make callers focus a table
        that lives in a *different* tab pane — which makes Textual activate that
        pane, so landing on Overview silently bounced the user to Network.
        """
        tabbed = self.query_one("#detail-tabs", TabbedContent)
        active = tabbed.active
        for category, _ in CATEGORIES:
            if active == f"tab-{category.value}":
                return self.query_one(f"#table-{category.value}", ResourceTable)
        return None

    @on(Button.Pressed, "#back-button")
    def _on_back(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_go_back()

    @on(TabbedContent.TabActivated)
    def _on_tab(self, event: TabbedContent.TabActivated) -> None:
        event.stop()
        if self._initialising:
            return
        # Overview has no table; focusing one would activate a different tab.
        table = self._active_table()
        if table is not None:
            table.focus_table()

    def on_key(self, event: Any) -> None:
        """Left/Right cycle the category tabs; Enter opens the selected row.

        Any key other than ``k`` cancels a pending terminate.
        """
        if event.key not in ("left", "right", "k"):
            self._disarm_kill()
            return
        if isinstance(self.focused, Input):
            return
        if event.key == "k":
            return
        event.stop()
        self.action_cycle_tab(1 if event.key == "right" else -1)

    @on(DataTable.RowSelected, "#resource-grid")
    def _on_resource_selected(self, event: DataTable.RowSelected) -> None:
        """Enter inside a focused table opens the selected resource handling.

        ``RowSelected`` covers both plain Enter and double-click, so there is no
        separate key action required.
        """
        event.stop()
        table = self._active_table()
        if table is None:
            return
        record = table.selected_record()
        if record is None:
            return
        self.app.push_resource_detail(record, self.process, self)

    def on_resource_table_record_count_changed(self, message: Any) -> None:
        message.stop()
        status = self.query_one("#status-bar", StatusBar)
        status.set_counts(
            f"{message.shown}/{message.total} shown  ·  {self._collapsed} collapsed"
            f"  ·  {self.auto_update_label()}"
        )
