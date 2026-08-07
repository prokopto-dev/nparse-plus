"""The live Maps window's chrome: reveal, keys, puck, rail, idle fade.

Nothing constructed the Maps window in tests before — it needs the legacy
``QApplication._signals`` the real ``NomnsParse`` provides. A scratch legacy
config keeps every run off the developer's own ``nparse.config.json``.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
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
