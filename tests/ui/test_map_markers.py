"""EQTool-style player markers + tracking radius (scene introspection)."""

from datetime import datetime

import pytest
from tests.ui.test_maps_zfade import (  # reuse the synthetic-map harness
    make_canvas,
    synthetic_maps,  # noqa: F401 - pytest fixture
)

from nparseplus.parsers.maps.mapclasses import YOU_COLOR, MapPoint

pytestmark = pytest.mark.qt

NOW = datetime(2026, 7, 16, 12, 0, 0)


def test_first_fix_has_no_arrow_second_fix_points(qtbot, synthetic_maps) -> None:  # noqa: F811
    canvas = make_canvas(qtbot, "fadezone")
    canvas.add_player("__you__", NOW, MapPoint(x=0.0, y=0.0, z=0.0))
    player = canvas._data.players["__you__"]
    assert player.previous_location is None
    assert not player.directional.isVisible()  # the old code showed a bogus arrow

    canvas.add_player("__you__", NOW, MapPoint(x=100.0, y=50.0, z=0.0))
    assert player.directional.isVisible()
    assert player.directional.rotation() != 0.0


def test_you_marker_is_eqtool_green_circle(qtbot, synthetic_maps) -> None:  # noqa: F811
    canvas = make_canvas(qtbot, "fadezone")
    canvas.add_player("__you__", NOW, MapPoint(x=0.0, y=0.0, z=0.0))
    player = canvas._data.players["__you__"]
    assert player.icon.pen().color().getRgb()[:3] == (61, 235, 52)  # rgb(61,235,52)
    # Other players keep their stable colorhash color, not green.
    canvas.add_player("Soandso", NOW, MapPoint(x=10.0, y=10.0, z=0.0))
    other = canvas._data.players["Soandso"]
    assert other.icon.pen().color().getRgb()[:3] != (61, 235, 52)


def test_tracking_circle_true_radius_and_cleanup(qtbot, synthetic_maps) -> None:  # noqa: F811
    canvas = make_canvas(qtbot, "fadezone")
    canvas.add_player("__you__", NOW, MapPoint(x=5.0, y=6.0, z=0.0), tracking_distance=2000.0)
    circle = canvas._tracking_circles["__you__"]
    rect = circle.rect()
    assert rect.width() == 4000.0 and rect.height() == 4000.0  # 2r in scene units
    assert (circle.pos().x(), circle.pos().y()) == (5.0, 6.0)
    assert circle.pen().isCosmetic()
    assert circle.pen().color().getRgb()[:3] == YOU_COLOR.getRgb()[:3]
    assert circle.brush().color().alpha() == 5  # EQTool "You" fill alpha

    # Radius updates in place; None removes it.
    canvas.add_player("__you__", NOW, MapPoint(x=5.0, y=6.0, z=0.0), tracking_distance=240.0)
    assert canvas._tracking_circles["__you__"].rect().width() == 480.0
    canvas.add_player("__you__", NOW, MapPoint(x=5.0, y=6.0, z=0.0), tracking_distance=None)
    assert "__you__" not in canvas._tracking_circles


def test_untrackable_player_never_gets_a_circle(qtbot, synthetic_maps) -> None:  # noqa: F811
    canvas = make_canvas(qtbot, "fadezone")
    canvas.add_player("__you__", NOW, MapPoint(x=0.0, y=0.0, z=0.0), tracking_distance=None)
    assert canvas._tracking_circles == {}


def test_remote_player_circle_and_remove_cleanup(qtbot, synthetic_maps) -> None:  # noqa: F811
    canvas = make_canvas(qtbot, "fadezone")
    canvas.add_player("Trackerfriend", NOW, MapPoint(x=1.0, y=2.0, z=0.0), tracking_distance=480.0)
    circle = canvas._tracking_circles["Trackerfriend"]
    assert circle.brush().color().alpha() == 3  # EQTool other-player fill alpha
    canvas.remove_player("Trackerfriend")
    assert "Trackerfriend" not in canvas._tracking_circles
    assert "Trackerfriend" not in canvas._data.players
