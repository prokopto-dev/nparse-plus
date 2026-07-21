"""Setting-gated render trade-offs: map antialiasing, band pixmap cache,
overlay alert-text shadow (all default to the pre-existing look)."""

from __future__ import annotations

import pytest
from tests.ui.test_maps_zfade import (  # reuse the synthetic-map harness
    make_canvas,
    synthetic_maps,  # noqa: F401 - pytest fixture
)

from nparseplus.helpers import config
from nparseplus.parsers.maps.mapcanvas import MapCanvas
from nparseplus.ui.eventoverlay import EventOverlayWindow

pytestmark = pytest.mark.qt


def test_antialias_defaults_on_and_can_be_disabled(qtbot, synthetic_maps) -> None:  # noqa: F811
    from PySide6.QtGui import QPainter

    canvas = make_canvas(qtbot, "fadezone")
    assert canvas.renderHints() & QPainter.RenderHint.Antialiasing

    config.data["maps"]["antialias"] = False
    plain = MapCanvas()
    qtbot.addWidget(plain)
    assert not (plain.renderHints() & QPainter.RenderHint.Antialiasing)


def test_band_cache_opt_in(qtbot, synthetic_maps) -> None:  # noqa: F811
    from PySide6.QtWidgets import QGraphicsItem

    canvas = make_canvas(qtbot, "fadezone")
    band = canvas._data[next(iter(canvas._data.keys()))]["paths"]
    assert band.cacheMode() == QGraphicsItem.CacheMode.NoCache  # default off

    config.data["maps"]["band_cache"] = True
    try:
        cached = make_canvas(qtbot, "fadezone")
        band = cached._data[next(iter(cached._data.keys()))]["paths"]
        assert band.cacheMode() == QGraphicsItem.CacheMode.DeviceCoordinateCache
    finally:
        config.data["maps"]["band_cache"] = False


def test_overlay_shadow_setting(qtbot) -> None:
    with_shadow = EventOverlayWindow()
    qtbot.addWidget(with_shadow)
    assert with_shadow._center_text.graphicsEffect() is not None  # default look

    without = EventOverlayWindow(text_shadow=False)
    qtbot.addWidget(without)
    assert without._center_text.graphicsEffect() is None
