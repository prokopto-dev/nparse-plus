"""Overlay skins: the pure data/geometry layer under the redesign.

``ui/skins.py`` is Qt-free by design, so almost everything here runs without a
window. The two Qt-touching cases (color parsing, the notched plate path) get
their own section at the bottom.
"""

from __future__ import annotations

from dataclasses import fields

import pytest

from nparseplus.ui import skins, theme

pytestmark = pytest.mark.qt


# -- the skin set ---------------------------------------------------------------


def test_every_shipped_skin_is_registered_and_ordered() -> None:
    assert set(skins.SKINS) == set(skins.SKIN_ORDER)
    assert skins.DEFAULT_SKIN in skins.SKINS
    # The order is what the picker and the tray submenu render.
    assert skins.SKIN_ORDER == ("duxa", "velious", "ledger")


def test_every_skin_defines_every_field() -> None:
    """A half-filled skin would fall back to a neighbour's look at random."""
    for skin in skins.SKINS.values():
        for field in fields(skin):
            value = getattr(skin, field.name)
            assert value is not None, (skin.name, field.name)
        assert skin.label and skin.blurb
        assert skin.plate and skin.glass and skin.title_fill


def test_every_skin_tints_every_header_kind() -> None:
    for skin in skins.SKINS.values():
        for kind in skins.HEADER_KINDS:
            assert kind in skin.header_tints, (skin.name, kind)
            assert skin.header_tints[kind].text


def test_set_skin_falls_back_to_the_default_on_an_unknown_name() -> None:
    try:
        skins.set_skin("velious")
        assert skins.raw_skin() is skins.VELIOUS
        skins.set_skin("nonsense")
        assert skins.raw_skin() is skins.SKINS[skins.DEFAULT_SKIN]
    finally:
        skins.set_skin(skins.DEFAULT_SKIN)


def test_ledger_is_the_only_full_row_skin() -> None:
    """The row style is the one structural difference between the three."""
    full = {name for name, skin in skins.SKINS.items() if skin.row_style == "full"}
    assert full == {"ledger"}
    assert skins.LEDGER.row_rule > 0  # the full-row bar needs its left rule
    assert skins.DUXA.bar_height > 0 and skins.VELIOUS.bar_height > 0


# -- color + geometry helpers ---------------------------------------------------


def test_shade_moves_toward_white_and_black_without_leaving_the_ramp() -> None:
    assert skins.shade("#808080", 0.0) == "#808080"
    assert skins.shade("#000000", 1.0) == "#ffffff"
    assert skins.shade("#ffffff", -1.0) == "#000000"
    lighter = skins.shade("#2f9e6e", 0.3)
    darker = skins.shade("#2f9e6e", -0.3)
    assert lighter > "#2f9e6e" > darker  # hex ordering tracks brightness here


def test_shade_clamps_instead_of_overflowing() -> None:
    assert skins.shade("#ffffff", 0.9) == "#ffffff"
    assert skins.shade("#000000", -0.9) == "#000000"


def test_base_color_reads_a_fill_in_any_notation() -> None:
    """A skin's plate may be hex, rgba() or transparent; a caller that wants a
    color rather than a fill wants one answer."""
    assert skins.base_color(("#3a3122", "#241e14")) == "#3a3122"
    assert skins.base_color(("rgba(6, 7, 10, 219)",)) == "#06070a"
    assert skins.base_color(("rgb(6, 7, 10)",)) == "#06070a"
    assert skins.base_color(("transparent",)) == "#000000"
    assert skins.base_color(()) == "#000000"


def test_base_color_survives_every_shipped_plate_and_glass() -> None:
    for skin in skins.SKINS.values():
        for colors in (theme.DARK, theme.LIGHT):
            resolved = skin.resolved(colors)
            for stops in (resolved.plate, resolved.glass, resolved.title_fill):
                assert skins.base_color(stops).startswith("#")


def test_rgba_carries_the_alpha_through() -> None:
    assert skins.rgba("#2f9e6e", 0.5) == "rgba(47, 158, 110, 0.500)"
    assert skins.rgba("#2f9e6e", 5) == "rgba(47, 158, 110, 1.000)"  # clamped
    assert skins.rgba("#2f9e6e", -1) == "rgba(47, 158, 110, 0.000)"


def test_gradient_degrades_to_a_flat_color_for_one_stop() -> None:
    assert skins.gradient(("#123456",)) == "#123456"
    two = skins.gradient(("#000000", "#ffffff"))
    assert two.startswith("qlineargradient")
    assert "stop: 0.000 #000000" in two and "stop: 1.000 #ffffff" in two
    assert skins.gradient(()) == "transparent"


def test_bar_fill_is_flat_for_full_row_skins_and_lit_for_stacked_ones() -> None:
    # Ledger paints its bar itself, so styling it as a gradient would be a lie.
    assert skins.bar_fill(skins.LEDGER, "#2f9e6e") == "#2f9e6e"
    duxa = skins.bar_fill(skins.DUXA, "#2f9e6e")
    velious = skins.bar_fill(skins.VELIOUS, "#2f9e6e")
    assert duxa.startswith("qlineargradient")
    # Velious adds the shaded third stop; Duxa only lifts the top edge.
    assert velious.count("stop:") == 3
    assert duxa.count("stop:") == 2


def test_px_scales_with_the_users_font_size_and_never_goes_illegible() -> None:
    assert skins.px(12, 1.0) == 12
    assert skins.px(24, 1.0) == 24  # the user's font size still drives everything
    assert skins.px(6, 0.1) == 7  # floor
    assert skins.px(12, 1.25) == 15


def test_tracking_is_expressed_in_px_because_qt_has_no_em() -> None:
    assert skins.tracking(12, 1.0, 0.2) == "2.40px"


# -- stylesheet builders --------------------------------------------------------


def test_header_style_tints_from_the_row_kind() -> None:
    detrimental = skins.header_style(skins.DUXA, 12, skins.KIND_DETRIMENTAL)
    beneficial = skins.header_style(skins.DUXA, 12, skins.KIND_YOU)
    assert skins.DUXA.header_tints[skins.KIND_DETRIMENTAL].text in detrimental
    assert detrimental != beneficial
    assert "background-color" in detrimental  # Duxa bands its headers


def test_header_style_of_a_bandless_skin_has_no_fill() -> None:
    style = skins.header_style(skins.LEDGER, 12, skins.KIND_DETRIMENTAL)
    assert "background: transparent" in style
    assert "background-color" not in style


def test_header_style_falls_back_rather_than_raising_on_an_unknown_kind() -> None:
    fallback = skins.header_style(skins.DUXA, 12, "not-a-kind")
    assert fallback == skins.header_style(skins.DUXA, 12, skins.KIND_PLAYER)


def test_title_bar_style_carries_the_velious_bevel_only() -> None:
    velious = skins.title_bar_style(skins.VELIOUS, 12)
    duxa = skins.title_bar_style(skins.DUXA, 12)
    assert skins.VELIOUS.title_highlight in velious
    assert "border-top" not in duxa


def test_overlay_window_style_names_the_shared_row_objects() -> None:
    style = skins.overlay_window_style(skins.DUXA, theme.DARK, 12)
    for selector in ("#SkinTitle", "#SkinRowName", "#SkinRowValue", "QScrollBar"):
        assert selector in style


# -- theme adaptation -----------------------------------------------------------


def test_skins_pass_through_unchanged_on_the_dark_theme() -> None:
    assert skins.DUXA.resolved(theme.DARK) is skins.DUXA


def test_light_theme_lifts_the_glass_but_keeps_the_skins_geometry() -> None:
    """A dark plate under the light theme would be a hole in the desktop; the
    frame's SHAPE is the user's choice and must survive."""
    light = skins.VELIOUS.resolved(theme.LIGHT)
    assert light is not skins.VELIOUS
    assert light.notch == skins.VELIOUS.notch
    assert light.row_style == skins.VELIOUS.row_style
    assert light.plate_padding == skins.VELIOUS.plate_padding
    assert light.glass != skins.VELIOUS.glass
    assert light.title_color == skins.VELIOUS.title_color  # the gold stays gold


def test_light_theme_darkens_the_chrome_accent_and_nothing_else_new() -> None:
    """``chrome_accent`` lands on a pale config surface, where the gold every
    other accent keeps would be illegible. The four overlay fields need no
    branch — the event overlay is never themed."""
    for skin in skins.SKINS.values():
        light = skin.resolved(theme.LIGHT)
        assert light.chrome_accent != skin.chrome_accent
        assert light.chrome_band == skin.chrome_band
        assert light.lane_bg == skin.lane_bg
        assert light.lane_border == skin.lane_border
        assert light.overlay_chip_fill == skin.overlay_chip_fill
        assert light.overlay_chip_text == skin.overlay_chip_text


def test_the_new_overlay_fields_are_visible_under_every_skin() -> None:
    """These exist precisely because deriving them broke on Ledger, whose
    ``overlay_bar_bg`` is transparent and whose ``mark_color`` is empty."""
    for skin in skins.SKINS.values():
        assert skin.lane_bg and skin.lane_bg != "transparent", skin.name
        assert skin.lane_border, skin.name
        assert skin.overlay_chip_fill and skin.overlay_chip_text, skin.name
        assert skin.chrome_accent, skin.name
        assert skin.chrome_band and skin.chrome_band[0] != "transparent", skin.name


def test_skin_reads_through_the_active_theme() -> None:
    try:
        skins.set_skin("duxa")
        theme.set_theme("light")
        assert skins.skin().glass != skins.DUXA.glass
        assert skins.raw_skin() is skins.DUXA  # the picker still previews the real one
        theme.set_theme("dark")
        assert skins.skin() is skins.DUXA
    finally:
        theme.set_theme("dark")
        skins.set_skin(skins.DEFAULT_SKIN)
