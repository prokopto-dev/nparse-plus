"""The plugin skin façade — ``ui/pluginskin.py``, behind ``nparseplus_sdk.skin``.

Two things are being pinned here, and only the first is obvious.

The obvious one: the snapshot can be built under every skin, follows a live
skin change, and resolves its sizes from the user's font size.

The one that matters: **the palette owns VALUE and the skin owns HUE.** A
plugin painting a ground from :attr:`AppSkin.accent` produces gold on gold
under Velious, which is exactly the mistake the value/hue split exists to
prevent — so the value group is asserted identical across all three skins and
readable on each, and the counter-example is asserted to fail the same
measurement. Without that second half the guard is just a tautology about
constants.
"""

from __future__ import annotations

import pathlib
import re
from dataclasses import fields

import pytest

from nparseplus.config.settings import Settings
from nparseplus.ui import chrome, pluginskin, skins, theme

pytestmark = pytest.mark.qt


@pytest.fixture(autouse=True)
def _restore_appearance():
    yield
    skins.set_skin(skins.DEFAULT_SKIN)
    pluginskin.use_settings(None)


# -- contrast, so "readable" is measured rather than asserted -------------------


def _channel(value: float) -> float:
    value /= 255
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def _luminance(color: str) -> float:
    red, green, blue = skins._hex_rgb(skins.base_color((color,)))
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def contrast(foreground: str, background: str) -> float:
    """WCAG contrast ratio, 1.0 (identical) to 21.0 (black on white).

    Alpha is dropped by ``base_color``: every ground here is composited over
    either the app's own black glass or the game, both dark, so the opaque
    colour is the optimistic-but-fair reading.
    """
    light, dark = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


_RGBA = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)")


def composite(over: str, ground: str) -> str:
    """``over`` laid on ``ground``, as ``#rrggbb``.

    A translucent band is not the colour it declares — Ledger's is a 22%
    wash — so measuring its declared value would flatter it. Alpha is written
    both ways in this codebase (0-255 Qt-stylesheet, 0-1 CSS), like
    ``skinwidgets.qcolor``.
    """
    match = _RGBA.match(over.strip())
    if match is None:
        return skins.base_color((over,))
    red, green, blue, alpha = match.groups()
    weight = 1.0 if alpha is None else (float(alpha) if "." in alpha else int(alpha) / 255)
    under = skins._hex_rgb(skins.base_color((ground,)))
    mixed = [
        round(int(channel) * weight + below * (1 - weight))
        for channel, below in zip((red, green, blue), under, strict=True)
    ]
    return "#{:02x}{:02x}{:02x}".format(*mixed)


def test_contrast_helper_is_calibrated() -> None:
    assert contrast("#ffffff", "#000000") == pytest.approx(21.0, abs=0.01)
    assert contrast("#888888", "#888888") == pytest.approx(1.0)


def test_composite_helper_is_calibrated() -> None:
    assert composite("#123456", "#ffffff") == "#123456"  # opaque wins
    assert composite("rgba(255, 255, 255, 0.5)", "#000000") == "#808080"
    assert composite("rgba(255, 255, 255, 128)", "#000000") == "#808080"  # 0-255 alpha


# -- the snapshot ----------------------------------------------------------------


@pytest.mark.parametrize("skin_name", skins.SKIN_ORDER)
def test_every_skin_produces_a_complete_snapshot(skin_name: str) -> None:
    skins.set_skin(skin_name)
    app = pluginskin.current()

    assert app.name == skin_name
    assert app.label == skins.SKINS[skin_name].label
    for field in fields(app):
        value = getattr(app, field.name)
        assert value is not None
        # ``bar_track_border`` is documented as "" = no border, and Ledger
        # paints its bars rather than styling them, so it has none.
        if field.name in {"notch", "plate_padding", "bar_track_border"}:
            continue
        if isinstance(value, str):
            assert value != "", field.name


def test_the_facade_covers_every_skin_the_user_can_pick() -> None:
    """A skin the settings offer but the façade cannot describe would be a
    plugin painting itself from a stale snapshot with nothing to notice it."""
    assert set(pluginskin.SKIN_NAMES) == set(skins.SKINS)
    assert pluginskin.SKIN_NAMES == skins.SKIN_ORDER


VALUE_FIELDS = (
    "text",
    "heading",
    "hint",
    "disabled",
    "surface",
    "surface_alt",
    "field_bg",
    "field_text",
    "field_border",
    "panel_bg",
    "track",
)
HUE_FIELDS = ("accent", "plate_border", "chip_text")


def test_the_value_group_is_identical_under_every_skin() -> None:
    """The readability floor a skin may not move. This is the assertion a
    plugin author is relying on when they paint text with ``app.text``."""
    snapshots = []
    for name in skins.SKIN_ORDER:
        skins.set_skin(name)
        snapshots.append(pluginskin.current())

    for field in VALUE_FIELDS:
        values = {getattr(app, field) for app in snapshots}
        assert len(values) == 1, (field, values)


def test_the_hue_group_actually_changes_with_the_skin() -> None:
    """The other half: if nothing moved, picking Velious would be cosmetic
    only in Settings and a plugin would have nothing to follow."""
    accents = set()
    for name in skins.SKIN_ORDER:
        skins.set_skin(name)
        app = pluginskin.current()
        accents.add(tuple(getattr(app, field) for field in HUE_FIELDS))
    assert len(accents) == len(skins.SKIN_ORDER)


@pytest.mark.parametrize("skin_name", skins.SKIN_ORDER)
@pytest.mark.parametrize("ground", ["surface", "surface_alt", "field_bg", "panel_bg"])
def test_palette_owned_values_stay_readable_under_every_skin(skin_name: str, ground: str) -> None:
    """A plugin that paints text with ``app.text`` on any of the app's own
    grounds clears WCAG AA on all three skins, without knowing which is on."""
    skins.set_skin(skin_name)
    app = pluginskin.current()

    assert contrast(app.text, getattr(app, ground)) >= 4.5


@pytest.mark.parametrize("skin_name", skins.SKIN_ORDER)
def test_the_gold_on_gold_mistake_is_the_one_the_rule_prevents(skin_name: str) -> None:
    """The counter-example, measured.

    ``accent`` is a mark — a hairline, a focus ring, a group title — not a
    ground. Body text on it is unreadable under EVERY skin (1.2:1 on
    Velious, where it is literally gold on gold; 1.7:1 Duxa; 3.3:1 Ledger),
    which is what "just use the accent for everything" produces.
    """
    skins.set_skin(skin_name)
    app = pluginskin.current()

    assert contrast(app.text, app.accent) < 4.5
    # ...while the accent used as intended, as a mark on the app's ground,
    # is perfectly visible.
    assert contrast(app.accent, app.surface) >= 3.0


@pytest.mark.parametrize("skin_name", skins.SKIN_ORDER)
def test_the_selection_band_takes_a_palette_foreground(skin_name: str) -> None:
    """``band`` is the ground a selection actually wants, and its text comes
    from the value group like every other ground.

    The tempting pairing is the skin's own caps colour, which is what the
    app's config chrome uses — but that is tuned for the sidebar and measures
    3.4:1 on Ledger's band, below AA. A façade must not recommend it.
    """
    skins.set_skin(skin_name)
    app = pluginskin.current()
    ground = composite(app.band[0], app.surface)

    assert contrast(app.heading, ground) >= 4.5
    assert contrast(app.text, ground) >= 4.5
    # And the band is actually visible against the surface it sits on, or a
    # selection would be indistinguishable from an unselected row.
    assert contrast(ground, app.surface) > 1.1


@pytest.mark.parametrize("skin_name", skins.SKIN_ORDER)
def test_accent_text_is_kept_for_the_plugins_that_shipped_against_it(skin_name: str) -> None:
    """SDK 1.x is additive-only, and app v2.26.0 shipped ``accent_text``.

    Whether the standalone wheel reached PyPI does not undo that: a plugin
    written against the bundled façade reads the attribute, and dropping it
    would raise on the user's next app update. So the NAME is kept for 1.x
    (removal is an SDK 2.0 decision) and the VALUE is corrected — it shipped
    carrying the skin's caps colour, which is 3.4:1 on Ledger's band.
    """
    skins.set_skin(skin_name)
    app = pluginskin.current()

    assert hasattr(app, "accent_text")
    assert app.accent_text == app.heading
    # Corrected, so a plugin still reading it is now readable rather than
    # merely un-crashed — which is the whole point of correcting over keeping.
    assert contrast(app.accent_text, composite(app.band[0], app.surface)) >= 4.5


# -- sizes are multipliers, never px ---------------------------------------------


def test_sizes_follow_the_users_font_size() -> None:
    settings = Settings()
    settings.general.font_size = 12
    pluginskin.use_settings(settings)
    assert pluginskin.current().base_font_size == 12
    twelve = pluginskin.current().px(1.0)

    settings.general.font_size = 20
    app = pluginskin.current()
    assert app.base_font_size == 20
    assert app.px(1.0) > twelve
    assert app.px(1.0) == skins.px(20, 1.0)


def test_the_snapshot_reads_settings_live_rather_than_caching() -> None:
    """``use_settings`` is called once by ``create_app``; the user then edits
    Appearance for the rest of the session. A snapshot taken before the edit
    is stale by design — a fresh one must not be."""
    settings = Settings()
    pluginskin.use_settings(settings)

    settings.general.frame_opacity = 40
    assert pluginskin.current().frame_opacity == pytest.approx(0.4)
    settings.general.frame_opacity = 100
    assert pluginskin.current().frame_opacity == pytest.approx(1.0)


def test_create_app_points_the_facade_at_the_live_settings() -> None:
    """One line in ``create_app``, and everything downstream is silent if it
    goes: every plugin would render at the default font size and full frame
    opacity forever, with nothing raising. Read as text rather than imported,
    so the guard costs no Qt (see tests/core/plugins/test_master_toggle.py)."""
    source = (
        pathlib.Path(__file__).resolve().parents[2] / "src" / "nparseplus" / "app.py"
    ).read_text(encoding="utf-8")

    assert "pluginskin.use_settings(settings)" in source


def test_unbound_answers_the_shipped_defaults() -> None:
    """Outside the app — a plugin's unit tests, ``nparseplus-plugin
    validate`` — there is no settings tree, and refusing to answer would make
    the façade untestable in exactly the place it is most useful."""
    pluginskin.use_settings(None)
    app = pluginskin.current()

    assert app.base_font_size == Settings().general.font_size
    assert app.frame_opacity == pytest.approx(1.0)


def test_typography_carries_family_size_and_colour() -> None:
    pluginskin.use_settings(None)
    app = pluginskin.current()

    style = app.typography(pluginskin.NUMERIC_TEXT, color=app.heading)
    assert skins.NOTO_SANS in style
    assert f"font-size: {app.px(pluginskin.NUMERIC_TEXT.scale)}px" in style
    assert f"color: {app.heading}" in style
    assert "px" in app.tracking(pluginskin.SMALL_DISPLAY.scale, 0.2)


# -- the ready-made stylesheets ---------------------------------------------------


@pytest.mark.parametrize("skin_name", skins.SKIN_ORDER)
def test_the_overlay_sheet_targets_the_object_names_it_publishes(skin_name: str) -> None:
    """The names are the contract: a plugin stamps one on a QLabel instead of
    writing rules. A rename on either side and the label goes undressed with
    nothing to notice it."""
    skins.set_skin(skin_name)
    sheet = pluginskin.current().overlay_stylesheet()

    for name in (pluginskin.TITLE, pluginskin.ROW_NAME, pluginskin.ROW_VALUE):
        assert f"#{name}" in sheet


@pytest.mark.parametrize("skin_name", skins.SKIN_ORDER)
def test_the_config_sheet_is_the_one_the_app_dresses_settings_with(skin_name: str) -> None:
    skins.set_skin(skin_name)
    app = pluginskin.current()

    assert app.config_stylesheet() == chrome.window_style(
        skins.skin(), theme.palette(), app.base_font_size
    )


@pytest.mark.parametrize("skin_name", skins.SKIN_ORDER)
def test_qt_parses_both_ready_made_sheets(qtbot, skin_name: str) -> None:
    """Qt discards a malformed sheet WHOLE, with only a runtime warning — the
    window renders undressed while every string assertion still passes. The
    only way to know is to ask Qt (see tests/ui/test_chrome_live.py)."""
    from PySide6.QtCore import qInstallMessageHandler
    from PySide6.QtWidgets import QWidget

    skins.set_skin(skin_name)
    app = pluginskin.current()
    messages: list[str] = []
    qInstallMessageHandler(lambda mode, ctx, message: messages.append(message))
    try:
        for sheet in (
            app.overlay_stylesheet(),
            app.config_stylesheet(),
            app.bar_stylesheet("#0f0"),
        ):
            widget = QWidget()
            qtbot.addWidget(widget)
            widget.setStyleSheet(sheet)
            widget.ensurePolished()
            widget.style().polish(widget)
    finally:
        qInstallMessageHandler(None)

    assert not [m for m in messages if "Could not parse" in m], messages


@pytest.mark.parametrize("skin_name", skins.SKIN_ORDER)
def test_a_region_widget_can_draw_the_overlays_own_bar(skin_name: str) -> None:
    """The façade's first real consumer is a plugin contributing an event
    overlay region (#155), whose bars have to read as part of the overlay
    rather than as an add-on's idea of one — so they come from the same
    builder the event overlay uses, not from ``bar_stylesheet`` (which dresses
    the thin rule under a Timers row and is a different thing entirely)."""
    skins.set_skin(skin_name)
    app = pluginskin.current()

    assert app.overlay_bar_stylesheet(pluginskin.TIMER) == skins.overlay_bar_rules(
        skins.skin(), pluginskin.TIMER
    )
    assert app.overlay_bar_stylesheet(pluginskin.TIMER) != app.bar_stylesheet(pluginskin.TIMER)
    assert app.overlay_bar_height > 0


def test_the_event_overlay_and_a_plugin_region_draw_the_same_bar() -> None:
    """One builder, so a redesign of the overlay's bars cannot leave a
    plugin's region wearing the old ones."""
    import inspect

    from nparseplus.ui import eventoverlay

    assert "skins.overlay_bar_rules" in inspect.getsource(
        eventoverlay.EventOverlayWindow._style_bar
    )


def test_bar_fill_follows_the_skins_own_row_style() -> None:
    """Ledger paints its bars rather than styling them, so its fill is the
    flat colour; the stacked skins get the lit-to-shaded gradient."""
    skins.set_skin("ledger")
    assert pluginskin.current().bar_fill(pluginskin.GOOD) == pluginskin.GOOD

    skins.set_skin("velious")
    assert "qlineargradient" in pluginskin.current().bar_fill(pluginskin.GOOD)


# -- the semantic accents ---------------------------------------------------------


def test_semantic_accents_are_the_apps_own() -> None:
    """A plugin's "this is a debuff" must be the same red the Timers window
    draws, or two windows disagree about what red means."""
    for name in ("GOOD", "BAD", "COOLDOWN", "TIMER", "ROLL", "POP_WINDOW", "LINK"):
        assert getattr(pluginskin, name) == getattr(chrome, name)


def test_helpers_are_the_hosts_own() -> None:
    for name in ("shade", "rgba", "gradient", "px", "tracking", "typography_style"):
        assert getattr(pluginskin, name) is getattr(skins, name)
