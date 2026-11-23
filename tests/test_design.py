"""Tests for the TUI-Maker Female Cyber Space design system."""

from __future__ import annotations

import pytest

from ports_explorer import design
from ports_explorer.app import PortsExplorerApp
from ports_explorer.config import AMBROSIA, CATALOG, PALETTES, Theme, build_settings


@pytest.fixture(autouse=True)
def _restore_theme():
    """Snapshot *all* settings: build_settings() resets every field."""
    from ports_explorer import config

    settings = config.get_settings()
    snapshot = (
        settings.debug,
        settings.log_file,
        settings.refresh_interval,
        settings.lsof_timeout,
        CATALOG.active.name,
    )
    try:
        yield
    finally:
        debug, log_file, refresh_interval, lsof_timeout, theme = snapshot
        config.build_settings(
            debug=debug,
            log_file=log_file,
            refresh_interval=refresh_interval,
            lsof_timeout=lsof_timeout,
            theme=theme,
        )


def test_exact_palette_constants() -> None:
    assert design.DARK_PANEL_BG == "#1c1810"
    assert design.DEEP_PANEL_BG == "#0f0d0a"
    assert design.SELECTED_BG == "#3a2a0f"
    assert design.HOVER_BG == "#4a3512"
    assert design.FEMME_PINK == "#ff5fa2"
    assert design.GUMMY_PINK == "#ff2e88"
    assert design.GUMMY_LIGHT_PINK == "#ff9ec9"
    assert design.GUMMY_RED == "#ff5c7a"
    assert design.AMBER == "#ffb454"
    assert design.YELLOW == "#ffe08a"
    assert design.PHOSPHOR == "#b6ff5c"
    assert design.TEXT_PRIMARY == "#fff4dd"
    assert design.TEXT_MUTED == "#cdbb95"


def test_aliases_point_at_the_canonical_values() -> None:
    assert design.GUMMY_GREEN == design.PHOSPHOR
    assert design.PHOSPHOR_CYAN == design.PHOSPHOR
    assert design.GUMMY_AMBER == design.AMBER


def test_textual_variables_override_blue_defaults() -> None:
    variables = design.textual_variables()
    assert variables["primary"] == design.AMBER
    assert variables["secondary"] == design.FEMME_PINK
    assert variables["accent"] == design.PHOSPHOR
    assert variables["error"] == design.GUMMY_RED
    assert variables["background"] == design.DEEP_PANEL_BG
    assert variables["surface"] == design.DARK_PANEL_BG
    assert variables["block-cursor-background"] == design.SELECTED_BG
    assert variables["block-cursor-foreground"] == design.YELLOW
    assert "blue" not in variables


def test_textual_variables_returns_a_fresh_copy() -> None:
    first = design.textual_variables()
    first["primary"] = "changed"
    assert design.textual_variables()["primary"] == design.AMBER


def test_ambrosia_maps_the_design_roles() -> None:
    assert AMBROSIA.surface == design.DEEP_PANEL_BG
    assert AMBROSIA.surface_alt == design.DARK_PANEL_BG
    assert AMBROSIA.surface_raised == design.SELECTED_BG
    assert AMBROSIA.border_color == design.GUMMY_PINK
    assert AMBROSIA.accent == design.GUMMY_LIGHT_PINK
    assert AMBROSIA.focus_color == design.AMBER
    assert AMBROSIA.header_color == design.PHOSPHOR
    assert AMBROSIA.selection == design.YELLOW
    assert AMBROSIA.hover_color == design.HOVER_BG
    assert AMBROSIA.danger == design.GUMMY_RED


def test_ambrosia_never_uses_blue_for_text() -> None:
    assert AMBROSIA.blue == design.PHOSPHOR
    assert AMBROSIA.cyan == design.AMBER
    assert AMBROSIA.purple == design.GUMMY_PINK  # structural only


def test_legacy_themes_derive_the_new_roles() -> None:
    gummy = PALETTES["gummy"]
    assert isinstance(gummy, Theme)
    assert gummy.accent == gummy.purple
    assert gummy.border_color == gummy.purple
    assert gummy.header_color == gummy.lime
    assert gummy.selection == gummy.cotton
    assert gummy.hover_color == gummy.surface_raised


def test_role_tokens_are_present_on_every_theme() -> None:
    required = {
        "role-border",
        "role-accent",
        "role-focus",
        "role-header",
        "role-selection",
        "role-hover",
    }
    for theme in PALETTES.values():
        assert required <= set(theme.tokens())
        for name in required:
            assert theme.tokens()[name].startswith("#")


def test_app_exposes_both_design_variables_and_theme_tokens() -> None:
    app = PortsExplorerApp(theme="ambrosia")
    variables = app.get_css_variables()
    assert variables["primary"] == design.AMBER
    assert variables["border"] == design.AMBER
    assert variables["role-border"] == design.GUMMY_PINK
    assert variables["role-accent"] == design.GUMMY_LIGHT_PINK
    assert variables["surface"] == design.DEEP_PANEL_BG


def test_switching_theme_updates_css_variables() -> None:
    app = PortsExplorerApp(theme="ambrosia")
    ambrosia_surface = app.get_css_variables()["surface"]
    build_settings(theme="daylight")
    app.apply_theme("daylight")
    assert app.get_css_variables()["surface"] != ambrosia_surface
    assert app.get_css_variables()["surface"] == "#FAFAFA"
