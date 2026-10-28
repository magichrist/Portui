"""Shared screen building blocks: banner, back button, breadcrumb, empty state."""

from __future__ import annotations

import time
from contextlib import suppress
from typing import Any

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, Input, Static

from ..config import current_theme, get_settings
from ..services import KillOutcome, KillResult, force_kill, terminate
from ..widgets.status_bar import StatusBar

__all__ = [
    "ARM_TIMEOUT",
    "COMPACT_WIDTH",
    "NARROW_WIDTH",
    "AppHeader",
    "AutoUpdateMixin",
    "EmptyState",
    "KillMixin",
    "ResponsiveMixin",
    "ScreenBanner",
]


COMPACT_WIDTH = 100
NARROW_WIDTH = 76

ARM_TIMEOUT = 5.0
"""Seconds a pending terminate stays armed before it silently lapses."""


def format_interval(seconds: float) -> str:
    """Render a refresh interval for the status bar without trailing noise."""
    return f"{seconds:g}s"


class AutoUpdateMixin:
    """Periodic refresh that ``R`` pauses and resumes.

    Subclasses implement :meth:`_auto_update_pass` to perform one refresh pass.
    The timer is armed with ``set_interval`` rather than ``set_timer``, because
    ``set_timer`` is one-shot: it would fire exactly once and then go silent.

    Only cheap sources belong in :meth:`_auto_update_pass`. Anything expensive
    stays behind :meth:`action_refresh`, which runs once when the user resumes.

    Resuming arms a brand-new timer rather than calling ``Timer.resume()``:
    Textual's ``resume()`` replays every missed tick immediately, so a ten-minute
    pause at a two-second interval would fire about 300 callbacks back to back
    and storm the UI. A fresh timer also keeps the cadence clean after a pause.

    Names are prefixed ``_auto_update_*`` on purpose: Textual's ``DOMNode``
    already claims ``_auto_refresh`` and ``_auto_refresh_timer`` as instance
    attributes for widget layout throttling, so those names cannot be reused.
    """

    def _start_auto_update(self) -> None:
        """Begin periodic refreshes unless the interval is disabled."""
        self._auto_update_interval = get_settings().refresh_interval
        self._auto_update_timer = None
        self._auto_update_enabled = self._auto_update_interval > 0
        if self._auto_update_enabled:
            self._arm_auto_update_timer()

    def _arm_auto_update_timer(self) -> None:
        """Create a fresh timer with a clean cadence, replacing any old one."""
        self._stop_auto_update_timer()
        self._auto_update_timer = self.set_interval(
            self._auto_update_interval, self._auto_update_tick
        )

    def _stop_auto_update_timer(self) -> None:
        timer = getattr(self, "_auto_update_timer", None)
        if timer is not None:
            timer.stop()
            self._auto_update_timer = None

    def _auto_update_tick(self) -> None:
        if not getattr(self, "_auto_update_enabled", False):
            return
        self._auto_update_pass()

    def _auto_update_pass(self) -> None:
        """Perform one cheap refresh pass. Implemented by each screen."""
        raise NotImplementedError

    @property
    def auto_update_enabled(self) -> bool:
        return getattr(self, "_auto_update_enabled", False)

    @property
    def auto_update_available(self) -> bool:
        """False when the interval is 0, i.e. this screen cannot auto-update."""
        return getattr(self, "_auto_update_interval", 0) > 0

    def auto_update_label(self) -> str:
        """Status-bar text saying whether the timer is running.

        Shown on every refreshed screen so a frozen list is never mistaken for
        a live one.
        """
        if not self.auto_update_available:
            return "auto-update off  ·  R to refresh"
        if self.auto_update_enabled:
            return f"live {format_interval(self._auto_update_interval)}"
        return "paused  ·  R to resume"

    def action_refresh(self) -> None:
        """Run a full refresh, including anything too costly to do on a timer."""
        raise NotImplementedError

    def action_toggle_auto_update(self) -> None:
        """``R``: pause a running refresh, or resume and catch up in one press.

        Resuming also performs an immediate full refresh, so a single press both
        updates stale data and puts the screen back on the timer.

        With ``--refresh 0`` there is no timer to toggle, so ``R`` stays a plain
        one-shot refresh rather than claiming to be "live".
        """
        if not self.auto_update_available:
            self.action_refresh()
            return

        if self.auto_update_enabled:
            self._auto_update_enabled = False
            timer = getattr(self, "_auto_update_timer", None)
            if timer is not None:
                timer.pause()
            self._on_auto_update_toggled(paused=True)
            return

        self._auto_update_enabled = True
        self._arm_auto_update_timer()
        self.action_refresh()
        self._on_auto_update_toggled(paused=False)

    def _on_auto_update_toggled(self, *, paused: bool) -> None:
        """Hook for status text and user feedback. Overridden by each screen."""
        return


class KillMixin:
    """Two-key arming for the one action that is not read-only.

    Terminating a process is irreversible and cannot be undone from inside this
    app, so a single keystroke must never be enough. Pressing ``k`` arms the
    action for the currently highlighted process and says so in the status bar;
    a second ``k`` within :data:`ARM_TIMEOUT` seconds carries it out. Any other
    key, a selection change, or simply letting the timer lapse disarms it again,
    so the app is never left sitting in a state where one stray keypress is
    enough to kill something.

    SIGTERM always goes first. If the process survives it, the screen re-arms
    for an explicit SIGKILL escalation rather than escalating on its own.
    """

    _kill_armed_pid: int | None = None
    _kill_stage: str | None = None
    _kill_deadline: float = 0.0
    _kill_timer: Any = None

    # -- subclass hooks ---------------------------------------------------

    def _kill_target(self) -> tuple[int, str] | None:
        """Return ``(pid, label)`` for the process ``k`` would act on."""
        raise NotImplementedError

    def _kill_target_changed(self) -> None:
        """Called when the selection moved; disarms any pending terminate."""

    def _after_kill(self, result: Any) -> None:
        """Called after a signal is delivered. Default: nothing."""

    # -- arming ----------------------------------------------------------

    def _kill_expired(self) -> bool:
        return time.monotonic() >= self._kill_deadline

    def _disarm_kill(self) -> None:
        """Clear any pending terminate and stop the lapse timer."""
        timer = self._kill_timer
        if timer is not None:
            with suppress(Exception):
                timer.stop()
        self._kill_timer = None
        self._kill_armed_pid = None
        self._kill_stage = None
        self._kill_deadline = 0.0
        self._render_kill_hint()

    def _arm_kill(self, pid: int, stage: str, label: str) -> None:
        self._kill_armed_pid = pid
        self._kill_stage = stage
        self._kill_deadline = time.monotonic() + ARM_TIMEOUT
        self._render_kill_hint()
        verb = "Force kill" if stage == "force" else "Terminate"
        self.notify(
            f"{verb} {label} (PID {pid})?  Press  k  again within "
            f"{ARM_TIMEOUT:.0f}s to confirm.  Any other key cancels.",
            title="Confirm",
            severity="warning",
            timeout=ARM_TIMEOUT,
        )

    def _render_kill_hint(self) -> None:
        """Surface the pending state in the key hints so it is never invisible."""
        with suppress(Exception):
            status = self.query_one("#status-bar", StatusBar)
            if self._kill_armed_pid is None:
                self._restore_key_hints()
                return
            verb = "FORCE KILL" if self._kill_stage == "force" else "TERMINATE"
            remaining = max(0.0, self._kill_deadline - time.monotonic())
            status.set_keys(
                f"  {verb} armed  ·  k to confirm in {remaining:.0f}s  ·  any key cancels  "
            )

    def _restore_key_hints(self) -> None:
        with suppress(Exception):
            self.query_one("#status-bar", StatusBar).set_keys(self._default_key_hints())

    def _default_key_hints(self) -> str:
        return "  /  search    R  auto-update    k  terminate  Q  quit  "

    # -- the action -------------------------------------------------------

    def action_kill_process(self) -> None:
        """``k``: arm, then confirm with a second ``k``.

        The first press never signals anything. Only the second press within
        the arm window does, and it still refuses to signal PID 0, PID 1 or this
        process.
        """
        # Typing "k" into the search box must insert a character, not arm a kill.
        if isinstance(getattr(self, "focused", None), Input):
            return

        target = self._kill_target()
        if target is None:
            self._disarm_kill()
            return

        pid, label = target
        armed_pid = self._kill_armed_pid

        if armed_pid is None or self._kill_expired() or armed_pid != pid:
            if armed_pid is not None and armed_pid != pid:
                # Selection moved onto a different process: the old intent no
                # longer applies, so start over on the new target.
                self._disarm_kill()
            self._arm_kill(pid, "term", label)
            return

        stage = self._kill_stage or "term"
        self._disarm_kill()
        if stage == "force":
            self.run_force_kill(pid, label)
        else:
            self.run_terminate(pid, label)

    @work(exclusive=True, group="kill")
    async def run_terminate(self, pid: int, label: str) -> None:
        """Send SIGTERM and wait, then report whether escalation is needed."""
        result = await terminate(pid)
        self._report_kill(result, pid, label)
        if result.outcome is KillOutcome.IGNORED:
            # Re-arm for the explicit hard kill rather than escalating silently.
            self._arm_kill(pid, "force", label)

    @work(exclusive=True, group="kill")
    async def run_force_kill(self, pid: int, label: str) -> None:
        """Send SIGKILL after SIGTERM was already ignored."""
        result = await force_kill(pid)
        self._report_kill(result, pid, label)

    def _report_kill(self, result: KillResult, pid: int, label: str) -> None:
        style = {
            KillOutcome.TERMINATED: "information",
            KillOutcome.NOT_FOUND: "information",
            KillOutcome.SENT: "information",
            KillOutcome.PERMISSION_DENIED: "error",
            KillOutcome.REFUSED: "error",
            KillOutcome.ERROR: "error",
            KillOutcome.IGNORED: "warning",
        }[result.outcome]
        self.notify(result.detail, title=label, severity=style, timeout=8)
        self._after_kill(result)


class ResponsiveMixin:
    """Toggle layout-density classes when the terminal is resized.

    Textual's TCSS has no ``@media`` support, so breakpoints are applied here by
    toggling ``-compact`` and ``-narrow``. Classes are inherited by children, so
    one toggle restyles the whole subtree.
    """

    def _apply_breakpoints(self, width: int | None) -> None:
        if width is None:
            return
        self.set_class(width < COMPACT_WIDTH, "-compact")
        self.set_class(width < NARROW_WIDTH, "-narrow")

    def on_resize(self, event: Any) -> None:
        size = getattr(event, "size", None)
        if size is not None:
            self._apply_breakpoints(size.width)
        else:
            self._apply_breakpoints(getattr(event, "width", None))


class AppHeader(Static):
    """Branded header shown on every screen."""

    def compose(self) -> ComposeResult:
        yield Static("", id="header-title")
        yield Static("", id="header-subtitle")

    def on_mount(self) -> None:
        title = Text()
        title.append("◈ PORTS EXPLORER", style=f"bold {current_theme().coral}")
        title.append("  ·  PROCESS · NETWORK · FILES", style=current_theme().muted)
        self.query_one("#header-title", Static).update(title)
        self.set_subtitle("inspection")

    def set_subtitle(self, text: str) -> None:
        rendered = Text()
        rendered.append(text, style=current_theme().muted)
        self.query_one("#header-subtitle", Static).update(rendered)


class ScreenBanner(Horizontal):
    """Breadcrumb trail plus a visible Back control for detail screens."""

    def __init__(
        self, crumbs: tuple[str, ...] = (), *, show_back: bool = True, **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self._crumbs = crumbs
        self._show_back = show_back

    def compose(self) -> ComposeResult:
        if self._show_back:
            yield Button("← Back", id="back-button", variant="default")
        yield Static("", id="breadcrumb")

    def on_mount(self) -> None:
        self.render_crumb(self._crumbs)

    def render_crumb(self, crumbs: tuple[str, ...]) -> None:
        self._crumbs = crumbs
        rendered = Text()
        for index, crumb in enumerate(crumbs):
            if index:
                rendered.append("  ›  ", style=current_theme().caramel)
            style = (
                f"bold {current_theme().cotton}"
                if index == len(crumbs) - 1
                else current_theme().muted
            )
            rendered.append(crumb, style=style)
        with suppress(Exception):
            self.query_one("#breadcrumb", Static).update(rendered)


class EmptyState(Static):
    """Centred empty/error state with a concrete hint, never a bare message."""

    def __init__(
        self,
        title: str,
        body: str = "",
        hint: str = "",
        *,
        tone: str = current_theme().muted,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._title = title
        self._body = body
        self._hint = hint
        self._tone = tone
        super().update(self._build())

    def _build(self) -> Text:
        rendered = Text()
        rendered.append(f"{self._title}\n", style=f"bold {self._tone}")
        if self._body:
            rendered.append(f"{self._body}\n", style=current_theme().muted)
        if self._hint:
            rendered.append(f"\n{self._hint}", style=current_theme().purple)
        return rendered

    def refresh_content(self) -> None:
        with suppress(Exception):
            self.update(self._build(), layout=False)

    def set_content(self, title: str, body: str = "", hint: str = "") -> None:
        self._title = title
        self._body = body
        self._hint = hint
        self.refresh_content()
