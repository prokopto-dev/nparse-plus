"""Background zone loading: MapData.parse must stay scene-item-free (safe
off the GUI thread), stale loads must be discarded, and the async path must
install the same map the synchronous path does."""

from __future__ import annotations

import pytest
from tests.ui.test_maps_zfade import (  # reuse the synthetic-map harness
    make_canvas,
    synthetic_maps,  # noqa: F401 - pytest fixture
)

from nparseplus.parsers.maps import mapdata
from nparseplus.parsers.maps.mapdata import MapData

pytestmark = pytest.mark.qt


class _Poisoned:
    def __init__(self, *a, **k):
        raise AssertionError("GUI-thread-only Qt class constructed inside MapData.parse")


def test_parse_constructs_no_scene_items(qtbot, synthetic_maps, monkeypatch) -> None:  # noqa: F811
    """The off-thread half must never build QGraphics* items — poison them."""
    monkeypatch.setattr(mapdata, "QGraphicsPathItem", _Poisoned)
    monkeypatch.setattr(mapdata, "QGraphicsItemGroup", _Poisoned)
    monkeypatch.setattr(mapdata, "PointOfInterest", _Poisoned)
    parsed = MapData.parse("fadezone")
    assert parsed.zone == "fadezone"
    assert parsed.lines and parsed.geometry is not None and parsed.z_groups


def test_from_parsed_matches_synchronous_load(qtbot, synthetic_maps) -> None:  # noqa: F811
    sync = MapData(zone="fadezone")
    built = MapData.from_parsed(MapData.parse("fadezone"))
    assert built.zone == sync.zone
    assert built._z_groups == sync._z_groups
    assert sorted(built.keys()) == sorted(sync.keys())
    assert built.poi_entries() == sync.poi_entries()
    assert built.geometry.z_groups == sync.geometry.z_groups


def test_stale_generation_is_discarded(qtbot, synthetic_maps) -> None:  # noqa: F811
    canvas = make_canvas(qtbot, "fadezone")
    stale = MapData.parse("allzone")
    canvas._load_generation += 1  # a newer load superseded the in-flight one
    canvas._on_parsed_ready(stale, canvas._load_generation - 1, False)
    assert canvas._data.zone == "fadezone"  # unchanged

    fresh = MapData.parse("allzone")
    canvas._on_parsed_ready(fresh, canvas._load_generation, False)
    assert canvas._data.zone == "allzone"


def test_async_load_installs_on_delivery(qtbot, synthetic_maps) -> None:  # noqa: F811
    canvas = make_canvas(qtbot, "fadezone")
    canvas.load_map_async("allzone")
    qtbot.waitUntil(lambda: canvas._data.zone == "allzone", timeout=3000)
    # the scene was rebuilt for the new zone (bands + mouse-location present)
    assert canvas._data._z_groups


def test_sync_load_supersedes_inflight_async(qtbot, synthetic_maps) -> None:  # noqa: F811
    canvas = make_canvas(qtbot, "fadezone")
    generation = canvas._load_generation
    canvas.load_map("allzone")
    assert canvas._load_generation == generation + 1
    # a delivery from before the sync load must be ignored
    canvas._on_parsed_ready(MapData.parse("fadezone"), generation, False)
    assert canvas._data.zone == "allzone"
