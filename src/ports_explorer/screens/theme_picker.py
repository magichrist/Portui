"""Interactive theme picker with live previews."""

from __future__ import annotations

from typing import Any, ClassVar

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Static

from ..config import CATALOG, PALETTES, Theme, current_theme
from ..widgets.status_bar import StatusBar
from .common import AppHeader, ScreenBanner

__all__ = ["ThemePickerScreen"]

PREVIEW_ROWS: list[tuple[str, str, str]] = [
    ("ESTABLISHED", "state", ""),
    ("LISTEN", "state", ""),
    ("CLOSE_WAIT", "state", ""),
    ("127.0.0.1:8080", "network", ""),
    ("/usr/local/lib/libssl.dylib", "meta", ""),
]


class ThemeSwatch(Static):
    """A horizontal colour bar previewing a palette."""

    DEFAULT_CSS = """
    ThemeSwatch {
        height: 1;
        margin: 0 0 1 0;
    }
    """

    def set_theme(self, theme: Theme) -> None:
        order = ("coral", "amber", "lime", "cyan", "blue", "purple", "caramel", "cotton")
        rendered = Text()
        for index, token in enumerate(order):
            rendered.append("█" * 5, style=theme.tokens()[token])
            if index == 1:
                rendered.append("  ")
        self.update(rendered, layout=False)


class ThemePickerScreen(Screen[None]):
    """List every palette; the preview updates live as the cursor moves."""

    BINDINGS: ClassVar = [
        ("escape", "close", "Back"),
        ("backspace", "close", "Back"),
        ("left", "close", "Back"),
        ("q", "close", "Back"),
        ("t", "next_theme", "Next"),
    ]

    DEFAULT_CSS = """
    ThemePickerScreen {
        height: 1fr;
    }
    ThemePickerScreen #theme-preview {
        height: auto;
        margin: 0 2 1 2;
        padding: 1 2;
        border: round $purple;
        background: $surface-alt;
    }
    ThemePickerScreen #theme-preview-name {
        color: $coral;
        text-style: bold;
    }
    """

    def __init__(self, app: Any, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.app_ref = app
        self._themes = [PALETTES[name] for name in CATALOG.names]

    def compose(self) -> ComposeResult:
        yield AppHeader(id="app-header")
        yield ScreenBanner(("Settings", "Themes"), id="banner")
        with VerticalScroll():
            yield DataTable(id="theme-grid", zebra_stripes=True, cursor_type="row")
            yield Static("", id="theme-preview")
        yield StatusBar(id="status-bar")

    def on_mount(self) -> None:
        table = self.query_one("#theme-grid", DataTable)
        table.add_columns("THEME", "BACKGROUND", "TEXT", "ACCENT", "STATE")
        for column, width in zip(table.columns.values(), (18, 14, 14, 14, 14), strict=False):
            column.width = width

        for theme in self._themes:
            table.add_row(*self._cells(theme), key=theme.name)

        table.focus()
        self._select(table.cursor_row)
        self._update_status()

    def _cells(self, theme: Theme) -> list[Text]:
        return [
            Text(theme.label, style=f"bold {theme.coral}"),
            Text("██████", style=theme.surface_alt),
            Text("██████", style=theme.cotton),
            Text("██████", style=theme.purple),
            Text("██████", style=theme.lime),
        ]

    def _select(self, row: int) -> None:
        if not (0 <= row < len(self._themes)):
            return
        theme = self._themes[row]
        self.app_ref.apply_theme(theme.name)
        self._render_preview(theme)
        self._update_status()

    def _render_preview(self, theme: Theme) -> None:
        preview = self.query_one("#theme-preview", Static)
        rendered = Text()
        rendered.append(f"{theme.label}\n", style=f"bold {theme.coral}")

        tokens = theme.tokens()
        rendered.append(
            "  ".join(tokens[name] for name in ("coral", "cyan", "lime")), style=theme.muted
        )
        rendered.append("\n\n")

        for value, kind, _ in PREVIEW_ROWS:
            style = (
                theme.state_color(value)
                if kind == "state"
                else (theme.cyan if kind == "network" else theme.caramel)
            )
            rendered.append(f"  {value:<34}", style=style)

        rendered.append("\n\n  ")
        for index, token in enumerate(
            ("coral", "amber", "lime", "cyan", "blue", "purple", "caramel", "cotton")
        ):
            rendered.append("██", style=tokens[token])
            if index == 3:
                rendered.append("  ")

        preview.update(rendered, layout=False)

    def _update_status(self) -> None:
        status = self.query_one("#status-bar", StatusBar)
        active = current_theme()
        status.set_counts(f"{len(self._themes)} themes  ·  active: {active.label}")
        status.set_keys("  ⏎  apply  T  next  Esc  back  ")

    @on(DataTable.RowHighlighted, "#theme-grid")
    def _on_highlight(self, event: DataTable.RowHighlighted) -> None:
        event.stop()
        self._select(event.cursor_row or 0)

    @on(DataTable.RowSelected, "#theme-grid")
    def _on_select(self, event: DataTable.RowSelected) -> None:
        event.stop()
        self._select(event.cursor_row or 0)
        self.action_close()

    def action_close(self) -> None:
        self.app.pop_screen()

    def action_next_theme(self) -> None:
        table = self.query_one("#theme-grid", DataTable)
        table.action_cursor_down()
