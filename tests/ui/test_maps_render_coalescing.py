"""Coalesced map rendering (perf): location fixes schedule one repaint per
frame instead of running the full-scene update_() synchronously per fix."""

from datetime import datetime

from tests.ui.test_maps_zfade import (  # reuse the synthetic-map harness
    make_canvas,
    synthetic_maps,  # noqa: F401 - pytest fixture
)

from nparseplus.parsers.maps.mapclasses import MapPoint


def _fix(canvas, n, z=0.0):
    canvas.add_player("__you__", datetime.now(), MapPoint(x=float(n), y=float(n), z=z))


def test_burst_of_fixes_renders_once(qtbot, synthetic_maps, monkeypatch) -> None:  # noqa: F811
    canvas = make_canvas(qtbot, "fadezone")
    calls = []
    original = canvas.update_
    monkeypatch.setattr(canvas, "update_", lambda *a, **k: (calls.append(1), original(*a, **k)))

    for i in range(25):
        _fix(canvas, i)
    assert calls == []  # nothing rendered synchronously
    assert canvas._render_timer.isActive()

    canvas.flush_pending_render()
    assert len(calls) == 1  # the whole burst collapsed to one repaint
    assert not canvas._render_timer.isActive()


def test_flush_is_a_noop_when_nothing_pending(qtbot, synthetic_maps, monkeypatch) -> None:  # noqa: F811
    canvas = make_canvas(qtbot, "fadezone")
    calls = []
    monkeypatch.setattr(canvas, "update_", lambda *a, **k: calls.append(1))
    canvas.flush_pending_render()
    assert calls == []


def test_auto_follow_centers_on_flush(qtbot, synthetic_maps, monkeypatch) -> None:  # noqa: F811
    canvas = make_canvas(qtbot, "fadezone")
    centered = []
    monkeypatch.setattr(canvas, "center", lambda: centered.append(1))
    _fix(canvas, 1)
    assert centered == []  # deferred with the repaint
    canvas.flush_pending_render()
    assert centered == [1]
    # a plain scheduled render (no fix) must not re-center
    canvas._schedule_render()
    canvas.flush_pending_render()
    assert centered == [1]


def test_marker_moves_immediately_before_flush(qtbot, synthetic_maps) -> None:  # noqa: F811
    """The player's own marker must not lag the coalescing interval."""
    canvas = make_canvas(qtbot, "fadezone")
    _fix(canvas, 5)
    player = canvas._data.players["__you__"]
    assert (player.pos().x(), player.pos().y()) == (5.0, 5.0)


def test_nametag_html_set_once_across_fixes(qtbot, synthetic_maps) -> None:  # noqa: F811
    canvas = make_canvas(qtbot, "fadezone")
    _fix(canvas, 1)
    canvas.flush_pending_render()
    player = canvas._data.players["__you__"]
    calls = []
    original = player.nametag.setHtml
    player.nametag.setHtml = lambda html: (calls.append(html), original(html))
    for i in range(10):
        _fix(canvas, i + 2)
    canvas.flush_pending_render()
    assert calls == []  # name and font scale unchanged -> no HTML rebuilds


def test_poi_geometry_skipped_when_only_player_moves(qtbot, synthetic_maps, monkeypatch) -> None:  # noqa: F811
    canvas = make_canvas(qtbot, "fadezone")
    _fix(canvas, 1)
    canvas.flush_pending_render()

    pois = [p for band_key in canvas._data for p in canvas._data[band_key]["poi"]]
    assert pois, "synthetic zone should have POIs"
    calls = []
    for p in pois:
        monkeypatch.setattr(p, "update_", lambda *a, **k: calls.append(1))

    _fix(canvas, 2)
    canvas.flush_pending_render()
    assert calls == []  # scale/font unchanged -> geometry pass skipped

    canvas.update_(canvas._scale * 1.5)  # zoom -> geometry pass must run
    assert len(calls) == len(pois)
