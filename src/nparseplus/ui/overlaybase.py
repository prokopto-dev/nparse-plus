"""Shared plumbing for the M2 overlay windows.

Factors the window-state recipe pioneered by ``ui.spellwindow.SpellTimerWindow``
(frameless/always-on-top flags, geometry + opacity persisted into
``Settings.windows[key]``, drag-to-move, save-on-quit) into a base class so
the DPS meter, mob info, and console windows don't re-implement it.
``spellwindow.py`` predates this module and intentionally stays untouched.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QCoreApplication, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QSizeGrip, QWidget

from nparseplus.config.settings import Settings, WindowState
from nparseplus.ui import appquit

# Width of the invisible band along each frameless-window edge where a
# mouse-press starts an edge/corner resize (the QSizeGrip is an additional
# corner affordance, but users expect to grab any edge).
RESIZE_MARGIN = 7


def format_mmss(seconds: float) -> str:
    """mm:ss (or h:mm:ss past the hour), clamped at zero."""
    total = max(0, int(seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def edge_at(pos: QPoint, rect: QRect, margin: int = RESIZE_MARGIN) -> Qt.Edge:
    """Which window edges a point hits within ``margin`` of ``rect``'s border.

    Returns a combined ``Qt.Edge`` flag (e.g. ``TopEdge | LeftEdge`` for the
    top-left corner) or ``Qt.Edge(0)`` (falsy) for the interior. Pure so the
    hit-testing can be unit-tested without a live window.
    """
    edges = Qt.Edge(0)
    x, y = pos.x(), pos.y()
    if x <= rect.left() + margin:
        edges |= Qt.Edge.LeftEdge
    elif x >= rect.right() - margin:
        edges |= Qt.Edge.RightEdge
    if y <= rect.top() + margin:
        edges |= Qt.Edge.TopEdge
    elif y >= rect.bottom() - margin:
        edges |= Qt.Edge.BottomEdge
    return edges


def cursor_for_edges(edges: Qt.Edge) -> Qt.CursorShape | None:
    """The resize cursor for an ``edge_at`` result, or ``None`` for the interior."""
    left = bool(edges & Qt.Edge.LeftEdge)
    right = bool(edges & Qt.Edge.RightEdge)
    top = bool(edges & Qt.Edge.TopEdge)
    bottom = bool(edges & Qt.Edge.BottomEdge)
    if (top and left) or (bottom and right):
        return Qt.CursorShape.SizeFDiagCursor
    if (top and right) or (bottom and left):
        return Qt.CursorShape.SizeBDiagCursor
    if left or right:
        return Qt.CursorShape.SizeHorCursor
    if top or bottom:
        return Qt.CursorShape.SizeVerCursor
    return None


class EdgeResizeMixin:
    """Frameless edge/corner drag-resize for a ``QWidget``.

    Frameless windows have no OS resize border, so we hand-roll one: a margin
    band around every edge where a left-press starts a native
    ``startSystemResize`` (Qt >= 5.15, works on macOS/Win/X11) and a hover sets
    the matching resize cursor. Hosts must enable mouse tracking (so hover
    ``mouseMoveEvent``s arrive) and override ``_resize_frameless`` to gate it —
    framed windows keep their OS border and get no edge band.
    """

    _resize_margin = RESIZE_MARGIN

    def _resize_frameless(self) -> bool:
        return True

    def _maybe_begin_edge_resize(self, pos: QPoint) -> bool:
        """Start a system resize if ``pos`` is on a frameless edge; else False."""
        if not self._resize_frameless():
            return False
        edges = edge_at(pos, self.rect(), self._resize_margin)
        if not edges:
            return False
        handle = self.windowHandle()
        if handle is None:
            return False
        handle.startSystemResize(edges)
        return True

    def _update_edge_cursor(self, pos: QPoint) -> None:
        """Set the resize cursor while hovering a frameless edge (else restore)."""
        cursor = cursor_for_edges(edge_at(pos, self.rect(), self._resize_margin))
        if not self._resize_frameless() or cursor is None:
            self.unsetCursor()
        else:
            self.setCursor(cursor)


class OverlayWindowBase(EdgeResizeMixin, QWidget):
    """A window whose geometry/flags/visibility persist in ``Settings.windows``.

    Subclasses build their content in ``__init__`` and then call
    ``restore_visibility()`` to honor the persisted ``shown`` flag.
    """

    def __init__(
        self,
        settings: Settings,
        window_key: str,
        title: str,
        default_geometry: tuple[int, int, int, int],
        on_save: Callable[[], None] | None = None,
        default_state: WindowState | None = None,
        translucent: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._window_key = window_key
        self._on_save = on_save
        self._drag_offset: QPoint | None = None
        self._quitting = False
        # True while __init__ applies the restored geometry, so that initial
        # setGeometry (and any content-build resizes) never schedule a save.
        self._restoring = True

        state = settings.windows.get(window_key)
        if state is None:
            state = default_state if default_state is not None else WindowState()
            settings.windows[window_key] = state
        self._state = state

        self.setWindowTitle(title)
        if translucent:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # Corner grip + hover edge-resize: track the mouse so hover moves reach
        # mouseMoveEvent for the resize cursor.
        self.setMouseTracking(True)
        self._size_grip = QSizeGrip(self)
        self._size_grip.setStyleSheet("QSizeGrip { background: transparent; }")
        self._size_grip.raise_()
        # Debounced geometry persist: fires once a grip/edge/drag resize settles.
        self._persist_resize = QTimer(self)
        self._persist_resize.setSingleShot(True)
        self._persist_resize.setInterval(400)
        self._persist_resize.timeout.connect(self.persist_state)
        self._apply_flags()
        self.setGeometry(*(state.geometry or default_geometry))
        self.setWindowOpacity(state.opacity)
        self._update_grip_visibility()
        self._restoring = False

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._on_app_quit)

    def _resize_frameless(self) -> bool:
        return self._state.frameless

    def _update_grip_visibility(self) -> None:
        """Show the corner grip only when frameless (framed windows resize via
        the OS border)."""
        self._size_grip.setVisible(self._state.frameless)

    def restore_visibility(self) -> None:
        """Show the window if it was shown last session (call after building UI)."""
        if self._state.shown:
            self.show()

    # -- window state ------------------------------------------------------------

    def _apply_flags(self) -> None:
        state = self._state
        if state.frameless:
            flags = Qt.WindowType.FramelessWindowHint
        else:
            flags = Qt.WindowType.WindowCloseButtonHint | Qt.WindowType.WindowMinMaxButtonsHint
        if state.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        if state.clickthrough:
            flags |= Qt.WindowType.WindowTransparentForInput
        self.setWindowFlags(flags)

    def apply_window_state(self) -> None:
        """Re-apply opacity/flags from the (possibly just-edited) state.

        setWindowFlags hides the window, so re-show when it was visible —
        same recipe as the legacy ParserWindow settings watcher.
        """
        self.setWindowOpacity(self._state.opacity)
        was_visible = self.isVisible()
        self._apply_flags()
        self._update_grip_visibility()
        if was_visible:
            self.show()

    def toggle(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.show()
        self.persist_state()

    def persist_state(self, shown: bool | None = None) -> None:
        """Write geometry/opacity/shown into ``settings.windows[key]`` and save."""
        geo = self.geometry()
        self._state.geometry = (geo.x(), geo.y(), geo.width(), geo.height())
        self._state.opacity = min(1.0, max(0.0, round(self.windowOpacity(), 3)))
        self._state.shown = self.isVisible() if shown is None else shown
        if self._on_save is not None:
            self._on_save()

    def _app_quitting(self) -> bool:
        """True on any quit path — aboutToQuit, tray Quit, or macOS Cmd+Q
        (which closes windows via closeAllWindows before aboutToQuit fires)."""
        return self._quitting or appquit.is_quitting() or QCoreApplication.closingDown()

    def _on_app_quit(self) -> None:
        self._quitting = True
        # App quit must never flip ``shown`` downward: it already reflects the
        # last deliberate visibility choice (toggle / user close). On Cmd+Q the
        # windows were closed by closeAllWindows() before this fires, so
        # isVisible() would clobber — persist geometry/opacity, keep ``shown``.
        self.persist_state(shown=self._state.shown)

    def closeEvent(self, event) -> None:
        if not self._app_quitting():
            self.persist_state(shown=False)
        super().closeEvent(event)

    # -- resize (grip + edges) -----------------------------------------------------

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        rect = self.rect()
        self._size_grip.move(
            rect.right() - self._size_grip.width(),
            rect.bottom() - self._size_grip.height(),
        )
        # Persist real (visible) resizes only; the __init__ restore path and any
        # not-yet-shown content-build resizes must not schedule a save.
        if not self._restoring and self.isVisible():
            self._persist_resize.start()

    # -- drag-to-move / edge-resize ------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._maybe_begin_edge_resize(event.position().toPoint()):
                event.accept()
                return
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
        else:
            if not event.buttons():
                self._update_edge_cursor(event.position().toPoint())
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None:
            self._drag_offset = None
            self.persist_state()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:
        event.accept()  # deliberately inert: no scroll-through to the game
