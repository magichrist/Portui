"""Centralized configuration: themes, logging and tunables."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "PALETTES",
    "THEME",
    "Settings",
    "Theme",
    "ThemeCatalog",
    "build_settings",
    "configure_logging",
    "default_log_path",
    "design_tokens",
    "get_settings",
    "with_tokens",
]


@dataclass(frozen=True, slots=True)
class Theme:
    """One colour scheme for the whole application.

    The token names are fixed so ``app.tcss`` and every widget's ``DEFAULT_CSS``
    can reference them without knowing which palette is active.
    """

    name: str
    label: str
    coral: str
    orange: str
    amber: str
    yellow: str
    lime: str
    cyan: str
    blue: str
    purple: str
    grape: str
    mint: str
    caramel: str
    cotton: str
    surface: str
    surface_alt: str
    surface_raised: str
    muted: str
    danger: str
    dark: bool = True

    def tokens(self) -> dict[str, str]:
        """CSS variable name to hex value."""
        return {
            "surface": self.surface,
            "surface-alt": self.surface_alt,
            "surface-raised": self.surface_raised,
            "coral": self.coral,
            "cyan": self.cyan,
            "lime": self.lime,
            "amber": self.amber,
            "yellow": self.yellow,
            "purple": self.purple,
            "blue": self.blue,
            "mint": self.mint,
            "grape": self.grape,
            "muted": self.muted,
            "cotton": self.cotton,
            "caramel": self.caramel,
            "danger": self.danger,
        }

    def state_color(self, state: str) -> str:
        mapping = {
            "LISTEN": self.amber,
            "ESTABLISHED": self.lime,
            "CLOSE_WAIT": self.yellow,
            "TIME_WAIT": self.muted,
            "SYN_SENT": self.blue,
            "SYN_RECV": self.blue,
            "FIN_WAIT_1": self.blue,
            "FIN_WAIT_2": self.blue,
            "CLOSING": self.yellow,
            "LAST_ACK": self.yellow,
            "-": self.muted,
            "UNKNOWN": self.coral,
        }
        return mapping.get(state.upper(), self.cotton)


GUMMY = Theme(
    name="gummy",
    label="Gummy",
    coral="#FF5C8A",
    orange="#FF9F43",
    amber="#FF9F43",
    yellow="#FFD93D",
    lime="#6BCB77",
    cyan="#4ECDC4",
    blue="#4D96FF",
    purple="#B983FF",
    grape="#8E6CC7",
    mint="#4ECDC4",
    caramel="#C08B4F",
    cotton="#FFF6E5",
    surface="#140F1B",
    surface_alt="#1E1526",
    surface_raised="#2B1D36",
    muted="#B39BC4",
    danger="#FF5470",
)

MIDNIGHT = Theme(
    name="midnight",
    label="Midnight",
    coral="#FF5C8A",
    orange="#FF9F43",
    amber="#FF9F43",
    yellow="#FFD93D",
    lime="#6BCB77",
    cyan="#4ECDC4",
    blue="#4D96FF",
    purple="#B983FF",
    grape="#8E6CC7",
    mint="#4ECDC4",
    caramel="#C08B4F",
    cotton="#F2EAE0",
    surface="#14121B",
    surface_alt="#1E1B28",
    surface_raised="#262233",
    muted="#8A8496",
    danger="#FF5470",
)

TOKYO = Theme(
    name="tokyo",
    label="Tokyo Night",
    coral="#F7768E",
    orange="#FF9E64",
    amber="#FF9E64",
    yellow="#E0AF68",
    lime="#9ECE6A",
    cyan="#7DCFFF",
    blue="#7AA2F7",
    purple="#BB9AF7",
    grape="#9ECE6A",
    mint="#73DACA",
    caramel="#A9B1D6",
    cotton="#C0CAF5",
    surface="#1A1B26",
    surface_alt="#24283B",
    surface_raised="#2F334D",
    muted="#565F89",
    danger="#F7768E",
)

GRUVBOX = Theme(
    name="gruvbox",
    label="Gruvbox",
    coral="#FB4934",
    orange="#FE8019",
    amber="#FABD2F",
    yellow="#FECC02",
    lime="#B8BB26",
    cyan="#8EC07C",
    blue="#83A598",
    purple="#D3869B",
    grape="#FE8019",
    mint="#8EC07C",
    caramel="#B57614",
    cotton="#EBDBB2",
    surface="#282828",
    surface_alt="#3C3836",
    surface_raised="#504945",
    muted="#928374",
    danger="#FB4934",
)

NEBULA = Theme(
    name="nebula",
    label="Nebula",
    coral="#FF7AD9",
    orange="#FFA15E",
    amber="#FFC773",
    yellow="#FFE08A",
    lime="#7DF9A6",
    cyan="#5FE3E1",
    blue="#7CA9FF",
    purple="#C39BFF",
    grape="#9B7BFF",
    mint="#66E0C2",
    caramel="#C9A227",
    cotton="#EDEAF6",
    surface="#0D0B1A",
    surface_alt="#151129",
    surface_raised="#221B3D",
    muted="#7E7799",
    danger="#FF5470",
)

DAYLIGHT = Theme(
    name="daylight",
    label="Daylight",
    coral="#C2185B",
    orange="#E65100",
    amber="#E65100",
    yellow="#F9A825",
    lime="#2E7D32",
    cyan="#00838F",
    blue="#1565C0",
    purple="#6A1B9A",
    grape="#4527A0",
    mint="#00897B",
    caramel="#8D6E63",
    cotton="#1A1A1A",
    surface="#FAFAFA",
    surface_alt="#F0EDF2",
    surface_raised="#E2DCE8",
    muted="#6B6478",
    danger="#C62828",
    dark=False,
)


PALETTES: dict[str, Theme] = {
    theme.name: theme for theme in (GUMMY, MIDNIGHT, TOKYO, GRUVBOX, NEBULA, DAYLIGHT)
}
DEFAULT_THEME = "gummy"


def design_tokens(theme: Theme) -> str:
    """Render a theme as a TCSS variable block."""
    lines = [f"${name}: {value};" for name, value in theme.tokens().items()]
    return "\n".join(lines)


class ThemeCatalog:
    """Holds the active palette and switches it at runtime.

    The active theme is module-level state so the many small helpers that render
    ``Text`` fragments (which have no access to the app) stay cheap, while the
    app re-parses its stylesheet whenever the palette changes.
    """

    def __init__(self, initial: str = DEFAULT_THEME) -> None:
        self._order = list(PALETTES)
        self._active = PALETTES.get(initial, GUMMY)

    @property
    def active(self) -> Theme:
        return self._active

    @property
    def names(self) -> list[str]:
        return list(self._order)

    def get(self, name: str) -> Theme | None:
        return PALETTES.get(name)

    def set(self, name: str) -> Theme | None:
        theme = PALETTES.get(name)
        if theme is None:
            return None
        self._active = theme
        return theme

    def cycle(self, step: int = 1) -> Theme:
        index = self._order.index(self._active.name)
        index = (index + step) % len(self._order)
        self._active = PALETTES[self._order[index]]
        return self._active

    def state_color(self, state: str) -> str:
        return self._active.state_color(state)


CATALOG = ThemeCatalog()


def __getattr__(name: str) -> Theme:
    if name in PALETTES:
        return CATALOG.set(name) or CATALOG.active
    raise AttributeError(name)


def set_theme(name: str) -> Theme | None:
    return CATALOG.set(name)


def cycle_theme(step: int = 1) -> Theme:
    return CATALOG.cycle(step)


def current_theme() -> Theme:
    return CATALOG.active


def theme_names() -> list[str]:
    return CATALOG.names


THEME = CATALOG.active


def with_tokens(css: str) -> str:
    """Prepend the active theme's variables to a widget's ``DEFAULT_CSS``."""
    return f"{design_tokens(CATALOG.active)}\n{css}"


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings derived from the CLI."""

    debug: bool = False
    log_file: Path | None = None
    refresh_interval: float = 2.0
    lsof_timeout: float = 20.0
    max_process_rows: int = 20000
    theme: str = DEFAULT_THEME

    @property
    def log_level(self) -> int:
        return logging.DEBUG if self.debug else logging.WARNING


_SETTINGS = Settings()


def get_settings() -> Settings:
    return _SETTINGS


def build_settings(
    debug: bool = False,
    log_file: str | Path | None = None,
    refresh_interval: float = 2.0,
    lsof_timeout: float = 20.0,
    theme: str = DEFAULT_THEME,
) -> Settings:
    """Create and install a new settings instance (used by the CLI entry point)."""
    global _SETTINGS
    path: Path | None
    if log_file is None:
        default = Path.home() / ".cache" / "ports-explorer" / "debug.log"
        path = default if debug else None
    else:
        path = Path(log_file)
    CATALOG.set(theme)
    _SETTINGS = Settings(
        debug=debug,
        log_file=path,
        refresh_interval=max(0.0, refresh_interval),
        lsof_timeout=lsof_timeout,
        theme=CATALOG.active.name,
    )
    return _SETTINGS


def configure_logging(debug: bool = False, log_file: str | Path | None = None) -> None:
    """Send diagnostics to a log file — never into the rendered UI."""
    level = logging.DEBUG if debug else logging.WARNING
    handlers: list[logging.Handler] = []

    if log_file is not None:
        path = Path(log_file).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path, encoding="utf-8"))

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        handlers=handlers or [logging.NullHandler()],
        force=True,
    )
    logging.getLogger("ports_explorer").setLevel(level)


def default_log_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "ports-explorer" / "debug.log"
