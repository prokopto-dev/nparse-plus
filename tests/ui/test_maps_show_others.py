"""Hide-others'-dots toggle (eqtool #211): display-only gate + purge.

The full Maps window needs the app-level signal registry, so these tests
exercise the real methods on a bare instance (``Maps.__new__``) over an
offscreen MapCanvas — the same canvas-level style as test_maps_zfade.py.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from nparseplus.core.events import (
    OtherPlayerLocationReceivedRemoteEvent,
    RemotePlayer,
)
from nparseplus.helpers import config
from nparseplus.parsers.maps.mapcanvas import MapCanvas
from nparseplus.parsers.maps.mapclasses import MapPoint
from nparseplus.parsers.maps.window import Maps

pytestmark = pytest.mark.qt

NOW = datetime(2026, 7, 14, 12, 0, 0)


@pytest.fixture
def maps(qtbot, tmp_path) -> Maps:
    config.load(str(tmp_path / "nparse.config.json"))
    config.verify_settings()
    config.data["maps"]["show_other_players"] = True
    canvas = MapCanvas()
    qtbot.addWidget(canvas)
    canvas.load_map("west freeport")
    bare = Maps.__new__(Maps)
    bare._map = canvas
    return bare


def remote_event(name: str = "Jaloy") -> OtherPlayerLocationReceivedRemoteEvent:
    return OtherPlayerLocationReceivedRemoteEvent(
        player=RemotePlayer(name=name, x=10.0, y=20.0, z=0.0, zone="freportw")
    )


def test_dots_drawn_when_shown(maps: Maps) -> None:
    maps.handle_remote_event(remote_event())
    assert "Jaloy" in maps._map._data.players


def test_dots_not_drawn_when_hidden(maps: Maps) -> None:
    config.data["maps"]["show_other_players"] = False
    maps.handle_remote_event(remote_event())
    assert "Jaloy" not in maps._map._data.players


def test_purge_removes_only_remote_dots(maps: Maps) -> None:
    maps._map.add_player("__you__", NOW, MapPoint(x=0, y=0, z=0))
    maps.handle_remote_event(remote_event("Jaloy"))
    maps.handle_remote_event(remote_event("Thalistair"))
    maps._purge_remote_players()
    assert set(maps._map._data.players) == {"__you__"}
