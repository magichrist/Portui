"""Female Cyber Space design tokens (TUI-Maker).

The exact palette used by the Ambrosia look. Every value here is intentional:

* ``GUMMY_PINK`` is a *structural* colour — borders and dividers only. It reads
  as red when used for text on a dark background, so pink *text* always uses
  ``GUMMY_LIGHT_PINK`` instead.
* There is no blue. Textual's blue focus/border defaults are replaced below.

This module has no dependencies so both the theme config and the App can import
it without creating a cycle.
"""

from __future__ import annotations

__all__ = [
    "AMBER",
    "DARK_PANEL_BG",
    "DEEP_PANEL_BG",
    "FEMME_PINK",
    "GUMMY_AMBER",
    "GUMMY_GREEN",
    "GUMMY_LIGHT_PINK",
    "GUMMY_PINK",
    "GUMMY_RED",
    "HOVER_BG",
    "PHOSPHOR",
    "PHOSPHOR_CYAN",
    "SELECTED_BG",
    "TEXT_MUTED",
    "TEXT_PRIMARY",
    "YELLOW",
    "textual_variables",
]

# Panels — warm charcoal, never purple or blue.
DARK_PANEL_BG = "#1c1810"
DEEP_PANEL_BG = "#0f0d0a"
SELECTED_BG = "#3a2a0f"
HOVER_BG = "#4a3512"

# Rose / magenta accents.
FEMME_PINK = "#ff5fa2"
GUMMY_PINK = "#ff2e88"  # structural pink: borders, dividers
GUMMY_LIGHT_PINK = "#ff9ec9"  # text pink: hints, labels, `$ command` lines
GUMMY_RED = "#ff5c7a"  # errors only

# Warm highlights.
AMBER = "#ffb454"  # focus border, mode indicator, primary button
YELLOW = "#ffe08a"  # selected text, active text
PHOSPHOR = "#b6ff5c"  # success, headers, names

# Text.
TEXT_PRIMARY = "#fff4dd"
TEXT_MUTED = "#cdbb95"

# Backwards-compatible aliases used by the wider TUI-Maker ecosystem.
GUMMY_GREEN = PHOSPHOR
PHOSPHOR_CYAN = PHOSPHOR
GUMMY_AMBER = AMBER

# Textual's own theme variables. Overriding these removes the blue focus rings
# and blue borders that ship with Textual, so no widget can fall back to them.
TEXTUAL_VARIABLES: dict[str, str] = {
    "primary": AMBER,
    "secondary": FEMME_PINK,
    "accent": PHOSPHOR,
    "warning": YELLOW,
    "error": GUMMY_RED,
    "success": PHOSPHOR,
    "background": DEEP_PANEL_BG,
    "surface": DARK_PANEL_BG,
    "panel": DARK_PANEL_BG,
    "border": AMBER,
    "border-blurred": "#4a3a20",
    "block-cursor-background": SELECTED_BG,
    "block-cursor-foreground": YELLOW,
    "block-cursor-text-style": "bold",
    "scrollbar": "#6b4a17",
    "scrollbar-active": AMBER,
    "input-cursor-background": YELLOW,
    "input-cursor-foreground": DEEP_PANEL_BG,
    "text-accent": YELLOW,
    "text-primary": TEXT_PRIMARY,
    "text-secondary": TEXT_MUTED,
}


def textual_variables() -> dict[str, str]:
    """Return a fresh copy of the Textual variable overrides."""
    return dict(TEXTUAL_VARIABLES)
