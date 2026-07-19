"""EQTool-style continuous z-fade tests: pure curve + offscreen MapCanvas."""

from __future__ import annotations

import math
from datetime import datetime

import pytest

from nparseplus.core.zones import ZoneDatabase, ZoneInfo
from nparseplus.helpers import config
from nparseplus.parsers.maps import mapdata as mapdata_module
from nparseplus.parsers.maps.mapcanvas import MapCanvas
from nparseplus.parsers.maps.mapclasses import MapPoint, scaled_font_size
from nparseplus.parsers.maps.mapdata import MapData
from nparseplus.parsers.maps.zfade import band_center, band_key_for, band_width_for, fade_opacity

pytestmark = pytest.mark.qt


# --- pure fade curve ---------------------------------------------------------


def test_fade_fully_opaque_below_height() -> None:
    assert fade_opacity(0, 10) == 1.0
    assert fade_opacity(9.99, 10) == 1.0


def test_fade_clamped_at_inner_edge() -> None:
    # d == h computes 1.1 before clamping.
    assert fade_opacity(10, 10) == 1.0


def test_fade_linear_curve_values() -> None:
    # opacity = ((2h) - (d - h)) / (2h) + 0.1
    assert fade_opacity(15, 10) == pytest.approx(0.85)
    assert fade_opacity(20, 10) == pytest.approx(0.6)
    assert fade_opacity(25, 10) == pytest.approx(0.35)
    assert fade_opacity(30, 10) == pytest.approx(0.1)


def test_fade_floor_beyond_three_heights() -> None:
    assert fade_opacity(31, 10) == 0.1
    assert fade_opacity(1000, 10) == 0.1


def test_fade_negative_distance_uses_absolute_value() -> None:
    assert fade_opacity(-20, 10) == pytest.approx(0.6)


def test_fade_disabled_without_zone_height() -> None:
    assert fade_opacity(500, None) == 1.0
    assert fade_opacity(500, 0) == 1.0


def test_fade_min_opacity_moves_the_floor() -> None:
    assert fade_opacity(1000, 10, min_opacity=0.4) == pytest.approx(0.4)
    # The linear segment is offset by the floor too (EQTool adds it in).
    assert fade_opacity(20, 10, min_opacity=0.4) == pytest.approx(0.9)


def test_fade_strength_scales_effective_height() -> None:
    # strength 2.0 halves the effective height: h=5 for a 10-unit zone.
    assert fade_opacity(4.9, 10, strength=2.0) == 1.0
    assert fade_opacity(10, 10, strength=2.0) == pytest.approx(0.6)
    # strength 0.5 doubles it: d=20 is the inner edge, still opaque.
    assert fade_opacity(19.9, 10, strength=0.5) == 1.0
    # Degenerate strength never divides by zero.
    assert fade_opacity(100, 10, strength=0) == 1.0


def test_fade_fallback_height_fades_metadata_less_zones() -> None:
    assert fade_opacity(5, None, fallback_height=10) == 1.0
    assert fade_opacity(500, None, fallback_height=10) == pytest.approx(0.1)
    # Real zone metadata always wins over the fallback.
    assert fade_opacity(25, 10, fallback_height=100) == pytest.approx(0.35)


def test_band_helpers() -> None:
    assert band_width_for(None) == 10.0
    assert band_width_for(10) == 5.0
    assert band_width_for(4) == 5.0
    assert band_width_for(30) == 15.0
    assert band_key_for(0.0, 5.0) == 0
    assert band_key_for(-0.1, 5.0) == -1
    assert band_center(20, 5.0) == pytest.approx(102.5)
    assert math.isclose(band_center(band_key_for(7.0, 5.0), 5.0), 7.5)


# --- offscreen MapCanvas band fading ----------------------------------------

MAP_BODY = """L 0, 0, 0, 100, 0, 0, 255, 255, 255
L 0, 10, 0, 100, 10, 0, 255, 255, 255
L 0, 0, 100, 100, 0, 100, 255, 0, 0
L 0, 10, 100, 100, 10, 100, 255, 0, 0
P 50, 5, 0, 255, 0, 0, 3, King_Arthur
P 50, 5, 100, 255, 0, 0, 3, Roof_Boss
"""


@pytest.fixture
def synthetic_maps(tmp_path, monkeypatch):
    map_dir = tmp_path / "map_files"
    map_dir.mkdir()
    (map_dir / "fadezone.txt").write_text(MAP_BODY)
    (map_dir / "allzone.txt").write_text(MAP_BODY)
    (map_dir / "nozone.txt").write_text(MAP_BODY)
    timers = tmp_path / "map_timers.csv"
    timers.write_text("fadezone,6:40\nallzone,6:40\nnozone,6:40\n")

    zone_db = ZoneDatabase(
        zones={
            "fadezone": ZoneInfo(name="fadezone", zone_level_height=10),
            "allzone": ZoneInfo(name="allzone", zone_level_height=10, show_all_map_levels=True),
            "nozone": ZoneInfo(name="nozone"),  # no level metadata
        },
        boats=[],
        kael_faction_mobs=[],
        zone_name_mapper={"fadezone": "fadezone", "allzone": "allzone", "nozone": "nozone"},
        zone_who_mapper={},
    )

    monkeypatch.setattr(mapdata_module, "MAP_FILES_PATHLIB", map_dir)
    monkeypatch.setattr(mapdata_module, "MAP_SPAWNTIMES_FILE", str(timers))
    monkeypatch.setattr(
        MapData,
        "get_zone_dict",
        staticmethod(lambda: {"fadezone": "fadezone", "allzone": "allzone", "nozone": "nozone"}),
    )
    monkeypatch.setattr(mapdata_module, "load_zone_database", lambda: zone_db)

    config.load(str(tmp_path / "nparse.config.json"))
    config.verify_settings()
    config.data["maps"]["use_z_layers"] = False
    return zone_db


def make_canvas(qtbot, zone: str) -> MapCanvas:
    canvas = MapCanvas()
    qtbot.addWidget(canvas)
    canvas.load_map(zone)
    assert canvas._data is not None, f"synthetic map {zone!r} failed to load"
    return canvas


def band_opacity(canvas: MapCanvas, z: float) -> float:
    return canvas._data[canvas._data.band_key_for_z(z)]["paths"].opacity()


def test_band_opacities_follow_player_z(qtbot, synthetic_maps) -> None:
    canvas = make_canvas(qtbot, "fadezone")
    canvas.add_player("__you__", datetime.now(), MapPoint(x=0.0, y=0.0, z=0.0))

    # z=0 band center is 2.5 (width 5): d=2.5 < 10 -> fully opaque.
    assert band_opacity(canvas, 0.0) == pytest.approx(1.0)
    # z=100 band center is 102.5: d=102.5 > 3h -> floor of 0.1.
    assert band_opacity(canvas, 100.0) == pytest.approx(0.1)


def test_band_opacities_track_player_moving_up(qtbot, synthetic_maps) -> None:
    canvas = make_canvas(qtbot, "fadezone")
    canvas.add_player("__you__", datetime.now(), MapPoint(x=0.0, y=0.0, z=100.0))

    assert band_opacity(canvas, 100.0) == pytest.approx(1.0)
    assert band_opacity(canvas, 0.0) == pytest.approx(0.1)


def test_poi_labels_fade_by_their_own_z(qtbot, synthetic_maps) -> None:
    canvas = make_canvas(qtbot, "fadezone")
    canvas.add_player("__you__", datetime.now(), MapPoint(x=0.0, y=0.0, z=0.0))

    pois = {p.location.text: p for band_key in canvas._data for p in canvas._data[band_key]["poi"]}
    assert pois["King_Arthur"].text.opacity() == pytest.approx(1.0)
    assert pois["Roof_Boss"].text.opacity() == pytest.approx(0.1)


def test_poi_entries_expose_labels_for_search(qtbot, synthetic_maps) -> None:
    # Regression guard for #6: poi_entries() reads the persisted POI rows
    # (formerly MapData.raw["poi"]) that feed the NPC-search index in window.py.
    data = MapData(zone="fadezone")
    assert data.poi_entries() == [
        ("King_Arthur", 50.0, 5.0, 0.0),
        ("Roof_Boss", 50.0, 5.0, 100.0),
    ]
    # A zone-less MapData never runs _load(); poi_entries() is still safe/empty.
    assert MapData().poi_entries() == []


def test_player_marker_stays_fully_opaque(qtbot, synthetic_maps) -> None:
    canvas = make_canvas(qtbot, "fadezone")
    canvas.add_player("__you__", datetime.now(), MapPoint(x=0.0, y=0.0, z=0.0))
    assert canvas._data.players["__you__"].opacity() == pytest.approx(1.0)


def test_show_all_map_levels_zone_never_fades(qtbot, synthetic_maps) -> None:
    canvas = make_canvas(qtbot, "allzone")
    canvas.add_player("__you__", datetime.now(), MapPoint(x=0.0, y=0.0, z=0.0))

    assert band_opacity(canvas, 0.0) == pytest.approx(1.0)
    assert band_opacity(canvas, 100.0) == pytest.approx(1.0)


def test_no_fading_before_first_player_location(qtbot, synthetic_maps) -> None:
    canvas = make_canvas(qtbot, "fadezone")
    assert band_opacity(canvas, 0.0) == pytest.approx(1.0)
    assert band_opacity(canvas, 100.0) == pytest.approx(1.0)


def test_z_fade_can_be_disabled(qtbot, synthetic_maps) -> None:
    config.data["maps"]["z_fade_enabled"] = False
    canvas = make_canvas(qtbot, "fadezone")
    canvas.add_player("__you__", datetime.now(), MapPoint(x=0.0, y=0.0, z=0.0))
    assert band_opacity(canvas, 100.0) == pytest.approx(1.0)


def test_z_fade_min_opacity_setting(qtbot, synthetic_maps) -> None:
    config.data["maps"]["z_fade_min_opacity"] = 40
    canvas = make_canvas(qtbot, "fadezone")
    canvas.add_player("__you__", datetime.now(), MapPoint(x=0.0, y=0.0, z=0.0))
    assert band_opacity(canvas, 100.0) == pytest.approx(0.4)


def test_metadata_less_zone_fades_only_with_fallback(qtbot, synthetic_maps) -> None:
    canvas = make_canvas(qtbot, "nozone")
    canvas.add_player("__you__", datetime.now(), MapPoint(x=0.0, y=0.0, z=0.0))
    # Default fallback 0: EQTool behavior, no fading.
    assert band_opacity(canvas, 100.0) == pytest.approx(1.0)

    config.data["maps"]["z_fade_fallback_height"] = 10
    canvas.update_()
    assert band_opacity(canvas, 100.0) == pytest.approx(0.1)
    assert band_opacity(canvas, 0.0) == pytest.approx(1.0)


def test_scaled_font_size_clamps_to_html_range(synthetic_maps) -> None:
    config.data["maps"]["map_font_scale"] = 100
    assert scaled_font_size(4) == 4
    config.data["maps"]["map_font_scale"] = 150
    assert scaled_font_size(4) == 6
    config.data["maps"]["map_font_scale"] = 200
    assert scaled_font_size(5) == 7  # clamped at HTML max
    config.data["maps"]["map_font_scale"] = 50
    assert scaled_font_size(1) == 1  # clamped at HTML min


def test_map_font_scale_rerenders_poi_labels(qtbot, synthetic_maps) -> None:
    canvas = make_canvas(qtbot, "fadezone")
    pois = {p.location.text: p for bk in canvas._data for p in canvas._data[bk]["poi"]}
    poi = pois["King_Arthur"]
    base_width = poi.text.boundingRect().width()

    config.data["maps"]["map_font_scale"] = 200
    canvas.update_()
    assert poi.text.boundingRect().width() > base_width


def test_use_z_layers_keeps_tiered_behavior(qtbot, synthetic_maps) -> None:
    config.data["maps"]["use_z_layers"] = True
    canvas = make_canvas(qtbot, "fadezone")
    canvas.add_player("__you__", datetime.now(), MapPoint(x=0.0, y=0.0, z=0.0))

    current = config.data["maps"]["current_z_alpha"] / 100
    assert band_opacity(canvas, 0.0) == pytest.approx(current)
    # The far band belongs to another z-group tier, not the smooth curve.
    other_band = band_opacity(canvas, 100.0)
    assert other_band in (
        pytest.approx(config.data["maps"]["closest_z_alpha"] / 100),
        pytest.approx(config.data["maps"]["other_z_alpha"] / 100),
    )
