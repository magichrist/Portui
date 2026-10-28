"""The Textual application shell.

Owns the collectors, the screen stack, and the navigation helpers that restore
selection and filter state when the user backs out.
"""

from __future__ import annotations

import logging
import os
from contextlib import suppress
from pathlib import Path
from typing import Any, ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer

from .collectors.lsof import LsofCollector
from .collectors.processes import ProcessCollector
from .config import (
    CATALOG,
    DEFAULT_THEME,
    Theme,
    get_settings,
    set_theme,
)
from .models import ProcessRecord, ResourceRecord
from .screens.process_detail import ProcessDetailScreen
from .screens.process_list import ProcessListScreen
from .screens.resource_browser import ResourceBrowserScreen, ResourceDetailScreen
from .screens.theme_picker import ThemePickerScreen

log = logging.getLogger(__name__)

__all__ = ["PortsExplorerApp"]

STYLE_PATH = Path(__file__).parent / "styles" / "app.tcss"


class PortsExplorerApp(App):
    """Interactive macOS process, network and open-resource explorer.

    Inspection is read-only. The single exception is the two-key terminate
    action on ``k``, which sends SIGTERM and never escalates privileges on its
    own — a process owned by another user simply reports permission-denied and
    the choice to use ``sudo`` stays with the user.
    """

    CSS_PATH = STYLE_PATH
    TITLE = "Ports Explorer"
    SUB_TITLE = "process & network inspection"

    BINDINGS: ClassVar = [
        Binding("ctrl+q", "quit", "Quit", show=True, priority=True),
        Binding("question_mark", "show_help", "Help", show=True),
        Binding("ctrl+t", "show_themes", "Themes", show=True, priority=True),
        Binding("t", "next_theme", "Next theme", show=False),
    ]

    def __init__(self, theme: str | None = None) -> None:
        super().__init__()
        settings = get_settings()
        self.process_collector = ProcessCollector()
        self.lsof_collector = LsofCollector(timeout=settings.lsof_timeout)
        self._cursor_pid: int | None = None
        self._theme_name = theme or settings.theme or DEFAULT_THEME
        set_theme(self._theme_name)

    def get_css_variables(self) -> dict[str, str]:
        """Feed the active palette into every stylesheet, including widget CSS."""
        variables = super().get_css_variables()
        variables.update(CATALOG.active.tokens())
        return variables

    # -- theming ---------------------------------------------------------

    @property
    def theme_name(self) -> str:
        return CATALOG.active.name

    def apply_theme(self, name: str) -> Theme | None:
        """Switch palette and re-render every already-mounted widget."""
        theme = set_theme(name)
        if theme is None:
            return None
        self._theme_name = theme.name
        self.refresh_css()
        self._refresh_visible_content()
        return theme

    def _refresh_visible_content(self) -> None:
        """Re-render widgets whose text was baked with the previous palette."""
        with suppress(Exception):
            for screen in self.screen_stack:
                for widget in screen.query("*"):
                    redraw = getattr(widget, "render_overview", None) or getattr(
                        widget, "render_detail", None
                    )
                    if callable(redraw):
                        redraw()
        with suppress(Exception):
            self.screen.query_one("StatusBar").refresh_content()

    def action_show_themes(self) -> None:
        self.push_screen(ThemePickerScreen(self))

    def action_next_theme(self) -> None:
        theme = CATALOG.cycle(1)
        self.apply_theme(theme.name)
        self.notify(f"Theme: {theme.label}", severity="information", timeout=3)

    def action_show_help(self) -> None:
        self.notify(
            "↑↓ move  ⏎ open  ←/Esc back  / search  R auto-update  "
            "1-6 tabs  k k terminate  T theme  Ctrl+T themes  Q quit",
            title="Keyboard shortcuts",
            severity="information",
            timeout=8,
        )

    def push_process_detail(self, process: ProcessRecord, *, cursor_pid: int | None = None) -> None:
        """Open the real detail screen for a process."""
        if cursor_pid is not None:
            self._cursor_pid = cursor_pid
        screen = ProcessDetailScreen(
            process,
            self.lsof_collector,
            self.process_collector,
            cursor_pid=self._cursor_pid,
        )
        self.push_screen(screen)

    def push_resource_search(
        self,
        records: list[ResourceRecord],
        process: ProcessRecord,
        parent_screen: Any,
        *,
        initial_query: str = "",
    ) -> None:
        self.push_screen(
            ResourceBrowserScreen(
                records,
                process,
                parent_screen,
                initial_query=initial_query,
            )
        )

    def push_resource_detail(
        self,
        record: ResourceRecord,
        process: ProcessRecord,
        parent_screen: Any,
    ) -> None:
        self.push_screen(ResourceDetailScreen(record, process, parent_screen))

    def compose(self) -> ComposeResult:
        yield Footer()

    def on_mount(self) -> None:

        self.push_screen(ProcessListScreen(self.process_collector))
        self._report_missing_lsof()

    def _report_missing_lsof(self) -> None:
        missing = self.lsof_collector.missing_binary_error()
        if missing is not None:
            self.notify(
                missing,
                title="lsof not found",
                severity="error",
                timeout=10,
            )
        log.info("Ports Explorer started (pid=%s, theme=%s)", os.getpid(), self.theme_name)
