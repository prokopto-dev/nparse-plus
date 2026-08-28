"""The player marker's heading arrow toggle (#180).

The marker is already two items — ``Player.icon`` (the dot) and
``Player.directional`` (the arrow, created hidden) — so "plain circle" is the
arrow item staying hidden and there is no new drawing to test. What there is
to test is that the flip reaches markers **already on screen**, in both
directions, without the player having to move again.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from PySide6.QtWidgets import QApplication

from nparseplus.helpers import config
from nparseplus.helpers.settings import SettingsSignals
from nparseplus.parsers.maps.mapcanvas import MapCanvas
from nparseplus.parsers.maps.mapclasses import MapPoint
from nparseplus.parsers.maps.window import Maps

pytestmark = pytest.mark.qt

NOW = datetime(2026, 7, 14, 12, 0, 0)
ZONE = "west freeport"


@pytest.fixture
def canvas(qtbot, tmp_path) -> MapCanvas:
    config.load(str(tmp_path / "nparse.config.json"))
    config.verify_settings()
    view = MapCanvas()
    qtbot.addWidget(view)
    view.load_map(ZONE)
    return view


def _walk(view: MapCanvas, name: str = "__you__"):
    """Two fixes, which is what gives a marker a heading at all."""
    view.add_player(name, NOW, MapPoint(x=0.0, y=0.0, z=0.0))
    view.add_player(name, NOW, MapPoint(x=100.0, y=100.0, z=0.0))
    return view._data.players[name]


# -- the setting ----------------------------------------------------------------


def test_default_is_on(tmp_path) -> None:
    """An existing nparse.config.json has no such key; it keeps the arrow."""
    config.load(str(tmp_path / "nparse.config.json"))
    config.verify_settings()
    assert config.data["maps"]["show_direction_arrow"] is True


# -- rendering ------------------------------------------------------------------


def test_arrow_appears_once_the_player_moves(canvas: MapCanvas) -> None:
    you = _walk(canvas)
    assert you.heading_known
    assert you.directional.isVisible()


def test_first_fix_alone_draws_no_arrow(canvas: MapCanvas) -> None:
    """The ``previous_location is None`` guard: one fix is not a heading."""
    canvas.add_player("__you__", NOW, MapPoint(x=0.0, y=0.0, z=0.0))
    assert not canvas._data.players["__you__"].directional.isVisible()


@pytest.mark.parametrize("name", ["__you__", "Jaloy"])
def test_off_leaves_the_plain_circle(canvas: MapCanvas, name: str) -> None:
    """Your own dot and another player's shared dot alike."""
    config.data["maps"]["show_direction_arrow"] = False
    player = _walk(canvas, name)
    assert player.heading_known, "the heading is still tracked, just not drawn"
    assert not player.directional.isVisible()
    assert player.icon.isVisible()


# -- the live flip --------------------------------------------------------------


def test_canvas_sync_hides_and_restores_markers_already_drawn(canvas: MapCanvas) -> None:
    you = _walk(canvas)
    others = _walk(canvas, "Jaloy")
    assert you.directional.isVisible() and others.directional.isVisible()

    config.data["maps"]["show_direction_arrow"] = False
    canvas.sync_direction_arrows()
    assert not you.directional.isVisible()
    assert not others.directional.isVisible()

    # Back on with no further movement: the heading is remembered.
    config.data["maps"]["show_direction_arrow"] = True
    canvas.sync_direction_arrows()
    assert you.directional.isVisible()
    assert others.directional.isVisible()


def test_sync_restores_nothing_for_a_marker_with_no_heading_yet(canvas: MapCanvas) -> None:
    """Turning the setting on must not point an arrow at (0, 0)."""
    canvas.add_player("__you__", NOW, MapPoint(x=0.0, y=0.0, z=0.0))
    config.data["maps"]["show_direction_arrow"] = True
    canvas.sync_direction_arrows()
    assert not canvas._data.players["__you__"].directional.isVisible()


def test_sync_survives_a_canvas_with_no_map_loaded(qtbot, tmp_path) -> None:
    config.load(str(tmp_path / "nparse.config.json"))
    config.verify_settings()
    view = MapCanvas()
    qtbot.addWidget(view)
    view.sync_direction_arrows()  # no _data yet — config_updated can arrive first


def test_config_updated_reaches_markers_through_the_maps_window(
    qtbot, tmp_path, monkeypatch
) -> None:
    """End to end on the real window: the Settings > Maps checkbox writes the
    legacy key and emits ``config_updated``, which is this signal."""
    config.load(str(tmp_path / "nparse.config.json"))
    config.verify_settings()
    config.data["maps"]["last_zone"] = ZONE
    config.data["maps"]["toggled"] = False
    monkeypatch.setattr(config, "save", lambda: None)
    app = QApplication.instance()
    if not hasattr(app, "_signals"):
        app._signals = {"settings": SettingsSignals()}
    window = Maps()
    qtbot.addWidget(window)
    you = _walk(window._map)
    assert you.directional.isVisible()

    config.data["maps"]["show_direction_arrow"] = False
    app._signals["settings"].config_updated.emit()
    assert not you.directional.isVisible()

    config.data["maps"]["show_direction_arrow"] = True
    app._signals["settings"].config_updated.emit()
    assert you.directional.isVisible()
