"""Theme palette (eqtool #148): dark stays byte-identical to the pre-theme
literals; light defines every field; the css siblings stay in sync."""

from __future__ import annotations

import re
from dataclasses import fields
from pathlib import Path

from nparseplus.ui import theme

REPO = Path(__file__).resolve().parents[2]


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


def test_both_palettes_define_every_field() -> None:
    for palette in (theme.DARK, theme.LIGHT):
        for field in fields(palette):
            assert getattr(palette, field.name), (palette.name, field.name)


def test_set_theme_switches_palette_and_stylesheet() -> None:
    try:
        theme.set_theme("light")
        assert theme.palette() is theme.LIGHT
        assert theme.stylesheet_filename() == "light.css"
        theme.set_theme("nonsense")  # unknown values fall back to dark
        assert theme.palette() is theme.DARK
        assert theme.stylesheet_filename() == "_.css"
    finally:
        theme.set_theme("dark")


def _selectors(css: str) -> set[str]:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    return set(re.findall(r"^\s*([#A-Za-z][^{}]*?)\s*\{", css, re.MULTILINE))


def test_light_css_covers_the_same_selectors_as_dark() -> None:
    dark = (REPO / "data" / "ui" / "_.css").read_text()
    light = (REPO / "data" / "ui" / "light.css").read_text()
    assert _selectors(light) == _selectors(dark)
