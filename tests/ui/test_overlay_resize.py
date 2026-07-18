"""Window resizability tests: the shared corner grip, frameless edge/corner
drag-resize (``edge_at``/``cursor_for_edges`` + ``EdgeResizeMixin``), debounced
geometry persistence, minimum sizes, and the relaxed event-overlay min width.

Offscreen Qt via pytest-qt (``QT_QPA_PLATFORM=offscreen``). ``startSystemResize``
is a no-op under the offscreen platform, so these exercise the hit-detection,
cursor, and persistence wiring rather than a live native resize.
"""

from __future__ import annotations

import types
from datetime import datetime, timedelta

import pytest
from PySide6.QtCore import QPoint, QRect, Qt

from nparseplus.config.settings import Settings, WindowState
from nparseplus.core.dps import FightTracker
from nparseplus.core.events import CompleteHealEvent
from nparseplus.core.handlers.consider import MobInfoState
from nparseplus.core.player import ActivePlayer
from nparseplus.core.spells.models import Spell
from nparseplus.core.timers import YOU_GROUP, SpellRow, TimersService
from nparseplus.ui.dpswindow import WINDOW_KEY as DPS_KEY
from nparseplus.ui.dpswindow import DpsMeterWindow
from nparseplus.ui.eventoverlay import EventOverlayWindow
from nparseplus.ui.mobinfo import MobInfoWindow
from nparseplus.ui.overlaybase import RESIZE_MARGIN, cursor_for_edges, edge_at
from nparseplus.ui.spellwindow import WINDOW_KEY as SPELLS_KEY
from nparseplus.ui.spellwindow import SpellTimerWindow

pytestmark = pytest.mark.qt

NOW = datetime(2026, 7, 14, 12, 0, 0)


def _dps_backend(settings: Settings | None = None) -> types.SimpleNamespace:
    return types.SimpleNamespace(settings=settings or Settings(), fights=FightTracker())


def _spell_backend(settings: Settings | None = None) -> types.SimpleNamespace:
    timers = TimersService()
    timers.add_spell(
        SpellRow(
            name="Clarity",
            group=YOU_GROUP,
            updated_at=NOW,
            spell=Spell(id=1, name="Clarity"),
            ends_at=NOW + timedelta(minutes=35),
            total_duration_s=35 * 60.0,
        )
    )
    return types.SimpleNamespace(
        timers=timers, settings=settings or Settings(), player=ActivePlayer()
    )


# -- pure hit-testing helpers --------------------------------------------------

RECT = QRect(0, 0, 200, 100)  # right=199, bottom=99
M = RESIZE_MARGIN


def test_edge_at_interior_is_empty() -> None:
    assert not edge_at(QPoint(100, 50), RECT, M)  # Qt.Edge(0) is falsy


def test_edge_at_four_edges() -> None:
    assert edge_at(QPoint(2, 50), RECT, M) == Qt.Edge.LeftEdge
    assert edge_at(QPoint(197, 50), RECT, M) == Qt.Edge.RightEdge
    assert edge_at(QPoint(100, 2), RECT, M) == Qt.Edge.TopEdge
    assert edge_at(QPoint(100, 97), RECT, M) == Qt.Edge.BottomEdge


def test_edge_at_four_corners() -> None:
    assert edge_at(QPoint(2, 2), RECT, M) == (Qt.Edge.LeftEdge | Qt.Edge.TopEdge)
    assert edge_at(QPoint(197, 2), RECT, M) == (Qt.Edge.RightEdge | Qt.Edge.TopEdge)
    assert edge_at(QPoint(2, 97), RECT, M) == (Qt.Edge.LeftEdge | Qt.Edge.BottomEdge)
    assert edge_at(QPoint(197, 97), RECT, M) == (Qt.Edge.RightEdge | Qt.Edge.BottomEdge)


def test_edge_at_margin_boundaries() -> None:
    # The band is inclusive at exactly ``margin`` from the border, and clear at
    # one pixel further in.
    assert edge_at(QPoint(M, 50), RECT, M) == Qt.Edge.LeftEdge
    assert not edge_at(QPoint(M + 1, 50), RECT, M)
    assert edge_at(QPoint(199 - M, 50), RECT, M) == Qt.Edge.RightEdge
    assert not edge_at(QPoint(199 - M - 1, 50), RECT, M)
    assert edge_at(QPoint(100, M), RECT, M) == Qt.Edge.TopEdge
    assert not edge_at(QPoint(100, M + 1), RECT, M)


def test_cursor_for_edges() -> None:
    assert cursor_for_edges(Qt.Edge.LeftEdge) == Qt.CursorShape.SizeHorCursor
    assert cursor_for_edges(Qt.Edge.RightEdge) == Qt.CursorShape.SizeHorCursor
    assert cursor_for_edges(Qt.Edge.TopEdge) == Qt.CursorShape.SizeVerCursor
    assert cursor_for_edges(Qt.Edge.BottomEdge) == Qt.CursorShape.SizeVerCursor
    fdiag = Qt.CursorShape.SizeFDiagCursor
    bdiag = Qt.CursorShape.SizeBDiagCursor
    assert cursor_for_edges(Qt.Edge.TopEdge | Qt.Edge.LeftEdge) == fdiag
    assert cursor_for_edges(Qt.Edge.BottomEdge | Qt.Edge.RightEdge) == fdiag
    assert cursor_for_edges(Qt.Edge.TopEdge | Qt.Edge.RightEdge) == bdiag
    assert cursor_for_edges(Qt.Edge.BottomEdge | Qt.Edge.LeftEdge) == bdiag
    assert cursor_for_edges(Qt.Edge(0)) is None


# -- corner grip visibility ----------------------------------------------------


def test_dps_grip_visible_when_frameless(qtbot) -> None:
    window = DpsMeterWindow(_dps_backend())
    qtbot.addWidget(window)
    assert window._size_grip.isVisibleTo(window)


def test_mobinfo_grip_visible_when_frameless(qtbot) -> None:
    window = MobInfoWindow(Settings(), MobInfoState())
    qtbot.addWidget(window)
    assert window._size_grip.isVisibleTo(window)


def test_grip_hidden_when_framed(qtbot) -> None:
    settings = Settings()
    settings.windows[DPS_KEY] = WindowState(frameless=False)
    window = DpsMeterWindow(_dps_backend(settings))
    qtbot.addWidget(window)
    assert not window._size_grip.isVisibleTo(window)
    assert not window._resize_frameless()


def test_grip_visibility_follows_frameless_toggle(qtbot) -> None:
    settings = Settings()
    settings.windows[DPS_KEY] = WindowState(frameless=True)
    window = DpsMeterWindow(_dps_backend(settings))
    qtbot.addWidget(window)
    assert window._size_grip.isVisibleTo(window)
    # Flip to framed in settings, re-apply: the grip hides (OS border resizes).
    window._state.frameless = False
    window.apply_window_state()
    assert not window._size_grip.isVisibleTo(window)


# -- edge-resize wiring --------------------------------------------------------


def test_hover_sets_resize_cursor_on_edge(qtbot) -> None:
    window = DpsMeterWindow(_dps_backend())
    qtbot.addWidget(window)
    window.resize(260, 200)
    window._update_edge_cursor(QPoint(1, 100))  # left edge
    assert window.cursor().shape() == Qt.CursorShape.SizeHorCursor
    window._update_edge_cursor(QPoint(130, 100))  # interior -> restored
    assert window.cursor().shape() == Qt.CursorShape.ArrowCursor


def test_framed_window_never_sets_resize_cursor(qtbot) -> None:
    settings = Settings()
    settings.windows[DPS_KEY] = WindowState(frameless=False)
    window = DpsMeterWindow(_dps_backend(settings))
    qtbot.addWidget(window)
    window.resize(260, 200)
    window._update_edge_cursor(QPoint(1, 100))  # on the edge, but framed
    assert window.cursor().shape() == Qt.CursorShape.ArrowCursor


# -- debounced geometry persistence -------------------------------------------


def test_resize_persists_geometry_keeps_shown(qtbot) -> None:
    settings = Settings()
    settings.windows[DPS_KEY] = WindowState(shown=True)
    saves: list[int] = []
    window = DpsMeterWindow(_dps_backend(settings), on_save=lambda: saves.append(1))
    qtbot.addWidget(window)
    assert window.isVisible()
    shown_before = window._state.shown

    window.resize(360, 280)
    assert window._persist_resize.isActive()  # resize scheduled a debounced save
    window._persist_resize.timeout.emit()  # fire the debounce

    geo = settings.windows[DPS_KEY].geometry
    assert geo is not None and (geo[2], geo[3]) == (360, 280)
    assert window._state.shown == shown_before  # resize persist never flips shown
    assert saves  # on_save ran once the debounce fired


def test_construction_from_persisted_geometry_does_not_save(qtbot) -> None:
    settings = Settings()
    settings.windows[DPS_KEY] = WindowState(geometry=(120, 130, 300, 250), shown=False)
    saves: list[int] = []
    window = DpsMeterWindow(_dps_backend(settings), on_save=lambda: saves.append(1))
    qtbot.addWidget(window)
    assert saves == []  # restoring geometry in __init__ must not write settings


# -- minimum sizes -------------------------------------------------------------


def test_dps_minimum_size(qtbot) -> None:
    window = DpsMeterWindow(_dps_backend())
    qtbot.addWidget(window)
    assert (window.minimumSize().width(), window.minimumSize().height()) == (220, 140)
    window.resize(10, 10)  # clamped up to the minimum
    assert window.width() >= 220
    assert window.height() >= 140


def test_mobinfo_minimum_size(qtbot) -> None:
    window = MobInfoWindow(Settings(), MobInfoState())
    qtbot.addWidget(window)
    assert (window.minimumSize().width(), window.minimumSize().height()) == (200, 120)
    window.resize(10, 10)
    assert window.width() >= 200
    assert window.height() >= 120


# -- SpellTimerWindow integration ---------------------------------------------


def test_spellwindow_has_grip_and_edge_resize(qtbot) -> None:
    window = SpellTimerWindow(_spell_backend())
    qtbot.addWidget(window)
    assert window._grip is not None  # the original corner grip stays
    assert window._resize_frameless()  # frameless by default
    window.resize(260, 320)
    window._update_edge_cursor(QPoint(1, 150))  # left edge -> resize cursor
    assert window.cursor().shape() == Qt.CursorShape.SizeHorCursor


def test_spellwindow_edge_resize_persists_debounced(qtbot) -> None:
    settings = Settings()
    settings.windows[SPELLS_KEY] = WindowState(shown=True)
    saves: list[int] = []
    window = SpellTimerWindow(_spell_backend(settings), on_save=lambda: saves.append(1))
    qtbot.addWidget(window)
    assert window.isVisible()

    window.resize(300, 360)
    assert window._persist_resize.isActive()  # spellwindow's own debounce armed
    window._persist_resize.timeout.emit()

    geo = settings.windows[SPELLS_KEY].geometry
    assert geo is not None and (geo[2], geo[3]) == (300, 360)
    assert window._state.shown is True
    assert saves


# -- EventOverlayWindow narrowing ---------------------------------------------


def test_event_overlay_narrows_without_error(qtbot) -> None:
    overlay = EventOverlayWindow()
    qtbot.addWidget(overlay)
    overlay.resize(300, 400)  # well under a lane's fixed LANES_WIDTH (520)
    assert overlay.width() == 300
    # A CH lane still renders (it clips rather than reflowing the window).
    overlay.handle_event(
        CompleteHealEvent(timestamp=NOW, recipient="Tanky", tag="CA", position="1", caster="You")
    )
    assert "Tanky" in overlay.current_chain_lanes()
    assert overlay._lanes_host.minimumWidth() == 200
