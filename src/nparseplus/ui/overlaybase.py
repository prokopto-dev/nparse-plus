"""Shared plumbing for the M2 overlay windows.

Factors the window-state recipe pioneered by ``ui.spellwindow.SpellTimerWindow``
(frameless/always-on-top flags, geometry + opacity persisted into
``Settings.windows[key]``, drag-to-move, save-on-quit) into a base class so
the DPS meter, mob info, and console windows don't re-implement it.
``spellwindow.py`` predates this module and intentionally stays untouched.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QCoreApplication, QPoint, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QWidget

from nparseplus.config.settings import Settings, WindowState
from nparseplus.ui import appquit


def format_mmss(seconds: float) -> str:
    """mm:ss (or h:mm:ss past the hour), clamped at zero."""
    total = max(0, int(seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class OverlayWindowBase(QWidget):
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

        state = settings.windows.get(window_key)
        if state is None:
            state = default_state if default_state is not None else WindowState()
            settings.windows[window_key] = state
        self._state = state

        self.setWindowTitle(title)
        if translucent:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._apply_flags()
        self.setGeometry(*(state.geometry or default_geometry))
        self.setWindowOpacity(state.opacity)

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._on_app_quit)

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

    # -- drag-to-move --------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
        else:
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
