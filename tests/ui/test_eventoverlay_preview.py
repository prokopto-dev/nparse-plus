"""Positioning-mode preview for the event overlay (Phase 1).

Entering edit mode fills each region with labeled sample content so the user
can see where CH lanes, alerts, and timer bars appear — without touching any
live state (``_chain_lanes``/``_bars``/``_center_text``) or publishing events.
"""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QProgressBar

from nparseplus.core.events import TimerBarEvent
from nparseplus.ui.eventoverlay import CH_LANE_NAME_GAP, EventOverlayWindow

pytestmark = pytest.mark.qt


def _preview_alert_label(overlay: EventOverlayWindow) -> QLabel | None:
    for widget in overlay._preview_widgets:
        if isinstance(widget, QLabel) and "ENRAGED" in widget.text():
            return widget
    return None


def test_edit_mode_shows_sample_content_without_live_state(qtbot) -> None:
    overlay = EventOverlayWindow()
    qtbot.addWidget(overlay)

    overlay.set_edit_mode(True)

    # One sample lane row, two sample bars, one visible alert label.
    assert overlay._lanes_layout.count() == 1
    assert overlay._bars_layout.count() == 2
    label = _preview_alert_label(overlay)
    assert label is not None
    assert overlay.isVisible()

    # ...and zero leakage into live state.
    assert overlay._chain_lanes == {}
    assert overlay._bars == {}
    assert overlay.current_text() == ""
    # Preview bars are not registered as live bars.
    assert overlay.current_bar_names() == []
    # The bar timer must NOT be running for static preview bars.
    assert not overlay._bar_timer.isActive()


def test_exit_edit_mode_clears_preview_and_hides(qtbot) -> None:
    overlay = EventOverlayWindow()
    qtbot.addWidget(overlay)

    overlay.set_edit_mode(True)
    assert overlay._preview_widgets

    overlay.set_edit_mode(False)

    assert overlay._preview_widgets == []
    assert overlay._lanes_layout.count() == 0
    assert overlay._bars_layout.count() == 0
    assert overlay._chain_lanes == {}
    assert overlay._bars == {}
    # No live state -> the locked overlay hides itself.
    assert not overlay.isVisible()


def test_live_bar_during_edit_survives_exit(qtbot) -> None:
    overlay = EventOverlayWindow()
    qtbot.addWidget(overlay)

    overlay.set_edit_mode(True)
    # A real event arriving mid-edit must keep working and coexist.
    overlay.handle_event(TimerBarEvent(name="Stun Breath", total_seconds=12))
    assert "Stun Breath" in overlay._bars
    # 2 preview bars + 1 live bar.
    assert overlay._bars_layout.count() == 3

    overlay.set_edit_mode(False)

    # Preview bars gone; the live bar persists.
    assert overlay._preview_widgets == []
    assert overlay.current_bar_names() == ["Stun Breath"]
    assert overlay._bars_layout.count() == 1
    assert isinstance(overlay._bars_layout.itemAt(0).widget(), QProgressBar)


def test_double_toggle_does_not_duplicate(qtbot) -> None:
    overlay = EventOverlayWindow()
    qtbot.addWidget(overlay)

    overlay.set_edit_mode(True)
    overlay.set_edit_mode(True)  # idempotent
    assert overlay._lanes_layout.count() == 1
    assert overlay._bars_layout.count() == 2
    assert len(overlay._preview_widgets) == 4  # 1 row + 1 alert + 2 bars

    overlay.set_edit_mode(False)
    overlay.set_edit_mode(False)  # idempotent
    assert overlay._preview_widgets == []


def test_ch_lane_name_hugs_the_lane(qtbot) -> None:
    """The target name is right-aligned in its fixed column and sits an 8px gap
    from the lane, so a short name hugs the lane instead of floating far to the
    left of the column."""
    overlay = EventOverlayWindow()
    qtbot.addWidget(overlay)
    overlay.set_edit_mode(True)

    row = overlay._lanes_layout.itemAt(0).widget()
    layout = row.layout()
    # ~8px between the target name and the lane (the reported complaint).
    assert layout.spacing() == CH_LANE_NAME_GAP == 8

    # [name][lane]: the name is the first item and is right-aligned so it sits
    # next to the lane rather than at the far-left edge of its column.
    name = layout.itemAt(0).widget()
    assert isinstance(name, QLabel)
    assert bool(name.alignment() & Qt.AlignmentFlag.AlignRight)


def test_edit_hint_is_a_top_strip(qtbot) -> None:
    overlay = EventOverlayWindow()
    qtbot.addWidget(overlay)
    overlay.setGeometry(0, 0, 900, 700)

    overlay.set_edit_mode(True)

    assert overlay._edit_hint.isVisible()
    assert overlay._edit_hint.height() < overlay.height()
