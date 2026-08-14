"""How the map is panned (#115), and what that must not break.

The gesture used to be Ctrl+drag only, with nothing on screen saying so — a
plain left-drag did nothing at all. ``maps.pan_mode`` makes plain drag the
default and keeps Ctrl+drag working under either value.

The tests that matter most here are the ones guarding the trap: ``wheelEvent``
used to branch on ``dragMode()`` as a proxy for "is Ctrl held", so any
implementation that leaves ScrollHandDrag set would silently stop the wheel
zooming and start it walking the Z layers instead.
"""

from __future__ import annotations

import math

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QFocusEvent, QKeyEvent, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication, QGraphicsScene, QGraphicsView

from nparseplus.helpers import config
from nparseplus.helpers.settings import SettingsSignals
from nparseplus.parsers.maps import chrome
from nparseplus.parsers.maps.mapcanvas import BACKDROP_EDGE_PX, MapCanvas, pan_on_press
from nparseplus.parsers.maps.window import Maps

pytestmark = pytest.mark.qt

CTRL = Qt.KeyboardModifier.ControlModifier
NONE = Qt.KeyboardModifier.NoModifier
HAND = QGraphicsView.DragMode.ScrollHandDrag
NO_DRAG = QGraphicsView.DragMode.NoDrag


@pytest.fixture
def canvas(qtbot, tmp_path):
    """A loaded, resized MapCanvas over a scratch legacy config."""
    config.load(str(tmp_path / "nparse.config.json"))
    config.verify_settings()
    widget = MapCanvas()
    qtbot.addWidget(widget)
    widget.resize(400, 300)
    widget.load_map("west freeport")
    return widget


# -- synthetic input ------------------------------------------------------------
#
# Built and sent by hand rather than through QTest: the pan is decided from the
# event's own modifiers and position, and Qt's hand-scroll works off the deltas
# between them, so nothing here needs a real cursor.
#
# Handed straight to the handler rather than posted at the viewport, which is
# all QAbstractScrollArea's own forwarding would do with them anyway.
# QApplication::notify DISCARDS a wheel event while any popup is active, so a
# menu another test left open would silently make these assert nothing.

_DISPATCH = {
    QEvent.Type.MouseButtonPress: "mousePressEvent",
    QEvent.Type.MouseMove: "mouseMoveEvent",
    QEvent.Type.MouseButtonRelease: "mouseReleaseEvent",
    QEvent.Type.Wheel: "wheelEvent",
}


def _mouse(kind, x, y, button, buttons, modifiers):
    return QMouseEvent(kind, QPointF(x, y), QPointF(x, y), button, buttons, modifiers)


def _send(canvas, event):
    getattr(canvas, _DISPATCH[event.type()])(event)


def _center(canvas):
    """Where the middle of the viewport lands in scene coordinates."""
    return canvas.mapToScene(canvas.viewport().rect().center())


def _drag(canvas, start=(200, 150), delta=(60, 40), modifiers=NONE):
    """Press, move, release. Returns how far the view travelled, in scene units."""
    before = _center(canvas)
    left = Qt.MouseButton.LeftButton
    none = Qt.MouseButton.NoButton
    _send(canvas, _mouse(QEvent.Type.MouseButtonPress, *start, left, left, modifiers))
    for step in (1, 2):
        moved = (start[0] + delta[0] * step / 2, start[1] + delta[1] * step / 2)
        _send(canvas, _mouse(QEvent.Type.MouseMove, *moved, none, left, modifiers))
    end = (start[0] + delta[0], start[1] + delta[1])
    _send(canvas, _mouse(QEvent.Type.MouseButtonRelease, *end, left, none, modifiers))
    after = _center(canvas)
    return math.hypot(after.x() - before.x(), after.y() - before.y())


def _wheel(canvas, notches=1, at=(200, 150), modifiers=NONE):
    _send(
        canvas,
        QWheelEvent(
            QPointF(*at),
            QPointF(*at),
            QPoint(0, 0),
            QPoint(0, 120 * notches),
            Qt.MouseButton.NoButton,
            modifiers,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        ),
    )


def _key(kind, key, modifiers):
    return QKeyEvent(kind, key, modifiers)


# -- the decision, on its own ---------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "ctrl", "on_chrome", "in_band", "expected"),
    [
        # Plain drag: the new default. Anywhere on the map but the edge band.
        (config.PAN_DRAG, False, False, False, True),
        (config.PAN_DRAG, False, False, True, False),
        # Ctrl+drag is the EXPLICIT gesture: it pans under either setting, and
        # the edge band does not hold it off — it never did.
        (config.PAN_DRAG, True, False, True, True),
        (config.PAN_CTRL_DRAG, True, False, False, True),
        (config.PAN_CTRL_DRAG, True, False, True, True),
        # …and without it, the Ctrl setting pans nowhere.
        (config.PAN_CTRL_DRAG, False, False, False, False),
        # Chrome outranks everything: you are pressing a header, not the map.
        (config.PAN_DRAG, False, True, False, False),
        (config.PAN_DRAG, True, True, False, False),
        (config.PAN_CTRL_DRAG, True, True, False, False),
    ],
)
def test_the_pan_decision(mode, ctrl, on_chrome, in_band, expected) -> None:
    assert pan_on_press(mode, ctrl, on_chrome, in_band) is expected


def test_plain_drag_is_the_default(tmp_path) -> None:
    """It was inert before, so switching it on takes no capability away — and
    it is the whole point of the setting."""
    config.load(str(tmp_path / "nparse.config.json"))
    config.verify_settings()
    assert config.data["maps"]["pan_mode"] == config.PAN_DRAG


def test_an_unknown_pan_mode_reads_as_the_default(canvas) -> None:
    config.data["maps"]["pan_mode"] = "wiggle"
    assert canvas.pan_mode() == config.PAN_DRAG


# -- panning --------------------------------------------------------------------


def test_plain_drag_pans_when_the_setting_says_so(canvas) -> None:
    config.data["maps"]["pan_mode"] = config.PAN_DRAG
    assert _drag(canvas) > 0


def test_plain_drag_pans_nowhere_when_the_setting_says_ctrl(canvas) -> None:
    config.data["maps"]["pan_mode"] = config.PAN_CTRL_DRAG
    assert _drag(canvas) == 0


@pytest.mark.parametrize("mode", [config.PAN_DRAG, config.PAN_CTRL_DRAG])
def test_ctrl_drag_pans_under_both_settings(canvas, mode) -> None:
    """The gesture people already know keeps working either way — that is why
    the new default takes nothing away."""
    config.data["maps"]["pan_mode"] = mode
    assert _drag(canvas, modifiers=CTRL) > 0


def test_the_setting_applies_live(canvas) -> None:
    """Read at press time, not cached at construction: Settings > Apply reaches
    the map that is already open."""
    config.data["maps"]["pan_mode"] = config.PAN_CTRL_DRAG
    assert _drag(canvas) == 0
    config.data["maps"]["pan_mode"] = config.PAN_DRAG
    assert _drag(canvas) > 0


def test_a_plain_drag_from_the_edge_band_does_not_pan(canvas) -> None:
    """The band is where the wheel nudges the backdrop and where a frameless
    window's border is; an implicit gesture stands off it."""
    config.data["maps"]["pan_mode"] = config.PAN_DRAG
    assert _drag(canvas, start=(BACKDROP_EDGE_PX - 5, 150)) == 0
    # Ctrl+drag is explicit and still pans from there, as it always has.
    assert _drag(canvas, start=(BACKDROP_EDGE_PX - 5, 150), modifiers=CTRL) > 0


def test_a_drag_that_starts_on_chrome_does_not_pan(canvas) -> None:
    """The header sits OVER the canvas: pressing it is not pressing the map."""
    config.data["maps"]["pan_mode"] = config.PAN_DRAG
    header = QRect(0, 0, 400, 60)
    canvas.chrome_hit_test = header.contains
    assert _drag(canvas, start=(200, 30)) == 0
    assert _drag(canvas, start=(200, 150)) > 0


def test_the_pan_leaves_no_drag_mode_behind(canvas) -> None:
    config.data["maps"]["pan_mode"] = config.PAN_DRAG
    _drag(canvas)
    assert canvas.dragMode() == NO_DRAG


# -- the wheel, decoupled from the drag mode ------------------------------------


def test_the_wheel_still_zooms_while_the_view_is_armed_for_panning(canvas) -> None:
    """THE regression this feature invites. ``wheelEvent`` used to read
    ``dragMode()`` as "is Ctrl held" — true only while Ctrl was the one thing
    that ever set it. Leave ScrollHandDrag set to pan and the wheel silently
    stops zooming and starts walking the Z layers instead."""
    canvas.setDragMode(HAND)
    zoomed_out = canvas._scale
    _wheel(canvas, 1)
    assert canvas._scale > zoomed_out
    zoomed_in = canvas._scale
    _wheel(canvas, -1)
    assert canvas._scale < zoomed_in
    # …and the Z layer — what the wheel used to do while armed — stayed put.
    assert canvas._z_index == 0


@pytest.mark.parametrize("mode", [config.PAN_DRAG, config.PAN_CTRL_DRAG])
def test_the_wheel_zooms_after_a_drag_in_either_mode(canvas, mode) -> None:
    config.data["maps"]["pan_mode"] = mode
    _drag(canvas, modifiers=CTRL if mode == config.PAN_CTRL_DRAG else NONE)
    before = canvas._scale
    _wheel(canvas, 1)
    assert canvas._scale > before


def test_ctrl_wheel_steps_the_z_layer_and_does_not_zoom(canvas) -> None:
    canvas._z_index = 0
    before = canvas._scale
    _wheel(canvas, -1, modifiers=CTRL)
    assert canvas._z_index == 1
    assert canvas._scale == before


def test_the_edge_band_wheel_still_nudges_the_backdrop(canvas) -> None:
    config.data["maps"]["pan_mode"] = config.PAN_DRAG
    canvas.set_backdrop_opacity(50)
    before = canvas._scale
    _wheel(canvas, 1, at=(5, 150))
    assert canvas.backdrop_opacity() > 50
    assert canvas._scale == before


# -- the drag mode is never left stuck ------------------------------------------


def test_holding_ctrl_arms_the_hand_cursor(qtbot, canvas) -> None:
    """The affordance for the explicit gesture, driven the way the app gets it:
    through QTest, because Qt normalizes a hand-built KeyPress(Key_Control,
    ControlModifier) down to NoModifier while a real one carries it."""
    canvas.show()
    qtbot.waitExposed(canvas)
    canvas.setFocus()
    qtbot.keyPress(canvas, Qt.Key.Key_Control, CTRL)
    assert canvas.dragMode() == HAND
    qtbot.keyRelease(canvas, Qt.Key.Key_Control, NONE)
    assert canvas.dragMode() == NO_DRAG


def test_ctrl_arms_the_hand_even_with_another_modifier_down(canvas) -> None:
    """``modifiers() == ControlModifier`` was exact, so Ctrl+Shift — a hand
    resting on the keyboard — silently failed to arm anything."""
    shift = Qt.KeyboardModifier.ShiftModifier
    canvas.keyPressEvent(_key(QEvent.Type.KeyPress, Qt.Key.Key_Shift, CTRL | shift))
    assert canvas.dragMode() == HAND
    canvas.keyReleaseEvent(_key(QEvent.Type.KeyRelease, Qt.Key.Key_Control, NONE))
    assert canvas.dragMode() == NO_DRAG


def test_losing_focus_while_ctrl_is_held_disarms_the_view(canvas) -> None:
    """Alt-tab (or a click into the game) with Ctrl down never delivers the key
    release, which left the view stuck in ScrollHandDrag for the session."""
    canvas.keyPressEvent(_key(QEvent.Type.KeyPress, Qt.Key.Key_F, CTRL))
    assert canvas.dragMode() == HAND
    canvas.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
    assert canvas.dragMode() == NO_DRAG


class _RecordingScene(QGraphicsScene):
    """Counts the key events the view forwards, by kind."""

    def __init__(self) -> None:
        super().__init__()
        self.presses = 0
        self.releases = 0

    def keyPressEvent(self, event) -> None:
        self.presses += 1

    def keyReleaseEvent(self, event) -> None:
        self.releases += 1


def test_a_key_release_reaches_the_scene_as_a_release(canvas) -> None:
    """``keyReleaseEvent`` called ``QGraphicsView.keyPressEvent`` — the wrong
    super — so every key release arrived at the scene as a fresh key PRESS."""
    scene = _RecordingScene()
    canvas.setScene(scene)  # the map is done with; only the forwarding matters
    canvas.keyReleaseEvent(_key(QEvent.Type.KeyRelease, Qt.Key.Key_Control, NONE))
    assert (scene.presses, scene.releases) == (0, 1)


# -- the chrome hit test, on the real window ------------------------------------


def test_covers_point_is_pure_geometry() -> None:
    rects = [QRect(0, 0, 400, 60), QRect(0, 260, 400, 40)]
    assert chrome.covers_point(QPoint(200, 30), rects)
    assert chrome.covers_point(QPoint(200, 280), rects)
    assert not chrome.covers_point(QPoint(200, 150), rects)
    assert not chrome.covers_point(QPoint(200, 30), [])


def test_the_window_reports_only_the_chrome_that_is_up(qtbot, tmp_path, monkeypatch) -> None:
    config.load(str(tmp_path / "nparse.config.json"))
    config.verify_settings()
    config.data["maps"]["last_zone"] = "oasis of marr"
    config.data["maps"]["toggled"] = False
    monkeypatch.setattr(config, "save", lambda: None)
    app = QApplication.instance()
    if not hasattr(app, "_signals"):
        app._signals = {"settings": SettingsSignals()}
    window = Maps()
    qtbot.addWidget(window)
    window.resize(500, 420)
    window.show()
    qtbot.waitExposed(window)

    assert window._map.chrome_hit_test == window._chrome_covers
    under_header = QPoint(250, 5)
    # The chrome is out of the way until the pointer arrives.
    assert not window._chrome_covers(under_header)
    window.enterEvent(QEvent(QEvent.Type.Enter))
    assert window._chrome_covers(under_header)
    assert not window._chrome_covers(QPoint(250, 200))
