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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from PySide6.QtCore import QPoint, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QLabel,
    QProgressBar,
    QSizeGrip,
    QVBoxLayout,
    QWidget,
)

from nparseplus.config.settings import WindowState
from nparseplus.core.events import CompleteHealEvent, OverlayEvent, TimerBarEvent

DEFAULT_CLEAR_AFTER_S = 4.0
BAR_TICK_MS = 200
BAR_WIDTH = 320
DEFAULT_TEXT_COLOR = "red"
DEFAULT_BAR_COLOR = "steelblue"

# CH chain lane (EQTool EventOverlay.xaml.cs): each CH call is a green chip
# labeled with the caster's position, sliding across the lane over the CH
# cast time. A lane never disappears while chips are in flight, and persists
# ``ch_lane_retention_s`` (default 20s) past the last CH call for its target,
# so healers keep a stable anchor for who is being chain-healed.
CH_CHIP_SECONDS = 11.0
CH_LANE_HEIGHT = 30
DEFAULT_CH_LANE_RETENTION_S = 20.0


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


class _ChainLane(QFrame):
    """One heal target's CH lane: chips slide right-to-left across it."""

    def __init__(self, target: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.target = target
        self.chips: list[QLabel] = []
        self.last_call: datetime = datetime.now()
        # Called (with no args) whenever a chip finishes its slide.
        self.on_chip_done: Callable[[], None] | None = None
        self.setObjectName("ChChainLane")
        self.setFixedHeight(CH_LANE_HEIGHT)
        self.setStyleSheet(
            "#ChChainLane { background-color: rgba(0, 0, 0, 130);"
            " border: 1px solid rgba(255, 255, 255, 60); border-radius: 3px; }"
        )
        self._target_label = QLabel(target, self)
        self._target_label.setStyleSheet("color: #cccccc; font-size: 11px; font-weight: bold;")
        self._target_label.move(4, 6)
        self._target_label.show()

    def add_chip(self, position: str) -> QLabel:
        chip = QLabel(position, self)
        chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chip.setFixedSize(56, CH_LANE_HEIGHT - 6)
        chip.setStyleSheet(
            "background-color: forestgreen; color: white; font-weight: bold;"
            " border: 1px solid black; border-radius: 3px;"
        )
        chip.move(self.width(), 3)  # enter from the right edge
        chip.show()
        self.chips.append(chip)

        animation = QPropertyAnimation(chip, b"pos", chip)
        animation.setDuration(int(CH_CHIP_SECONDS * 1000))
        animation.setStartValue(QPoint(self.width(), 3))
        animation.setEndValue(QPoint(-chip.width(), 3))
        animation.finished.connect(lambda: self._chip_done(chip))
        animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        return chip

    def _chip_done(self, chip: QLabel) -> None:
        if chip in self.chips:
            self.chips.remove(chip)
        chip.deleteLater()
        if self.on_chip_done is not None:
            self.on_chip_done()


class EventOverlayWindow(QWidget):
    """Clickthrough full-screen overlay driven by bridge events."""

    def __init__(
        self,
        clear_after_s: float = DEFAULT_CLEAR_AFTER_S,
        ch_lane_retention_s: float = DEFAULT_CH_LANE_RETENTION_S,
        state: WindowState | None = None,
        on_save: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._clear_after_ms = max(1000, int(clear_after_s * 1000))
        self._ch_lane_retention_s = max(0.0, ch_lane_retention_s)
        self._state = state
        self._on_save = on_save
        self._edit_mode = False
        self._drag_offset: QPoint | None = None
        self.setObjectName("EventOverlayWindow")
        self.setWindowTitle("Event Overlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        # macOS: Qt.Tool windows normally hide when the app deactivates —
        # this attribute keeps the overlay up while the game has focus.
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        self._apply_locked_flags()

        # Region: persisted geometry if the user positioned it (e.g. centered
        # over the P99 window), otherwise the primary screen.
        geometry = state.geometry if state is not None else None
        if geometry:
            self.setGeometry(*geometry)
        else:
            screen = QApplication.primaryScreen()
            if screen is not None:
                self.setGeometry(screen.geometry())

        self._text_color = ""
        self._bars: dict[str, _TimerBar] = {}
        self._chain_lanes: dict[str, _ChainLane] = {}

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

        self._lanes_layout = QVBoxLayout()
        self._lanes_layout.setContentsMargins(0, 0, 0, 0)
        self._lanes_layout.setSpacing(3)
        lanes_host = QWidget(self)
        lanes_host.setMinimumWidth(520)
        lanes_host.setLayout(self._lanes_layout)

        layout = QVBoxLayout()
        layout.setContentsMargins(20, 40, 20, 60)
        layout.addWidget(lanes_host, 0, Qt.AlignmentFlag.AlignHCenter)
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

        # Position-mode chrome (hidden unless editing).
        self._edit_hint = QLabel(
            "Event overlay — drag to move, use the corner grip to resize,\n"
            "double-click to lock in place",
            self,
        )
        self._edit_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._edit_hint.setStyleSheet(
            "color: white; font-size: 16px; font-weight: bold;"
            " background-color: rgba(30, 60, 120, 120);"
        )
        self._edit_hint.hide()
        self._size_grip = QSizeGrip(self)
        self._size_grip.setFixedSize(24, 24)
        self._size_grip.hide()

        self.hide()

    # -- position mode -----------------------------------------------------------

    def _apply_locked_flags(self) -> None:
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
        )

    def set_edit_mode(self, editing: bool) -> None:
        """Position mode: the overlay becomes clickable/draggable/resizable so
        the user can center it over the game window, then locks again."""
        if editing == self._edit_mode:
            return
        self._edit_mode = editing
        if editing:
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool
            )
            self._edit_hint.setGeometry(0, 0, self.width(), self.height())
            self._edit_hint.show()
            self._size_grip.move(self.width() - 26, self.height() - 26)
            self._size_grip.show()
            self.show()
            self.raise_()
        else:
            self._edit_hint.hide()
            self._size_grip.hide()
            self._apply_locked_flags()
            if self._state is not None:
                geo = self.geometry()
                self._state.geometry = (geo.x(), geo.y(), geo.width(), geo.height())
                if self._on_save is not None:
                    self._on_save()
            self._update_visibility()

    def is_edit_mode(self) -> bool:
        return self._edit_mode

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._edit_mode:
            self._edit_hint.setGeometry(0, 0, self.width(), self.height())
            self._size_grip.move(self.width() - 26, self.height() - 26)

    def mousePressEvent(self, event) -> None:
        if self._edit_mode and event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._edit_mode and self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if self._edit_mode:
            self.set_edit_mode(False)
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    # -- event intake (connect the bridge's event_received signal here) ------------

    def handle_event(self, event: object) -> None:
        if isinstance(event, OverlayEvent):
            self._on_overlay_event(event)
        elif isinstance(event, TimerBarEvent):
            self._on_timer_bar_event(event)
        elif isinstance(event, CompleteHealEvent):
            self._on_complete_heal(event)

    def _on_complete_heal(self, event: CompleteHealEvent) -> None:
        target = event.recipient or "?"
        lane = self._chain_lanes.get(target)
        if lane is None:
            lane = _ChainLane(target, self)
            lane.setFixedWidth(520)
            lane.on_chip_done = lambda t=target: QTimer.singleShot(
                100, lambda: self._maybe_remove_lane(t)
            )
            self._chain_lanes[target] = lane
            self._lanes_layout.addWidget(lane)
            lane.show()
        lane.last_call = datetime.now()
        lane.add_chip(event.position or "?")
        # Re-check just past the retention window of THIS call; earlier
        # timers fire harmlessly (retention not yet elapsed).
        QTimer.singleShot(
            int(self._ch_lane_retention_s * 1000) + 250,
            lambda: self._maybe_remove_lane(target),
        )
        self._update_visibility()

    def _maybe_remove_lane(self, target: str) -> None:
        """Remove a lane only when it has no chips in flight AND the retention
        window since its last CH call has fully elapsed."""
        lane = self._chain_lanes.get(target)
        if lane is None:
            return
        idle_s = (datetime.now() - lane.last_call).total_seconds()
        if not lane.chips and idle_s >= self._ch_lane_retention_s:
            self._lanes_layout.removeWidget(lane)
            lane.deleteLater()
            del self._chain_lanes[target]
        self._update_visibility()

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
        if self._edit_mode:
            if not self.isVisible():
                self.show()
            return
        active = bool(self._center_text.text()) or bool(self._bars) or bool(self._chain_lanes)
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

    def current_chain_lanes(self) -> dict[str, list[str]]:
        """Test hook: {target: [chip position texts]} for the CH lanes."""
        return {
            target: [chip.text() for chip in lane.chips]
            for target, lane in self._chain_lanes.items()
        }
