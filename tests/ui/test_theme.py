"""Theme palette: one dark set of values, byte-identical to the pre-theme
literals it replaced, plus the config-surface tokens ui/chrome.py composes."""

from __future__ import annotations

from dataclasses import fields

from nparseplus.ui import theme


def test_dark_matches_pre_theme_literals() -> None:
    dark = theme.DARK
    assert dark.panel_bg == "rgba(0, 0, 0, 180)"
    assert dark.text == "#dddddd"
    assert dark.heading == "#ffffff"
    assert dark.bar_track == "rgba(255, 255, 255, 35)"
    assert dark.warning_text == "#ff5044"
    assert dark.dps_you == "#e0c341"
    assert dark.dps_dead_header == "rgba(90, 30, 30, 190)"
    assert dark.dps_live_header == "rgba(0, 40, 80, 190)"


def test_the_palette_defines_every_field() -> None:
    for field in fields(theme.DARK):
        assert getattr(theme.DARK, field.name), field.name


def test_palette_is_the_single_source() -> None:
    """There is one palette; ``palette()`` exists so call sites read like
    ``skins.skin()`` rather than reaching for a module constant."""
    assert theme.palette() is theme.DARK


def test_the_light_theme_is_gone() -> None:
    """It bought a second set of values to keep in sync and a restart to
    switch between them, on an app that renders over a dark game."""
    assert not hasattr(theme, "LIGHT")
    assert not hasattr(theme, "set_theme")
    assert not hasattr(theme, "stylesheet_filename")
