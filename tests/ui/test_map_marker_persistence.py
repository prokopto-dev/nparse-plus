"""Persistent map markers (nparse #10 / eqtool #190): spawn points and the
way point survive zone switches and (via settings.json) app restarts."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from tests.ui.test_maps_zfade import (  # reuse the synthetic-map harness
    make_canvas,
    synthetic_maps,  # noqa: F401 - pytest fixture
)

from nparseplus.config.settings import (
    MapMarkerStore,
    Settings,
    SpawnMarker,
    ZoneMarkers,
)

pytestmark = pytest.mark.qt


@pytest.fixture
def store() -> MapMarkerStore:
    return MapMarkerStore(Settings())


def canvas_with_store(qtbot, store: MapMarkerStore, zone: str = "fadezone"):
    canvas = make_canvas(qtbot, zone)
    canvas.marker_store = store
    canvas.restore_markers()  # the app.py injection path
    return canvas


def test_settings_roundtrip() -> None:
    settings = Settings()
    settings.map_markers["fadezone"] = ZoneMarkers(
        spawn_points=[
            SpawnMarker(x=1.0, y=2.0, z=0.0, length_s=90, ends_at=datetime(2026, 7, 14, 12))
        ]
    )
    reloaded = Settings.model_validate(settings.model_dump())
    assert reloaded.map_markers["fadezone"].spawn_points[0].ends_at == datetime(2026, 7, 14, 12)


def test_spawn_and_way_point_survive_zone_switch(qtbot, store, synthetic_maps) -> None:  # noqa: F811
    canvas = canvas_with_store(qtbot, store)
    canvas.create_spawn_point(10.0, 20.0, 120)
    canvas.set_way_point(30.0, 40.0)

    canvas.load_map("allzone")
    assert canvas._data.spawns == []
    assert canvas._data.way_point is None

    canvas.load_map("fadezone")
    assert len(canvas._data.spawns) == 1
    spawn = canvas._data.spawns[0]
    assert (spawn.location.x, spawn.location.y, spawn.length) == (10.0, 20.0, 120)
    assert canvas._data.way_point is not None
    assert (canvas._data.way_point.location.x, canvas._data.way_point.location.y) == (30.0, 40.0)


def test_running_spawn_restores_absolute_end_time(qtbot, store, synthetic_maps) -> None:  # noqa: F811
    canvas = canvas_with_store(qtbot, store)
    canvas.create_spawn_point(10.0, 20.0, 3600)
    end_time = canvas._data.spawns[0]._end_time
    canvas.load_map("allzone")
    canvas.load_map("fadezone")
    assert canvas._data.spawns[0]._end_time == end_time


def test_expired_spawn_restores_idle(qtbot, store, synthetic_maps) -> None:  # noqa: F811
    store._settings.map_markers["fadezone"] = ZoneMarkers(
        spawn_points=[
            SpawnMarker(
                x=5.0,
                y=6.0,
                z=0.0,
                length_s=60,
                ends_at=datetime.now() - timedelta(seconds=30),
            )
        ]
    )
    canvas = canvas_with_store(qtbot, store)
    spawn = canvas._data.spawns[0]
    assert getattr(spawn, "_end_time", None) is None  # idle "POP" state


def test_deleting_everything_drops_the_zone_entry(qtbot, store, synthetic_maps) -> None:  # noqa: F811
    canvas = canvas_with_store(qtbot, store)
    canvas.create_spawn_point(10.0, 20.0, 120)
    assert "fadezone" in store._settings.map_markers
    canvas.clear_spawn_points()
    assert "fadezone" not in store._settings.map_markers
