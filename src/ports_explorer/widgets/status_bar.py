"""Status strip: branded header, counts, collection health and key hints."""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.reactive import reactive
from textual.widgets import Static

from ..config import current_theme, with_tokens
from ..models import CollectionResult, PermissionState

__all__ = ["HealthBadge", "StatusBar"]


class HealthBadge(Static):
    """Small colored chip describing how trustworthy a collection was."""

    DEFAULT_CSS = with_tokens("""
    HealthBadge {
        height: 1;
        padding: 0 1;
        content-align: left middle;
    }
    """)

    def show(self, permission: PermissionState, detail: str = "") -> None:
        label, style = {
            PermissionState.OK: ("● complete", current_theme().lime),
            PermissionState.PARTIAL: ("● partial", current_theme().orange),
            PermissionState.DENIED: ("● permission denied", current_theme().coral),
            PermissionState.FAILED: ("● failed", current_theme().coral),
            PermissionState.GONE: ("● process exited", current_theme().muted),
        }[permission]
        self.update(Text(label, style=style))
        self.tooltip = detail or label


class StatusBar(Static):
    """Bottom strip with counts, collection health and contextual key hints."""

    counts: reactive[str] = reactive("")

    DEFAULT_CSS = with_tokens("""
    StatusBar {
        height: 1;
        background: $surface;
        color: $muted;
    }
    StatusBar #status-counts {
        width: 1fr;
        padding: 0 2;
    }
    StatusBar #status-health {
        width: auto;
        padding: 0 2;
    }
    StatusBar #status-keys {
        width: auto;
        padding: 0 2;
    }
    """)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._permission: PermissionState | None = None
        self._detail = ""

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Static("", id="status-counts")
            yield HealthBadge(id="status-health")
            yield Static("", id="status-keys")

    def set_counts(self, text: str) -> None:
        self.counts = text
        with suppress(Exception):
            self.query_one("#status-counts", Static).update(
                Text(text, style=current_theme().cotton)
            )

    def set_keys(self, text: str) -> None:
        with suppress(Exception):
            self.query_one("#status-keys", Static).update(Text(text, style=current_theme().muted))

    def set_health(self, permission: PermissionState, detail: str = "") -> None:
        self._permission = permission
        self._detail = detail
        with suppress(Exception):
            self.query_one("#status-health", HealthBadge).show(permission, detail)

    def set_collection(self, result: CollectionResult) -> None:
        self.set_health(result.permission, result.warning() or "")
