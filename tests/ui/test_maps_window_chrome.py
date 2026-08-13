"""The live Maps window's chrome: reveal, keys, puck, rail, idle fade.

Nothing constructed the Maps window in tests before — it needs the legacy
``QApplication._signals`` the real ``NomnsParse`` provides. A scratch legacy
config keeps every run off the developer's own ``nparse.config.json``.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QColor, QImage, QKeyEvent, QRegion
from PySide6.QtWidgets import QApplication, QLabel

from nparseplus.helpers import config
from nparseplus.helpers.settings import SettingsSignals
from nparseplus.parsers.maps.mapclasses import MapPoint
from nparseplus.parsers.maps.window import Maps

pytestmark = pytest.mark.qt

ZONE = "oasis of marr"


@pytest.fixture
def maps(qtbot, tmp_path, monkeypatch):
    config.load(str(tmp_path / "nparse.config.json"))
    config.verify_settings()
    config.data["maps"]["last_zone"] = ZONE
    config.data["maps"]["toggled"] = False
    # config.save() would write the scratch file on every toggle; harmless,
    # but the tests never need it and it makes them touch the disk per assert.
    monkeypatch.setattr(config, "save", lambda: None)
    app = QApplication.instance()
    if not hasattr(app, "_signals"):
        app._signals = {"settings": SettingsSignals()}
    window = Maps()
    qtbot.addWidget(window)
    window.resize(500, 420)
    return window


def _place_you(window, x=0.0, y=0.0):
    window._map.add_player("__you__", datetime.now(), MapPoint(x=x, y=y, z=0.0))


# -- reveal ---------------------------------------------------------------------


def test_the_chrome_starts_out_of_the_way(maps) -> None:
    assert not maps._header.isVisible()
    assert not maps._toolbar.isVisible()
    assert not maps._rail.isVisible()
    # The legacy single-glyph menu strip is retired for this window.
    assert not maps._menu.isVisible()


def test_hover_summons_the_header_and_toolbar_and_stands_the_tabs_down(maps) -> None:
    maps.show()
    maps._refresh_chrome()
    assert maps._edge_tabs, "oasis ships two zone lines"
    assert all(tab.isVisible() for tab in maps._edge_tabs)

    maps.enterEvent(QEvent(QEvent.Type.Enter))
    assert maps._header.isVisible() and maps._toolbar.isVisible()
    # The header names both exits in words; two answers is one too many.
    assert not any(tab.isVisible() for tab in maps._edge_tabs)

    maps.leaveEvent(QEvent(QEvent.Type.Leave))
    assert not maps._header.isVisible()
    assert all(tab.isVisible() for tab in maps._edge_tabs)


def test_the_header_names_the_zones_real_exits(maps) -> None:
    exits = maps._zone_line_exits()
    assert [name for name, _location in exits] == ["Northern Ro", "Southern Ro"]


# -- keys -----------------------------------------------------------------------


def _key(window, key, modifiers=Qt.KeyboardModifier.NoModifier):
    event = QKeyEvent(QEvent.Type.KeyPress, key, modifiers)
    # Through the filter, as a real press on the focused map canvas would.
    return window.eventFilter(window._map, event)


def test_tab_toggles_the_rail_from_the_map_canvas(maps) -> None:
    """The canvas takes strong focus, so Tab never reaches keyPressEvent —
    without the event filter it would just move focus."""
    maps.show()
    assert _key(maps, Qt.Key.Key_Tab) is True
    assert maps._rail.isVisible()
    assert _key(maps, Qt.Key.Key_Tab) is True
    assert not maps._rail.isVisible()


def test_ctrl_f_opens_the_find_palette_and_escape_closes_it(maps) -> None:
    maps.show()
    assert _key(maps, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier) is True
    assert maps._search_box.isVisible()
    assert _key(maps, Qt.Key.Key_Escape) is True
    assert not maps._search_box.isVisible()


def test_escape_is_left_alone_when_the_palette_is_closed(maps) -> None:
    assert _key(maps, Qt.Key.Key_Escape) is False


def test_an_empty_palette_offers_the_zones_notables(maps) -> None:
    """This replaced the old "☰ NPCs" button, so opening the palette has to
    offer them without anything being typed first."""
    maps.show()
    maps.open_find()
    assert maps._search_index.notables()
    assert maps._search_results.isVisible()
    listed = {maps._search_results.item(i).text() for i in range(maps._search_results.count())}
    assert any("Young Ronin" in text for text in listed)


# -- the rail -------------------------------------------------------------------


def test_the_rail_shows_what_the_zone_actually_has(maps) -> None:
    maps.show()
    maps.toggle_rail()
    text = " ".join(child.text() for child in maps._rail.findChildren(QLabel))
    assert "NORTHERN RO" in text.upper()
    assert "RESPAWN" in text.upper()
    # No notable NPCs and nobody sharing: those sections are absent, not empty.
    assert "SHARING" not in text.upper()


# -- the recenter puck ----------------------------------------------------------


def test_the_puck_is_muted_while_you_are_centred(maps) -> None:
    maps.show()
    _place_you(maps)
    maps._map.centerOn(0, 0)
    maps._update_puck()
    assert not maps._puck.is_lit()


def test_the_puck_lights_with_a_bearing_once_you_pan_off(maps) -> None:
    maps.show()
    _place_you(maps, x=0.0, y=0.0)
    maps._map.centerOn(-4000, -4000)  # you are now down-right of the view
    maps._update_puck()
    assert maps._puck.is_lit()
    assert maps._puck._arrow == "↘"
    assert maps._puck._distance


def test_clicking_the_puck_brings_you_back(maps) -> None:
    maps.show()
    _place_you(maps, x=0.0, y=0.0)
    maps._map.centerOn(-4000, -4000)
    maps._recenter()
    maps._update_puck()
    assert not maps._puck.is_lit()


def test_the_puck_stays_muted_with_no_location_fix_yet(maps) -> None:
    maps._map.remove_player("__you__")
    maps._update_puck()
    assert not maps._puck.is_lit()


# -- toolbar toggles ------------------------------------------------------------


def test_a_toolbar_toggle_flips_the_setting_and_its_own_lit_state(maps) -> None:
    before = config.data["maps"]["show_poi"]
    maps._toggle_map_option("show_poi")
    assert config.data["maps"]["show_poi"] is (not before)
    maps._toggle_map_option("show_poi")
    assert config.data["maps"]["show_poi"] is before


def test_hiding_other_players_drops_their_dots(maps) -> None:
    maps._map.add_player("Roger", datetime.now(), MapPoint(x=10.0, y=10.0, z=0.0))
    assert "Roger" in maps._map._data.players
    config.data["maps"]["show_other_players"] = True
    maps._toggle_map_option("show_other_players")
    assert "Roger" not in maps._map._data.players


# -- the idle backdrop fade -----------------------------------------------------


def test_idle_fade_drops_the_backdrop_without_forgetting_your_value(maps) -> None:
    config.data["maps"]["backdrop_fade_idle"] = True
    maps._map.set_backdrop_opacity(70)
    maps._fade_backdrop()
    assert maps._map.backdrop_opacity() == 0
    assert config.data["maps"]["backdrop_opacity"] == 70
    maps._wake_backdrop()
    assert maps._map.backdrop_opacity() == 70


def test_the_fade_never_fires_while_it_is_switched_off(maps) -> None:
    config.data["maps"]["backdrop_fade_idle"] = False
    maps._map.set_backdrop_opacity(70)
    maps._fade_backdrop()
    assert maps._map.backdrop_opacity() == 70


def test_touching_the_map_wakes_the_backdrop(maps) -> None:
    config.data["maps"]["backdrop_fade_idle"] = True
    maps._map.set_backdrop_opacity(70)
    maps._fade_backdrop()
    maps.eventFilter(maps._map.viewport(), QEvent(QEvent.Type.Enter))
    assert maps._map.backdrop_opacity() == 70


# -- the backdrop needs a window that can hold alpha (#99) ----------------------
#
# The report was "the backdrop does nothing, and then it starts working after
# clicking Apply twice". The value was never late: what an Apply did was reach
# ParserWindow.apply_window_state -> _set_flags -> setWindowFlags, which
# RECREATES the native window — and the recreated one finally honoured
# WA_TranslucentBackground. A window created without an alpha channel has
# nowhere to composite a below-100% backdrop, so it reads as opaque black at
# every setting (total since #65, which writes alpha with CompositionMode_Source
# instead of accumulating it).


@pytest.fixture
def maps_open(qtbot, tmp_path, monkeypatch):
    """A Maps window that was open at last quit — so ParserWindow.__init__
    shows it, which is when the platform window gets created."""
    config.load(str(tmp_path / "nparse.config.json"))
    config.verify_settings()
    config.data["maps"]["last_zone"] = ZONE
    config.data["maps"]["toggled"] = True
    monkeypatch.setattr(config, "save", lambda: None)
    app = QApplication.instance()
    if not hasattr(app, "_signals"):
        app._signals = {"settings": SettingsSignals()}
    window = Maps()
    qtbot.addWidget(window)
    window.resize(400, 400)
    return window


def test_the_map_window_is_created_with_an_alpha_channel(maps_open) -> None:
    """WA_TranslucentBackground only reaches the surface format of the window
    created AFTER it is set: QWindow::setFormat() past create() does not
    recreate anything, so setting it later (as _build_chrome did) left the
    request permanently ungranted."""
    handle = maps_open.windowHandle()
    assert handle is not None, "the window should be shown, and so have a platform window"
    assert handle.format().alphaBufferSize() > 0


def test_the_alpha_channel_does_not_wait_for_a_settings_apply(maps_open) -> None:
    """The bug's signature: it took an Apply (any Apply — it only had to change
    a window flag) to recreate the window and grant the alpha. Nothing here
    calls apply_window_state, and the channel is already there."""
    before = maps_open.windowHandle().format().alphaBufferSize()
    maps_open.apply_window_state()  # what Settings > Windows does on Apply
    assert before == maps_open.windowHandle().format().alphaBufferSize() > 0


def test_one_apply_is_enough_to_move_the_rendered_backdrop(maps_open) -> None:
    """Drive the settings-window apply path ONCE and read the pixels.

    The QImage is the previous frame, exactly as in the #65 tests: the map
    starts opaque, so a backdrop that only lands on the second Apply would
    leave black behind.
    """
    canvas = maps_open._map
    canvas.apply_backdrop_opacity(100)
    image = QImage(canvas.width(), canvas.height(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(0, 0, 0, 255))
    canvas.viewport().render(image, QPoint(), QRegion(image.rect()))
    # A point the zone's geometry does not ink, so the reading is the backdrop.
    bare = next(
        QPoint(x, y)
        for x in range(4, image.width(), 7)
        for y in range(4, image.height(), 7)
        if image.pixelColor(x, y) == QColor(0, 0, 0, 255)
    )

    # One Apply: _apply_maps writes the legacy key, then config_updated fires.
    config.data["maps"]["backdrop_opacity"] = 40
    QApplication.instance()._signals["settings"].config_updated.emit()

    canvas.viewport().render(image, QPoint(), QRegion(image.rect()))
    assert image.pixelColor(bare) == QColor(0, 0, 0, 102)


# -- the chrome survives an early resize ----------------------------------------


def test_the_window_survives_a_resize_before_the_chrome_exists(qtbot) -> None:
    """ParserWindow.__init__ sets geometry before this subclass builds any
    chrome; PySide6 would swallow the AttributeError, leaving an invisible
    traceback rather than a crash."""

    class Bare(Maps):
        def __init__(self):  # never runs _build_chrome
            pass

    bare = Bare()
    assert not bare._chrome_ready()
