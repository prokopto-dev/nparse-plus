"""The Discord overlay's menu strip.

Pure — ``chrome.discord_menu_style`` is a stylesheet builder, so none of this
needs QtWebEngine (which the Discord window itself pulls in).
"""

from __future__ import annotations

import pytest

from nparseplus.ui import chrome, skins, theme

pytestmark = pytest.mark.qt


def _style(skin: skins.Skin, alpha: float = 0.9) -> str:
    return chrome.discord_menu_style(chrome.chrome_for(skin, theme.DARK), 40, 50, 60, alpha)


def test_the_user_ground_survives_the_skin() -> None:
    """The Discord window has its own colour picker. A skin dressing the text
    on the strip is one thing; overriding a deliberate colour choice is not."""
    for skin in skins.SKINS.values():
        css = _style(skin)
        assert "rgba(40, 50, 60, 0.9)" in css, skin.name


def test_the_strip_text_follows_the_skin() -> None:
    sheets = {name: _style(skin) for name, skin in skins.SKINS.items()}
    assert len(set(sheets.values())) == len(sheets)


def test_the_2019_darkgreen_hover_is_gone() -> None:
    """The one piece of chrome still wearing the original nparse look."""
    for skin in skins.SKINS.values():
        assert "darkgreen" not in _style(skin)


def test_opacity_reaches_every_text_rule() -> None:
    """The strip fades with the window; a rule that ignored alpha would stay
    solid while everything around it faded out."""
    css = _style(skins.DUXA, alpha=0.25)
    assert "0.250" in css


def test_rgba_of_accepts_hex_and_rgba() -> None:
    assert chrome.rgba_of("#c8a951", 0.5) == "rgba(200, 169, 81, 0.500)"
    assert chrome.rgba_of("rgba(6, 7, 10, 0.9)", 1.0) == "rgba(6, 7, 10, 1.000)"


def test_the_discord_window_exposes_apply_skin() -> None:
    """_apply_appearance's duck-typed loop finds it through window_handles."""
    import ast
    import pathlib

    source = pathlib.Path(
        pathlib.Path(__file__).resolve().parents[2],
        "src",
        "nparseplus",
        "parsers",
        "discord.py",
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    methods = {
        node.name
        for cls in ast.walk(tree)
        if isinstance(cls, ast.ClassDef) and cls.name == "Discord"
        for node in cls.body
        if isinstance(node, ast.FunctionDef)
    }
    assert "apply_skin" in methods
    assert "CSS_MENU" not in source
