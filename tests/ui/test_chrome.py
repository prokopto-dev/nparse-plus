"""Config-surface chrome: the pure token layer under the redesign.

``ui/chrome.py`` is Qt-free by design (like skins.py and theme.py), so
everything here runs without a window — including the import test that keeps
it that way.
"""

from __future__ import annotations

from dataclasses import fields

import pytest

from nparseplus.ui import chrome, skins, theme

pytestmark = pytest.mark.qt


# -- the module's own contract --------------------------------------------------


def test_chrome_stays_qt_free() -> None:
    """The whole point of the split: tokens are testable without a window.

    ``ui/chromewidgets`` is where Qt is allowed to appear.
    """
    import inspect

    source = inspect.getsource(chrome)
    assert "PySide6" not in source
    assert "QtWidgets" not in source


def test_every_badge_tone_is_named_once() -> None:
    assert len(set(chrome.BADGE_TONES)) == len(chrome.BADGE_TONES)


# -- the palette/skin split -----------------------------------------------------


def test_chrome_takes_hue_from_the_skin_and_value_from_the_palette() -> None:
    """The rule the whole module rests on. If this inverts, a Velious config
    page ends up gold-on-gold."""
    duxa = chrome.chrome_for(skins.DUXA, theme.DARK)
    velious = chrome.chrome_for(skins.VELIOUS, theme.DARK)

    # Same theme -> identical ground, field and body text...
    assert duxa.surface == velious.surface
    assert duxa.field_bg == velious.field_bg
    assert duxa.text == velious.text
    # ...but a different accent.
    assert duxa.accent != velious.accent
    assert duxa.accent == skins.DUXA.chrome_accent
    assert velious.accent == skins.VELIOUS.chrome_accent


def test_the_theme_moves_the_ground_under_one_skin() -> None:
    dark = chrome.chrome_for(skins.DUXA, theme.DARK)
    light = chrome.chrome_for(skins.DUXA, theme.LIGHT)
    assert dark.surface != light.surface
    assert dark.text != light.text
    assert dark.hint != light.hint


def test_the_accent_is_never_the_field_background() -> None:
    """The failure mode this module exists to prevent, asserted directly."""
    for skin in skins.SKINS.values():
        for colors in (theme.DARK, theme.LIGHT):
            ch = chrome.chrome_for(skin.resolved(colors), colors)
            assert ch.accent != ch.field_bg, (skin.name, colors.name)
            assert ch.accent != ch.surface, (skin.name, colors.name)
            assert ch.text != ch.surface, (skin.name, colors.name)


def test_field_tokens_alias_the_existing_map_input_palette() -> None:
    """Not a copy — theme.py's map_input_* already mean 'an input field in
    this theme', and two sources for one idea drift."""
    ch = chrome.chrome_for(skins.DUXA, theme.DARK)
    assert ch.field_bg == theme.DARK.map_input_bg
    assert ch.field_text == theme.DARK.map_input_text
    assert ch.field_border == theme.DARK.map_input_border


def test_every_chrome_token_is_filled_for_every_skin_and_theme() -> None:
    for skin in skins.SKINS.values():
        for colors in (theme.DARK, theme.LIGHT):
            ch = chrome.chrome_for(skin.resolved(colors), colors)
            for field in fields(ch):
                value = getattr(ch, field.name)
                assert value, (skin.name, colors.name, field.name)


# -- semantic accents -----------------------------------------------------------


def test_semantic_tokens_are_distinct() -> None:
    """They may coincide with an unrelated surface's literal; they must not
    coincide with each other, or two meanings become unreadable as one."""
    tokens = (chrome.GOOD, chrome.BAD, chrome.COOLDOWN, chrome.TIMER, chrome.ROLL)
    assert len(set(tokens)) == len(tokens)


def test_the_spell_window_reads_its_bar_colors_from_the_tokens() -> None:
    from nparseplus.ui import spellwindow

    assert spellwindow.COLOR_BENEFICIAL == chrome.GOOD
    assert spellwindow.COLOR_DETRIMENTAL == chrome.BAD
    assert spellwindow.COLOR_ROLL == chrome.ROLL


def test_the_skin_preview_paints_the_same_colors_the_real_rows_do() -> None:
    from nparseplus.ui import skinwidgets, spellwindow

    assert skinwidgets.PREVIEW_ROW_COLORS == (
        spellwindow.COLOR_BENEFICIAL,
        spellwindow.COLOR_TIMER,
        spellwindow.COLOR_DETRIMENTAL,
    )
