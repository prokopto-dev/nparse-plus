"""Per-region repositioning for the event overlay (Phase 2).

Each region (CH lanes / alert text / timer bars) can be placed independently
inside the overlay window. ``overlay_regions=None`` keeps the legacy stacked
QVBoxLayout; a populated dict switches to manual anchor/dx/dy placement.
"""

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent

from nparseplus.config.settings import OverlayRegion, WindowState
from nparseplus.ui.eventoverlay import (
    BAR_WIDTH,
    REGION_MARGIN_TOP,
    EventOverlayWindow,
)

pytestmark = pytest.mark.qt


def _press(overlay: EventOverlayWindow, x: int, y: int) -> None:
    pt = QPointF(x, y)
    ev = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        pt,
        pt,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    overlay.mousePressEvent(ev)


def _move(overlay: EventOverlayWindow, x: int, y: int) -> None:
    pt = QPointF(x, y)
    ev = QMouseEvent(
        QEvent.Type.MouseMove,
        pt,
        pt,
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    overlay.mouseMoveEvent(ev)


def test_overlay_region_roundtrip_through_window_state() -> None:
    ws = WindowState(
        overlay_regions={
            "lanes": OverlayRegion(anchor="top", dx=12, dy=-5, width=540),
            "bars": OverlayRegion(anchor="bottom"),
        }
    )
    restored = WindowState.model_validate_json(ws.model_dump_json())
    assert restored.overlay_regions is not None
    assert restored.overlay_regions["lanes"] == OverlayRegion(anchor="top", dx=12, dy=-5, width=540)
    assert restored.overlay_regions["bars"].width is None


def test_none_regions_uses_stacked_layout(qtbot) -> None:
    state = WindowState(geometry=(0, 0, 1000, 800))
    overlay = EventOverlayWindow(state=state)
    qtbot.addWidget(overlay)

    assert not overlay._region_mode()
    # All three hosts are managed by the stacked QVBoxLayout.
    for host in overlay._region_hosts().values():
        assert overlay._main_layout.indexOf(host) >= 0


def test_regions_place_hosts_by_anchor_math(qtbot) -> None:
    state = WindowState(
        geometry=(0, 0, 1000, 800),
        overlay_regions={
            "lanes": OverlayRegion(anchor="top", dx=20, dy=4, width=520),
            "alert": OverlayRegion(anchor="center", dx=-30, dy=10, width=200),
            "bars": OverlayRegion(anchor="bottom", width=BAR_WIDTH),
        },
    )
    overlay = EventOverlayWindow(state=state)
    qtbot.addWidget(overlay)
    overlay._layout_regions()

    cx = 1000 // 2
    lanes = overlay._lanes_host
    assert lanes.x() == cx + 20 - 520 // 2
    assert lanes.y() == REGION_MARGIN_TOP + 4

    alert = overlay._alert_host
    assert alert.x() == cx - 30 - 200 // 2
    assert alert.y() == 800 // 2 + 10


def test_region_drag_updates_and_persists(qtbot) -> None:
    saves: list[int] = []
    state = WindowState(
        geometry=(0, 0, 1000, 800),
        overlay_regions={
            "lanes": OverlayRegion(anchor="top", dx=0, dy=0, width=520),
            "alert": OverlayRegion(anchor="center"),
            "bars": OverlayRegion(anchor="bottom"),
        },
    )
    overlay = EventOverlayWindow(state=state, on_save=lambda: saves.append(1))
    qtbot.addWidget(overlay)
    overlay.set_edit_mode(True)
    overlay._layout_regions()

    lanes = overlay._lanes_host
    # Press inside the (top-anchored, deterministic) lanes host, then drag.
    _press(overlay, lanes.x() + 10, lanes.y() + 5)
    assert overlay._drag_region == "lanes"
    _move(overlay, lanes.x() + 10 + 25, lanes.y() + 5 + 15)

    region = overlay._state.overlay_regions["lanes"]
    assert region.dx == 25
    assert region.dy == 15

    overlay.set_edit_mode(False)
    # Persisted through the shared _state/_on_save path.
    assert saves == [1]
    assert overlay._state.overlay_regions["lanes"].dx == 25
    assert overlay._state.overlay_regions["lanes"].dy == 15


def test_whole_window_drag_when_press_misses_regions(qtbot) -> None:
    state = WindowState(
        geometry=(0, 0, 1000, 800),
        overlay_regions={
            "lanes": OverlayRegion(anchor="top", width=520),
            "alert": OverlayRegion(anchor="center"),
            "bars": OverlayRegion(anchor="bottom"),
        },
    )
    overlay = EventOverlayWindow(state=state)
    qtbot.addWidget(overlay)
    overlay.set_edit_mode(True)
    overlay._layout_regions()

    # A gap that misses all three region hosts (right side, mid-height).
    assert overlay._region_at(QPoint(900, 300)) is None
    _press(overlay, 900, 300)
    assert overlay._drag_region is None
    assert overlay._drag_offset is not None

    _move(overlay, 950, 340)
    assert (overlay.x(), overlay.y()) == (50, 40)


def test_first_region_drag_initializes_defaults(qtbot) -> None:
    # Starting from the legacy layout (regions None), the first region drag
    # seeds overlay_regions with stacked-position defaults so untouched
    # regions keep their places.
    state = WindowState(geometry=(0, 0, 1000, 800))
    overlay = EventOverlayWindow(state=state)
    qtbot.addWidget(overlay)
    assert overlay._state.overlay_regions is None

    overlay._begin_region_drag("bars", QPoint(500, 700))
    regions = overlay._state.overlay_regions
    assert regions is not None
    assert set(regions) == {"lanes", "alert", "bars"}
    assert regions["lanes"].anchor == "top"
    assert regions["alert"].anchor == "center"
    assert regions["bars"].anchor == "bottom"
    assert overlay._region_mode()
