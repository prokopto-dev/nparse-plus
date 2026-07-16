"""Full-screen transparent overlay for OverlayEvent text and timer bars.

Port of EQTool's UI/EventOverlay.xaml(.cs) essentials:
- ``OverlayEvent``: big centered outlined text (color token from the event),
  cleared on a matching ``reset=True`` event or after ``CLEAR_AFTER_MS``.
- ``TimerBarEvent``: countdown bars stacked bottom-center, one per name
  (re-raising a name restarts its bar), removed when they reach zero.

The window is always frameless, always on top, and transparent for input
(never intercepts clicks); it hides itself whenever there is nothing to
show. Unlike the other overlays it has no tray toggle and persists nothing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsDropShadowEffect,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from nparseplus.core.events import OverlayEvent, TimerBarEvent

DEFAULT_CLEAR_AFTER_S = 4.0
BAR_TICK_MS = 200
BAR_WIDTH = 320
DEFAULT_TEXT_COLOR = "red"
DEFAULT_BAR_COLOR = "steelblue"


def resolve_color(token: str | None, fallback: str) -> str:
    """Resolve a core color token ('Red', 'Yellow', '#22aa44'…) to a hex color."""
    color = QColor((token or "").strip().lower() or fallback)
    if not color.isValid():
        color = QColor(fallback)
    return color.name()


@dataclass
class _TimerBar:
    name: str
    ends_at: datetime
    total_seconds: int
    widget: QProgressBar


class EventOverlayWindow(QWidget):
    """Clickthrough full-screen overlay driven by bridge events."""

    def __init__(
        self, clear_after_s: float = DEFAULT_CLEAR_AFTER_S, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._clear_after_ms = max(1000, int(clear_after_s * 1000))
        self.setObjectName("EventOverlayWindow")
        self.setWindowTitle("Event Overlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
        )

        screen = QApplication.primaryScreen()
        if screen is not None:
            self.setGeometry(screen.geometry())

        self._text_color = ""
        self._bars: dict[str, _TimerBar] = {}

        self._center_text = QLabel("", self)
        self._center_text.setObjectName("EventOverlayText")
        self._center_text.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._center_text.setWordWrap(True)
        outline = QGraphicsDropShadowEffect(self._center_text)
        outline.setOffset(0, 0)
        outline.setBlurRadius(8)
        outline.setColor(QColor("black"))
        self._center_text.setGraphicsEffect(outline)
        self._set_text_color(DEFAULT_TEXT_COLOR)

        self._bars_layout = QVBoxLayout()
        self._bars_layout.setContentsMargins(0, 0, 0, 0)
        self._bars_layout.setSpacing(2)

        bars_host = QWidget(self)
        bars_host.setFixedWidth(BAR_WIDTH)
        bars_host.setLayout(self._bars_layout)

        layout = QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 60)
        layout.addStretch(2)
        layout.addWidget(self._center_text, 0)
        layout.addStretch(3)
        layout.addWidget(bars_host, 0, Qt.AlignmentFlag.AlignHCenter)
        self.setLayout(layout)

        self._clear_timer = QTimer(self)
        self._clear_timer.setSingleShot(True)
        self._clear_timer.setInterval(self._clear_after_ms)
        self._clear_timer.timeout.connect(self.clear_text)

        self._bar_timer = QTimer(self)
        self._bar_timer.setInterval(BAR_TICK_MS)
        self._bar_timer.timeout.connect(self._tick_bars)

        self.hide()

    # -- event intake (connect the bridge's event_received signal here) ------------

    def handle_event(self, event: object) -> None:
        if isinstance(event, OverlayEvent):
            self._on_overlay_event(event)
        elif isinstance(event, TimerBarEvent):
            self._on_timer_bar_event(event)

    def _on_overlay_event(self, event: OverlayEvent) -> None:
        if event.reset:
            # EQTool only clears when the reset matches what is displayed.
            if self._center_text.text() == event.text:
                self.clear_text()
            return
        self._center_text.setText(event.text)
        self._set_text_color(resolve_color(event.foreground, DEFAULT_TEXT_COLOR))
        self._clear_timer.start()
        self._update_visibility()

    def _on_timer_bar_event(self, event: TimerBarEvent) -> None:
        existing = self._bars.pop(event.name, None)
        if existing is not None:  # re-raise restarts the bar
            self._bars_layout.removeWidget(existing.widget)
            existing.widget.deleteLater()
        total = max(1, int(event.total_seconds))
        bar = QProgressBar(self)
        bar.setObjectName("EventOverlayBar")
        bar.setRange(0, total)
        bar.setValue(total)
        bar.setFixedHeight(22)
        bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        color = resolve_color(event.bar_color, DEFAULT_BAR_COLOR)
        bar.setStyleSheet(
            "QProgressBar { background-color: rgba(10, 10, 10, 200);"
            " border: 1px solid #ffffff; color: #ffffff; font-weight: bold; }"
            f"QProgressBar::chunk {{ background-color: {color}; }}"
        )
        entry = _TimerBar(
            name=event.name,
            ends_at=datetime.now() + timedelta(seconds=total),
            total_seconds=total,
            widget=bar,
        )
        self._bars[event.name] = entry
        self._bars_layout.addWidget(bar)
        self._render_bar(entry, datetime.now())
        if not self._bar_timer.isActive():
            self._bar_timer.start()
        self._update_visibility()

    # -- rendering -------------------------------------------------------------

    def _set_text_color(self, color: str) -> None:
        if color != self._text_color:
            self._text_color = color
            self._center_text.setStyleSheet(f"color: {color}; font-size: 32px; font-weight: bold;")

    def clear_text(self) -> None:
        self._clear_timer.stop()
        self._center_text.setText("")
        self._update_visibility()

    def _render_bar(self, entry: _TimerBar, now: datetime) -> None:
        remaining = (entry.ends_at - now).total_seconds()
        entry.widget.setValue(max(0, min(entry.total_seconds, math.ceil(remaining))))
        entry.widget.setFormat(f"{entry.name}  {max(0, math.ceil(remaining))}s")

    def _tick_bars(self) -> None:
        now = datetime.now()
        for name, entry in list(self._bars.items()):
            if entry.ends_at <= now:
                self._bars_layout.removeWidget(entry.widget)
                entry.widget.deleteLater()
                del self._bars[name]
            else:
                self._render_bar(entry, now)
        if not self._bars:
            self._bar_timer.stop()
        self._update_visibility()

    def _update_visibility(self) -> None:
        active = bool(self._center_text.text()) or bool(self._bars)
        if active and not self.isVisible():
            self.show()
        elif not active and self.isVisible():
            self.hide()

    # -- test/debug hooks --------------------------------------------------------

    def current_text(self) -> str:
        return self._center_text.text()

    def current_text_color(self) -> str:
        return self._text_color

    def current_bar_names(self) -> list[str]:
        out: list[str] = []
        for i in range(self._bars_layout.count()):
            widget = self._bars_layout.itemAt(i).widget()
            if isinstance(widget, QProgressBar):
                for name, entry in self._bars.items():
                    if entry.widget is widget:
                        out.append(name)
                        break
        return out

    def is_active(self) -> bool:
        return self.isVisible()
