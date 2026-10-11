"""Tests for the theme catalog and live palette switching."""

from __future__ import annotations

import pytest

from ports_explorer.config import (
    CATALOG,
    DEFAULT_THEME,
    GUMMY,
    PALETTES,
    Theme,
    ThemeCatalog,
    current_theme,
    design_tokens,
    get_settings,
    set_theme,
    theme_names,
    with_tokens,
)


@pytest.fixture(autouse=True)
def _restore_global_theme_state():
    """Keep every test independent of module-level ``CATALOG`` / ``_SETTINGS``.

    ``build_settings`` and ``set_theme`` mutate module globals, so tests that
    use them would otherwise leak their theme into whatever runs next -- making
    assertions like ``current_theme().name == "gummy"`` pass or fail purely on
    collection order. Snapshot before, restore after.
    """
    from ports_explorer import config

    settings = get_settings()
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
        # build_settings reinstalls both _SETTINGS and CATALOG.active.
        config.build_settings(
            debug=debug,
            log_file=log_file,
            refresh_interval=refresh_interval,
            lsof_timeout=lsof_timeout,
            theme=theme,
        )


def test_catalog_contains_a_gummy_theme() -> None:
    assert "gummy" in PALETTES
    gummy = PALETTES["gummy"]
    assert gummy.coral == "#FF5C8A"
    assert gummy.orange == "#FF9F43"
    assert gummy.lime == "#6BCB77"
    assert gummy.cyan == "#4ECDC4"
    assert gummy.purple == "#B983FF"


def test_gummy_is_the_default_theme() -> None:
    assert DEFAULT_THEME == "gummy"
    assert get_settings().theme == "gummy"


def test_every_palette_has_the_same_token_shape() -> None:
    required = set(GUMMY.tokens())
    for theme in PALETTES.values():
        assert set(theme.tokens()) == required
        for value in theme.tokens().values():
            assert value.startswith("#")
            assert len(value) in (4, 7)


def test_design_tokens_are_valid_tcss() -> None:
    for theme in PALETTES.values():
        block = design_tokens(theme)
        for line in block.splitlines():
            name, _, value = line.partition(":")
            assert name.startswith("$")
            assert value.strip().endswith(";")


def test_with_tokens_prepends_the_active_palette() -> None:
    block = with_tokens("X { color: red; }")
    for token in ("$coral", "$surface", "$cotton"):
        assert f"{token}:" in block


def test_setting_an_unknown_theme_returns_none() -> None:
    assert set_theme("definitely-not-a-theme") is None
    assert current_theme().name == "gummy"


def test_cycle_wraps_in_both_directions() -> None:
    catalog = ThemeCatalog("gummy")
    names = catalog.names
    first = names[0]

    for _ in names:
        catalog.cycle(1)
    assert catalog.active.name == first

    catalog.cycle(1)
    catalog.cycle(-1)
    assert catalog.active.name == first


def test_cycle_lands_on_the_named_theme() -> None:
    # A local catalog: cycling it must move *this* catalog, not the module-level
    # CATALOG that current_theme() reads.
    catalog = ThemeCatalog("gummy")
    theme = catalog.cycle(3)
    assert theme.name == catalog.names[3]
    assert catalog.active.name == theme.name
    assert catalog.active.name != "gummy"


def test_themes_differ_from_each_other() -> None:
    signatures = {theme.coral + theme.surface for theme in PALETTES.values()}
    assert len(signatures) == len(PALETTES)


def test_themes_expose_a_state_color() -> None:
    for theme in PALETTES.values():
        assert theme.state_color("LISTEN") == theme.amber
        assert theme.state_color("ESTABLISHED") == theme.lime
        assert theme.state_color("-") == theme.muted


def test_dark_and_light_both_exist() -> None:
    assert any(theme.dark for theme in PALETTES.values())
    assert any(not theme.dark for theme in PALETTES.values())


def test_theme_catalog_names_are_stable() -> None:
    assert theme_names() == list(PALETTES)
    assert len(theme_names()) >= 5


def test_settings_record_the_theme() -> None:
    from ports_explorer.config import build_settings

    settings = build_settings(theme="tokyo")
    assert settings.theme == "tokyo"
    assert CATALOG.active.name == "tokyo"


def test_unknown_cli_theme_rejected(capsys) -> None:
    from ports_explorer.__main__ import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(["--theme", "nope"])


@pytest.mark.parametrize("name", list(PALETTES))
def test_every_palette_can_be_applied(name: str) -> None:
    from ports_explorer.config import build_settings

    theme = build_settings(theme=name).theme
    assert theme == name


def test_gummy_palette_matches_the_original_bash_tool() -> None:
    """The Gummy palette is ported verbatim from ``port-ps.sh``.

    The Bash tool names its colours (STRAWBERRY, ORANGE, LEMON, ...); the
    ``Theme`` dataclass names them by their role. Asserted here against the
    dataclass fields -- the palette's home -- not against ``tokens()``, which
    is the narrower CSS-variable view that intentionally collapses the
    duplicate orange/amber pair into the single ``$amber`` token.
    """
    expected = {
        "coral": "#FF5C8A",  # STRAWBERRY
        "orange": "#FF9F43",  # ORANGE
        "amber": "#FF9F43",  # ORANGE, aliased for state colours
        "yellow": "#FFD93D",  # LEMON
        "lime": "#6BCB77",  # LIME
        "blue": "#4D96FF",  # BLUEBERRY
        "purple": "#B983FF",  # GRAPE
        "mint": "#4ECDC4",  # MINT
        "caramel": "#C08B4F",  # CARAMEL
        "cotton": "#FFF6E5",  # COTTON
    }
    for name, value in expected.items():
        assert getattr(GUMMY, name) == value, name


def test_gummy_is_a_theme_dataclass_instance() -> None:
    assert isinstance(GUMMY, Theme)
    assert GUMMY.label == "Gummy"
