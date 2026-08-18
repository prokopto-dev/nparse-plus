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


def test_the_palette_supplies_every_value_token() -> None:
    ch = chrome.chrome_for(skins.DUXA, theme.DARK)
    assert ch.surface == theme.DARK.surface
    assert ch.text == theme.DARK.text
    assert ch.hint == theme.DARK.hint
    assert ch.disabled == theme.DARK.disabled


def test_the_accent_is_never_the_field_background() -> None:
    """The failure mode this module exists to prevent, asserted directly."""
    for skin in skins.SKINS.values():
        ch = chrome.chrome_for(skin, theme.DARK)
        assert ch.accent != ch.field_bg, skin.name
        assert ch.accent != ch.surface, skin.name
        assert ch.text != ch.surface, skin.name


def test_field_tokens_alias_the_existing_map_input_palette() -> None:
    """Not a copy — theme.py's map_input_* already mean "an input field", and
    two sources for one idea drift."""
    ch = chrome.chrome_for(skins.DUXA, theme.DARK)
    assert ch.field_bg == theme.DARK.map_input_bg
    assert ch.field_text == theme.DARK.map_input_text
    assert ch.field_border == theme.DARK.map_input_border


def test_every_chrome_token_is_filled_for_every_skin() -> None:
    for skin in skins.SKINS.values():
        ch = chrome.chrome_for(skin, theme.DARK)
        for field in fields(ch):
            assert getattr(ch, field.name), (skin.name, field.name)


# -- semantic accents -----------------------------------------------------------


def test_semantic_tokens_are_distinct() -> None:
    """They may coincide with an unrelated surface's literal; they must not
    coincide with each other, or two meanings become unreadable as one."""
    tokens = (
        chrome.GOOD,
        chrome.BAD,
        chrome.COOLDOWN,
        chrome.TIMER,
        chrome.ROLL,
        chrome.POP_WINDOW,
    )
    assert len(set(tokens)) == len(tokens)


def test_the_spell_window_reads_its_bar_colors_from_the_tokens() -> None:
    from nparseplus.ui import spellwindow

    assert spellwindow.COLOR_BENEFICIAL == chrome.GOOD
    assert spellwindow.COLOR_DETRIMENTAL == chrome.BAD
    assert spellwindow.COLOR_ROLL == chrome.ROLL
    assert spellwindow.COLOR_POP_WINDOW == chrome.POP_WINDOW


# -- window_style ---------------------------------------------------------------


def test_the_ground_rule_comes_before_every_field_rule() -> None:
    """The specificity trap. QWidget and QLineEdit match a QLineEdit with equal
    specificity, so within one sheet the later rule wins — emit the ground last
    and every text field turns into the page background."""
    style = chrome.window_style(skins.DUXA, theme.DARK, 12)
    ground = style.index("QWidget {")
    for selector in ("QLineEdit", "QPushButton", "QComboBox", "QListWidget", "QTabBar::tab"):
        assert ground < style.index(selector), selector


def test_every_builder_emits_balanced_braces() -> None:
    """A stray brace makes Qt discard the WHOLE sheet with only a runtime
    warning, so the window looks untouched and every other assertion here
    still passes. This caught exactly that: a non-f-string ending in ``}}``,
    which stays two literal braces instead of collapsing to one.
    """
    ch = chrome.chrome_for(skins.DUXA, theme.DARK)
    pieces = {
        "ground": chrome.ground_rules(ch, 12),
        "badge": chrome.badge_rules(ch, 12),
        "card": chrome.card_rules(ch),
        "group": chrome.group_rules(ch, 12),
        "field": chrome.field_rules(ch, 12),
        "button": chrome.button_rules(ch, 12),
        "view": chrome.view_rules(ch, 12),
        "tab": chrome.tab_rules(ch, 12),
        "sidebar": chrome.sidebar_rules(ch, 12),
        "slider": chrome.slider_rules(ch),
        "misc": chrome.misc_rules(ch),
        "scroll": chrome.scrollbar_rules(ch),
        "window": chrome.window_style(skins.DUXA, theme.DARK, 12),
    }
    for name, css in pieces.items():
        assert css.count("{") == css.count("}"), name
        assert "}}" not in css, f"{name}: doubled closing brace"
        assert "{{" not in css, f"{name}: doubled opening brace"


def test_window_style_names_every_shared_object() -> None:
    style = chrome.window_style(skins.DUXA, theme.DARK, 12)
    for name in (chrome.HINT, chrome.CAPTION, chrome.TITLE, chrome.BADGE, chrome.CARD):
        assert f"#{name}" in style, name
    assert f"#{chrome.SIDEBAR}" in style
    assert f"#{chrome.PRIMARY}" in style


def test_every_badge_tone_reaches_the_sheet() -> None:
    style = chrome.window_style(skins.DUXA, theme.DARK, 12)
    for tone in chrome.BADGE_TONES:
        assert f'[{chrome.PROP_TONE}="{tone}"]' in style, tone


def test_the_three_skins_tint_the_same_selectors_differently() -> None:
    """What "picking Velious tints Settings" means, asserted."""
    sheets = {name: chrome.window_style(skin, theme.DARK, 12) for name, skin in skins.SKINS.items()}
    assert len(set(sheets.values())) == len(sheets)
    for name, sheet in sheets.items():
        assert skins.SKINS[name].chrome_accent in sheet, name


def test_the_ground_never_changes_with_the_skin() -> None:
    """The readability floor: a skin may not move the page background or the
    field background, only the accents on top of them."""
    grounds = set()
    for skin in skins.SKINS.values():
        ch = chrome.chrome_for(skin, theme.DARK)
        grounds.add((ch.surface, ch.field_bg, ch.text))
    assert len(grounds) == 1


def test_the_font_size_drives_the_whole_sheet() -> None:
    small = chrome.window_style(skins.DUXA, theme.DARK, 10)
    large = chrome.window_style(skins.DUXA, theme.DARK, 20)
    assert small != large
    assert "font-size: 20px" in large
    assert "font-size: 20px" not in small


def test_qt_palette_spec_covers_the_roles_fusion_reads() -> None:
    ch = chrome.chrome_for(skins.DUXA, theme.DARK)
    spec = chrome.qt_palette_spec(ch)
    for role in ("Window", "WindowText", "Base", "Text", "Button", "Highlight"):
        assert spec.get(role), role


def test_qt_palette_spec_maps_onto_real_qt_roles() -> None:
    """Names are strings here; if Qt renames one the palette silently loses it."""
    from PySide6.QtGui import QPalette

    for role in chrome.qt_palette_spec(chrome.chrome_for(skins.DUXA, theme.DARK)):
        assert hasattr(QPalette.ColorRole, role), role


# -- app_stylesheet: the guard that protects the on-game overlay -----------------


def _selectors(css: str) -> list[str]:
    """Every selector in a sheet, one per rule block."""
    out = []
    for block in css.split("}"):
        head = block.split("{")[0].strip()
        if head:
            out.extend(part.strip() for part in head.split(",") if part.strip())
    return out


def test_app_stylesheet_uses_only_id_selectors_and_the_allowlist() -> None:
    """THE guard on this module.

    The app sheet reaches every widget in the process, including the overlays
    sitting on top of EverQuest. skins.overlay_window_style only overrides
    three properties on QLabel and Qt resolves conflicts per-property, so a
    bare type selector here leaks whatever it does not name onto a transparent
    surface over the game. Menus and tooltips are exempt because they are
    top-level windows a window-scoped sheet can never reach.
    """
    css = chrome.app_stylesheet(skins.DUXA, theme.DARK, 12)
    for selector in _selectors(css):
        assert selector.startswith("#") or selector in chrome.APP_SCOPE_ALLOWLIST, selector


def test_app_stylesheet_still_dresses_the_legacy_parser_chrome() -> None:
    """The only live part of the deleted data/ui/_.css — Discord reads it."""
    css = chrome.app_stylesheet(skins.DUXA, theme.DARK, 12)
    for selector in ("#ParserWindow", "#ParserWindowTitle", "#ParserWindowMenu QPushButton"):
        assert selector in css, selector


def test_app_stylesheet_follows_the_skin() -> None:
    sheets = {n: chrome.app_stylesheet(s, theme.DARK, 12) for n, s in skins.SKINS.items()}
    assert len(set(sheets.values())) == len(sheets)


def test_the_deleted_css_files_are_gone() -> None:
    """They were ~95% dead selectors; the live remainder is generated now."""
    import pathlib as _p

    ui = _p.Path(__file__).resolve().parents[2] / "data" / "ui"
    assert not (ui / "_.css").exists()
    assert not (ui / "light.css").exists()


# -- the regression guard -------------------------------------------------------


def test_no_ui_module_hardcodes_a_muted_grey() -> None:
    """The sixteen copies this layer replaced. They were literals, so they
    stayed dark grey under the light theme; a seventeenth would too."""
    import pathlib

    ui = pathlib.Path(__file__).resolve().parents[2] / "src" / "nparseplus" / "ui"
    allowed = {
        "theme.py",  # defines the tokens
        "chrome.py",  # names the old value in a docstring
    }
    offenders = []
    for path in sorted(ui.glob("*.py")):
        if path.name in allowed:
            continue
        # encoding is not optional: Windows defaults to cp1252, and these
        # modules contain em-dashes.
        text = path.read_text(encoding="utf-8")
        for literal in ("#888888", "#9aa0a6", "#777777"):
            if literal in text:
                offenders.append(f"{path.name}: {literal}")
    assert offenders == [], (
        "hardcoded muted greys found — use chromewidgets.hint()/caption() "
        f"or a Chrome token instead: {offenders}"
    )


def test_the_skin_preview_paints_the_same_colors_the_real_rows_do() -> None:
    from nparseplus.ui import skinwidgets, spellwindow

    assert skinwidgets.PREVIEW_ROW_COLORS == (
        spellwindow.COLOR_BENEFICIAL,
        spellwindow.COLOR_TIMER,
        spellwindow.COLOR_DETRIMENTAL,
    )
