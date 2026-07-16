"""Console window — scrollback of raw log lines (EQTool UI/Console.xaml).

A normal (non-clickthrough) tool window: read-only scrollback of LineEvents
with timestamps, a pause checkbox, capped at MAX_LINES.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QPlainTextEdit, QVBoxLayout, QWidget

from nparseplus.config.settings import Settings, WindowState
from nparseplus.core.events import LineEvent
from nparseplus.ui.overlaybase import OverlayWindowBase

WINDOW_KEY = "console"
DEFAULT_GEOMETRY = (200, 200, 560, 320)
MAX_LINES = 2000


class ConsoleWindow(OverlayWindowBase):
    def __init__(
        self,
        settings: Settings,
        on_save: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            settings=settings,
            window_key=WINDOW_KEY,
            title="Console",
            default_geometry=DEFAULT_GEOMETRY,
            on_save=on_save,
            default_state=WindowState(frameless=False, always_on_top=False),
            translucent=False,
            parent=parent,
        )
        self.setObjectName("ConsoleWindow")

        self._pause = QCheckBox("Pause", self)
        header = QHBoxLayout()
        header.addWidget(QLabel("Log console", self))
        header.addStretch(1)
        header.addWidget(self._pause)

        self._text = QPlainTextEdit(self)
        self._text.setReadOnly(True)
        self._text.setMaximumBlockCount(MAX_LINES)
        self._text.setFont(QFont("Menlo", 11))

        layout = QVBoxLayout()
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addLayout(header)
        layout.addWidget(self._text)
        self.setLayout(layout)

        self.restore_visibility()

    def handle_event(self, event: object) -> None:
        """Connect the Qt bridge's event_received signal here."""
        if isinstance(event, LineEvent) and not self._pause.isChecked():
            stamp = event.timestamp.strftime("%H:%M:%S")
            self._text.appendPlainText(f"[{stamp}] {event.line}")

    # -- test hooks ------------------------------------------------------------

    def line_count(self) -> int:
        return self._text.document().blockCount()

    def set_paused(self, paused: bool) -> None:
        self._pause.setChecked(paused)

    # dragging the window body would fight with text selection; only the
    # base-class drag on the margins applies. Keep default mouse handling.
    def mousePressEvent(self, event) -> None:
        QWidget.mousePressEvent(self, event)

    def mouseMoveEvent(self, event) -> None:
        QWidget.mouseMoveEvent(self, event)

    def mouseReleaseEvent(self, event) -> None:
        QWidget.mouseReleaseEvent(self, event)
