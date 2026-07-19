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

from PySide6.QtCore import QPoint, QPropertyAnimation, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizeGrip,
    QStyle,
    QStyleOption,
    QVBoxLayout,
    QWidget,
)

from nparseplus.config.settings import OverlayRegion, WindowState
from nparseplus.core.events import (
    CompleteHealCadenceEvent,
    CompleteHealEvent,
    OverlayEvent,
    TimerBarEvent,
)

DEFAULT_CLEAR_AFTER_S = 4.0
BAR_TICK_MS = 200
BAR_WIDTH = 320
LANES_WIDTH = 520
DEFAULT_TEXT_COLOR = "red"
DEFAULT_BAR_COLOR = "steelblue"

# Positioning-mode chrome.
EDIT_HINT_HEIGHT = 56
# The stacked layout's outer margins (mirrors the main QVBoxLayout margins);
# region-mode anchors measure from these lines.
REGION_MARGIN_TOP = 40
REGION_MARGIN_BOTTOM = 60

# CH chain lane (EQTool EventOverlay.xaml.cs): each CH call is a green chip
# labeled with the caster's position, sliding across the lane over the CH
# cast time. A lane never disappears while chips are in flight, and persists
# ``ch_lane_retention_s`` (default 20s) past the last CH call for its target,
# so healers keep a stable anchor for who is being chain-healed.
CH_CHIP_SECONDS = 11.0
CH_LANE_HEIGHT = 30
# The target name sits in its own fixed column beside the graduated lane
# (EQTool keeps the name in a separate grid column) so it never obscures the
# "1" second-marker cell; long names elide with the full name as a tooltip.
CH_LANE_NAME_WIDTH = 110
# The name is right-aligned within that column and sits this many pixels from
# the lane, so a short target name hugs the lane instead of floating at the
# far-left of the fixed-width column.
CH_LANE_NAME_GAP = 8
# The lane is graduated into 10 one-second cells (EQTool GetOrCreateChain: a
# 10-cell strip, each ``ActualWidth / 10`` wide, numbered 1..10 in red). A chip
# is exactly one cell wide and slides ``width + chip.width`` (= 11 cells) over
# ``CH_CHIP_SECONDS`` (11 s), so each cell is exactly 1 s of travel and the
# 10-cell bar spans ~one 10 s Complete Heal cast.
CH_LANE_CELLS = 10
DEFAULT_CH_LANE_RETENTION_S = 20.0
# Safety-net sweep: the one-shot removal timers give prompt cleanup in the
# normal case, but if a chip's ``finished`` signal never fires (animation torn
# down mid-flight) its lane's chip list never empties and the normal removal
# gate stays false forever. This periodic sweep force-removes any lane idle
# past ``max(retention, chip flight) + grace`` regardless of chip bookkeeping.
CH_LANE_SWEEP_MS = 1000
CH_LANE_FORCE_GRACE_S = 1.0


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
        # Declared CH cadence in seconds ("healers to 4"), or None (#15). When
        # set, a muted marker highlights that second-cell as the next-cast tick.
        self.cadence_seconds: int | None = None
        # Called (with no args) whenever a chip finishes its slide.
        self.on_chip_done: Callable[[], None] | None = None
        # The [name | lane] row container this lane sits in (set by the
        # overlay when it builds the row; None for a bare lane in tests).
        self.row: QWidget | None = None
        self.setObjectName("ChChainLane")
        self.setFixedHeight(CH_LANE_HEIGHT)
        self.setStyleSheet(
            "#ChChainLane { background-color: rgba(0, 0, 0, 130);"
            " border: 1px solid rgba(255, 255, 255, 60); border-radius: 3px; }"
        )

    def cell_width(self) -> int:
        """Width of one second-marker cell (``width / 10``, EQTool parity)."""
        return max(1, self.width() // CH_LANE_CELLS)

    def cell_geometry(self) -> list[QRect]:
        """The 10 second-marker cell rects, left to right. Test/paint hook so the
        cell layout is derived from the *current* width, never a hardcoded 520."""
        cw = self.cell_width()
        return [QRect(i * cw, 0, cw, self.height()) for i in range(CH_LANE_CELLS)]

    def paintEvent(self, event) -> None:
        # Divergence from EQTool: EQTool builds the 10-cell strip as a StackPanel
        # of Border children sitting behind a transparent animation Canvas. Here
        # the strip is static (fixed geometry) so we paint it directly on the
        # lane's own surface — cheaper than 20 child widgets, and it renders
        # behind the chip/target QLabels automatically (child widgets paint on
        # top of the parent's paintEvent).
        opt = QStyleOption()
        opt.initFrom(self)
        painter = QPainter(self)
        # Let the stylesheet background/border/radius render first.
        self.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget, opt, painter, self)
        painter.setFont(self.font())
        border = QColor("whitesmoke")
        red = QColor("red")
        height = self.height()
        for i, rect in enumerate(self.cell_geometry()):
            x, cw = rect.x(), rect.width()
            # Muted "next expected cast" marker on the declared-cadence cell (#15).
            if self.cadence_seconds is not None and i + 1 == self.cadence_seconds:
                painter.fillRect(rect.adjusted(1, 1, -1, -1), QColor(255, 215, 0, 60))
            painter.setPen(QPen(border, 1))  # 1px left/right verticals
            painter.drawLine(x, 0, x, height)
            painter.drawLine(x + cw - 1, 0, x + cw - 1, height)
            painter.setPen(QPen(border, 2))  # 2px bottom accent
            painter.drawLine(x, height - 1, x + cw, height - 1)
            painter.setPen(red)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(i + 1))
        painter.end()

    def _make_chip(self, text: str) -> QLabel:
        """Build a green CH chip (styling shared by live and static chips)."""
        chip = QLabel(text, self)
        chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Pin the chip to exactly one second-marker cell (EQTool: chip width =
        # ActualWidth / 10) so each cell stays exactly 1 s of chip travel.
        chip.setFixedSize(self.cell_width(), CH_LANE_HEIGHT - 6)
        chip.setStyleSheet(
            "background-color: forestgreen; color: white; font-weight: bold;"
            " border: 1px solid black; border-radius: 3px;"
        )
        return chip

    def add_chip(self, position: str) -> QLabel:
        chip = self._make_chip(position)
        chip.move(self.width(), 3)  # enter from the right edge
        chip.raise_()  # chips slide on top of the painted cell strip
        chip.show()
        self.chips.append(chip)

        animation = QPropertyAnimation(chip, b"pos", chip)
        animation.setDuration(int(CH_CHIP_SECONDS * 1000))
        animation.setStartValue(QPoint(self.width(), 3))
        animation.setEndValue(QPoint(-chip.width(), 3))
        animation.finished.connect(lambda: self._chip_done(chip))
        animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        return chip

    def add_static_chip(self, text: str, cell_index: int) -> QLabel:
        """A non-animated chip pinned onto a fixed cell (positioning-mode
        preview only): NOT appended to ``self.chips`` so it never participates
        in live chip bookkeeping or the lane-removal gate."""
        chip = self._make_chip(text)
        cells = self.cell_geometry()
        idx = max(0, min(cell_index, len(cells) - 1))
        chip.move(cells[idx].x(), 3)
        chip.raise_()
        chip.show()
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
        # Last declared CH cadence (#15); new lanes inherit it, existing lanes
        # are updated when a fresh callout arrives.
        self._ch_cadence_seconds: int | None = None
        # Positioning-mode sample widgets: tracked ONLY here, never registered
        # in ``_bars``/``_chain_lanes`` and never written to ``_center_text``.
        self._preview_widgets: list[QWidget] = []

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

        self._bars_host = QWidget(self)
        self._bars_host.setObjectName("OverlayBarsHost")
        self._bars_host.setFixedWidth(BAR_WIDTH)
        self._bars_host.setLayout(self._bars_layout)

        self._lanes_layout = QVBoxLayout()
        self._lanes_layout.setContentsMargins(0, 0, 0, 0)
        self._lanes_layout.setSpacing(3)
        self._lanes_host = QWidget(self)
        self._lanes_host.setObjectName("OverlayLanesHost")
        # Lanes keep their fixed LANES_WIDTH and simply clip when the window is
        # narrower than a lane; a low host minimum lets the overlay be narrowed.
        self._lanes_host.setMinimumWidth(200)
        self._lanes_host.setLayout(self._lanes_layout)

        # ``_center_text`` (and the preview alert label) live in their own host
        # so the alert region can be positioned independently in region mode.
        self._alert_layout = QVBoxLayout()
        self._alert_layout.setContentsMargins(0, 0, 0, 0)
        self._alert_layout.setSpacing(4)
        self._alert_layout.addWidget(self._center_text)
        self._alert_host = QWidget(self)
        self._alert_host.setObjectName("OverlayAlertHost")
        self._alert_host.setLayout(self._alert_layout)

        # Dedicated utility header section (#14): a "Utility" header + a stack of
        # auto-clearing lines for rebuff/OOM-style alerts routed here by triggers
        # whose output targets section="utility". Header hides when empty.
        self._utility_layout = QVBoxLayout()
        self._utility_layout.setContentsMargins(0, 0, 0, 0)
        self._utility_layout.setSpacing(2)
        self._utility_header = QLabel("Utility", self)
        self._utility_header.setObjectName("OverlayUtilityHeader")
        self._utility_header.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._utility_header.setStyleSheet(
            "color: #dddddd; background-color: rgba(30, 60, 120, 200);"
            " font-size: 12px; font-weight: bold; padding: 1px 6px; border-radius: 3px;"
        )
        self._utility_header.hide()
        self._utility_layout.addWidget(self._utility_header, 0, Qt.AlignmentFlag.AlignHCenter)
        self._utility_host = QWidget(self)
        self._utility_host.setObjectName("OverlayUtilityHost")
        self._utility_host.setLayout(self._utility_layout)
        self._utility_lines: dict[str, QLabel] = {}
        self._utility_timers: dict[str, QTimer] = {}

        self._main_layout = QVBoxLayout()
        self._main_layout.setContentsMargins(20, 40, 20, 60)
        self._main_layout.addWidget(self._lanes_host, 0, Qt.AlignmentFlag.AlignHCenter)
        self._main_layout.addWidget(self._utility_host, 0, Qt.AlignmentFlag.AlignHCenter)
        self._main_layout.addStretch(2)
        self._main_layout.addWidget(self._alert_host, 0)
        self._main_layout.addStretch(3)
        self._main_layout.addWidget(self._bars_host, 0, Qt.AlignmentFlag.AlignHCenter)
        self.setLayout(self._main_layout)

        # Small dashed-border title chips shown over each region while editing.
        self._region_titles: dict[str, QLabel] = {}
        for key, text in (
            ("lanes", "CH chains"),
            ("utility", "Utility"),
            ("alert", "Alerts"),
            ("bars", "Timer bars"),
        ):
            chip = QLabel(text, self)
            chip.setStyleSheet(
                "color: white; background-color: rgba(30, 60, 120, 220);"
                " font-size: 11px; font-weight: bold; padding: 1px 4px;"
            )
            chip.hide()
            self._region_titles[key] = chip

        self._clear_timer = QTimer(self)
        self._clear_timer.setSingleShot(True)
        self._clear_timer.setInterval(self._clear_after_ms)
        self._clear_timer.timeout.connect(self.clear_text)

        self._bar_timer = QTimer(self)
        self._bar_timer.setInterval(BAR_TICK_MS)
        self._bar_timer.timeout.connect(self._tick_bars)

        # Safety net for CH lanes: runs only while lanes exist (see sweep).
        self._sweep_timer = QTimer(self)
        self._sweep_timer.setInterval(CH_LANE_SWEEP_MS)
        self._sweep_timer.timeout.connect(self._sweep_lanes)

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

        # Region-drag bookkeeping (populated while dragging a single region).
        self._drag_region: str | None = None
        self._region_drag_start: QPoint | None = None
        self._region_drag_base = (0, 0)

        # If regions were persisted, switch out of the stacked QVBoxLayout now.
        if self._region_mode():
            self._activate_region_layout()

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
            # A top strip only, so the sample content beneath stays visible.
            self._edit_hint.setGeometry(0, 0, self.width(), EDIT_HINT_HEIGHT)
            self._edit_hint.show()
            self._edit_hint.raise_()
            self._size_grip.move(self.width() - 26, self.height() - 26)
            self._size_grip.show()
            self._show_preview()
            self._set_region_chrome(True)
            self.show()
            self.raise_()
        else:
            self._clear_preview()
            self._set_region_chrome(False)
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
        if self._region_mode():
            self._layout_regions()
        if self._edit_mode:
            self._edit_hint.setGeometry(0, 0, self.width(), EDIT_HINT_HEIGHT)
            self._size_grip.move(self.width() - 26, self.height() - 26)
            self._position_region_chrome()

    def _region_at(self, pos: QPoint) -> str | None:
        """The region key whose host contains ``pos`` (self-local), or None."""
        for key, host in self._region_hosts().items():
            if host.isVisible() and host.geometry().contains(pos):
                return key
        return None

    def mousePressEvent(self, event) -> None:
        if self._edit_mode and event.button() == Qt.MouseButton.LeftButton:
            # Hit-test the three regions first; a hit drags that region alone.
            if self._state is not None:
                key = self._region_at(event.position().toPoint())
                if key is not None:
                    self._begin_region_drag(key, event.globalPosition().toPoint())
                    event.accept()
                    return
            # Miss: fall back to moving the whole window.
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._edit_mode and self._drag_region is not None:
            delta = event.globalPosition().toPoint() - self._region_drag_start
            region = self._state.overlay_regions[self._drag_region]
            region.dx = self._region_drag_base[0] + delta.x()
            region.dy = self._region_drag_base[1] + delta.y()
            self._layout_regions()
            self._position_region_chrome()
            event.accept()
        elif self._edit_mode and self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        self._drag_region = None
        super().mouseReleaseEvent(event)

    # -- per-region positioning --------------------------------------------------

    def _region_hosts(self) -> dict[str, QWidget]:
        return {
            "lanes": self._lanes_host,
            "utility": self._utility_host,
            "alert": self._alert_host,
            "bars": self._bars_host,
        }

    def _default_region(self, key: str) -> OverlayRegion:
        """The stacked-layout default placement for a region host — the single
        source of truth used to seed and to backfill missing keys (e.g. the
        'utility' region absent from a layout saved before 1.11)."""
        return {
            "lanes": OverlayRegion(anchor="top"),
            "utility": OverlayRegion(anchor="top", dy=96),
            "alert": OverlayRegion(anchor="center"),
            "bars": OverlayRegion(anchor="bottom"),
        }.get(key, OverlayRegion())

    def _region_mode(self) -> bool:
        return self._state is not None and self._state.overlay_regions is not None

    def _begin_region_drag(self, key: str, global_start: QPoint) -> None:
        # First region drag initializes overlay_regions to defaults matching
        # the current stacked positions, so the untouched regions don't jump.
        if self._state.overlay_regions is None:
            self._state.overlay_regions = {
                name: self._default_region(name) for name in self._region_hosts()
            }
            self._activate_region_layout()
        region = self._state.overlay_regions.setdefault(key, self._default_region(key))
        self._drag_region = key
        self._region_drag_start = global_start
        self._region_drag_base = (region.dx, region.dy)

    def _activate_region_layout(self) -> None:
        """Take the three hosts out of the stacked QVBoxLayout so they can be
        placed manually. The stretch items stay behind harmlessly."""
        for host in self._region_hosts().values():
            self._main_layout.removeWidget(host)
        self._layout_regions()

    def _layout_regions(self) -> None:
        """Place each host at its anchor line + (dx, dy), centered horizontally
        on the window center by default. Lanes/bars grow downward from the
        anchor point; the legacy (None) path never calls this."""
        regions = self._state.overlay_regions if self._state is not None else None
        if not regions:
            return
        w, h = self.width(), self.height()
        cx = w // 2
        defaults = {
            "lanes": max(LANES_WIDTH, self._lanes_host.sizeHint().width()),
            "utility": max(320, self._utility_host.sizeHint().width()),
            "alert": w,
            "bars": BAR_WIDTH,
        }
        for key, host in self._region_hosts().items():
            # Backfill a region absent from a pre-1.11 saved layout (e.g.
            # 'utility') with its default so the host isn't stranded at (0, 0).
            region = regions.get(key) or self._default_region(key)
            host_w = region.width if region.width is not None else defaults[key]
            host_h = max(1, host.sizeHint().height())
            host.resize(host_w, host_h)
            x = cx + region.dx - host_w // 2
            if region.anchor == "top":
                y = REGION_MARGIN_TOP + region.dy
            elif region.anchor == "center":
                y = h // 2 + region.dy
            else:  # bottom
                y = h - REGION_MARGIN_BOTTOM - host_h + region.dy
            host.move(x, y)
            host.show()

    # -- positioning-mode preview & chrome ---------------------------------------

    def _set_region_chrome(self, on: bool) -> None:
        """Dashed border + title chip on each region host while editing."""
        for key, host in self._region_hosts().items():
            title = self._region_titles[key]
            if on:
                host.setStyleSheet(
                    f"#{host.objectName()} {{ border: 1px dashed rgba(255, 255, 255, 170); }}"
                )
                title.show()
                title.raise_()
            else:
                host.setStyleSheet("")
                title.hide()
        if on:
            self._position_region_chrome()

    def _position_region_chrome(self) -> None:
        for key, host in self._region_hosts().items():
            title = self._region_titles[key]
            if not title.isVisible():
                continue
            title.adjustSize()
            p = host.pos()
            title.move(p.x(), max(0, p.y()))
            title.raise_()

    def _show_preview(self) -> None:
        """Populate each region with labeled sample content so the user sees
        where CH lanes, alerts, and timer bars land. Idempotent; adds nothing
        to live state and publishes no events."""
        if self._preview_widgets:
            return
        # Sample CH lane with two static chips (and a sample cadence marker so
        # the muted "next cast" tick is visible while positioning, #15).
        lane = _ChainLane("Sample Target", self)
        lane.setFixedWidth(LANES_WIDTH)
        lane.cadence_seconds = 4
        row = self._build_lane_row("Sample Target", lane)
        self._lanes_layout.addWidget(row)
        lane.show()
        lane.add_static_chip("CH", 2)
        lane.add_static_chip("CH", 6)
        self._preview_widgets.append(row)

        # Sample alert label styled exactly like ``_center_text`` (yellow, like
        # the bard counter). Divergence from the Phase-1 note: inserted into the
        # alert host's layout (not the main layout) so it rides the alert region.
        label = QLabel("ENRAGED — sample alert", self)
        label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        label.setWordWrap(True)
        label.setStyleSheet("color: yellow; font-size: 32px; font-weight: bold;")
        shadow = QGraphicsDropShadowEffect(label)
        shadow.setOffset(0, 0)
        shadow.setBlurRadius(8)
        shadow.setColor(QColor("black"))
        label.setGraphicsEffect(shadow)
        self._alert_layout.insertWidget(self._alert_layout.indexOf(self._center_text) + 1, label)
        label.show()
        self._preview_widgets.append(label)

        # Sample utility line under the "Utility" header (#14).
        self._utility_header.show()
        util = QLabel("Rebuff: Sample — buff faded", self)
        util.setObjectName("OverlayUtilityLine")
        util.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        util.setStyleSheet("color: #ffd479; font-size: 20px; font-weight: bold;")
        self._utility_layout.addWidget(util, 0, Qt.AlignmentFlag.AlignHCenter)
        util.show()
        self._preview_widgets.append(util)

        # Sample timer bars (do NOT start ``_bar_timer`` — these never tick).
        for bar in (
            self._make_bar_widget("Sample Timer", DEFAULT_BAR_COLOR, 60, 45),
            self._make_bar_widget("CH Warning", "red", 10, 6),
        ):
            self._bars_layout.addWidget(bar)
            bar.show()
            self._preview_widgets.append(bar)

        if self._region_mode():
            self._layout_regions()

    def _clear_preview(self) -> None:
        """Remove all preview widgets from their layouts. Idempotent."""
        if not self._preview_widgets:
            return
        for widget in self._preview_widgets:
            for lay in (
                self._lanes_layout,
                self._alert_layout,
                self._bars_layout,
                self._utility_layout,
            ):
                lay.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        self._preview_widgets.clear()
        if not self._utility_lines:
            self._utility_header.hide()
        if self._region_mode():
            self._layout_regions()

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
        elif isinstance(event, CompleteHealCadenceEvent):
            self._on_ch_cadence(event)
        elif isinstance(event, CompleteHealEvent):
            self._on_complete_heal(event)

    def _on_ch_cadence(self, event: CompleteHealCadenceEvent) -> None:
        """Apply a declared CH cadence to the lanes' muted markers (#15)."""
        self._ch_cadence_seconds = event.seconds
        for lane in self._chain_lanes.values():
            lane.cadence_seconds = event.seconds
            lane.update()

    def _on_complete_heal(self, event: CompleteHealEvent) -> None:
        target = event.recipient or "?"
        lane = self._chain_lanes.get(target)
        if lane is None:
            lane = _ChainLane(target, self)
            lane.cadence_seconds = self._ch_cadence_seconds
            lane.setFixedWidth(520)
            lane.on_chip_done = lambda t=target: QTimer.singleShot(
                100, lambda: self._maybe_remove_lane(t)
            )
            self._chain_lanes[target] = lane
            self._lanes_layout.addWidget(self._build_lane_row(target, lane))
            lane.show()
        lane.last_call = datetime.now()
        lane.add_chip(event.position or "?")
        if not self._sweep_timer.isActive():
            self._sweep_timer.start()
        # Re-check just past the retention window of THIS call; earlier
        # timers fire harmlessly (retention not yet elapsed).
        QTimer.singleShot(
            int(self._ch_lane_retention_s * 1000) + 250,
            lambda: self._maybe_remove_lane(target),
        )
        self._update_visibility()

    def _build_lane_row(self, target: str, lane: _ChainLane) -> QWidget:
        """[name | lane] row: the target name lives in its own fixed-width column
        so it never covers the lane's "1" second-marker cell (EQTool keeps the
        name in a separate grid column beside the bar). The name is right-aligned
        within that column and sits ``CH_LANE_NAME_GAP`` px from the lane, so a
        short name hugs the lane instead of floating at the far-left edge."""
        row = QWidget(self)
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(CH_LANE_NAME_GAP)
        name = QLabel(row)
        name.setStyleSheet("color: #cccccc; font-size: 11px; font-weight: bold;")
        name.setFixedWidth(CH_LANE_NAME_WIDTH)
        name.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        metrics = QFontMetrics(name.font())
        name.setText(metrics.elidedText(target, Qt.TextElideMode.ElideRight, CH_LANE_NAME_WIDTH))
        name.setToolTip(target)
        layout.addWidget(name)
        layout.addWidget(lane)
        row.setLayout(layout)
        lane.row = row
        return row

    def _remove_lane(self, target: str) -> None:
        """Tear a lane's row out of the layout and the dict. Idempotent, and
        defensive: severs the chip-done callback so any late ``_chip_done``
        (from an animation finishing during teardown) cannot re-enter."""
        lane = self._chain_lanes.pop(target, None)
        if lane is None:
            return
        lane.on_chip_done = None
        lane.chips.clear()
        row = lane.row if lane.row is not None else lane
        self._lanes_layout.removeWidget(row)
        row.deleteLater()  # the lane (and name label) die with the row

    def _maybe_remove_lane(self, target: str) -> None:
        """Remove a lane only when it has no chips in flight AND the retention
        window since its last CH call has fully elapsed."""
        lane = self._chain_lanes.get(target)
        if lane is None:
            return
        idle_s = (datetime.now() - lane.last_call).total_seconds()
        if not lane.chips and idle_s >= self._ch_lane_retention_s:
            self._remove_lane(target)
        self._update_visibility()

    def _sweep_lanes(self) -> None:
        """Safety net: force-remove any lane idle past the retention window and
        the chip flight time, regardless of chip bookkeeping. This catches the
        leak where a chip's ``finished`` signal never fires and the normal
        ``_maybe_remove_lane`` gate (``not lane.chips``) stays false forever."""
        now = datetime.now()
        force_after = max(self._ch_lane_retention_s, CH_CHIP_SECONDS) + CH_LANE_FORCE_GRACE_S
        for target, lane in list(self._chain_lanes.items()):
            if (now - lane.last_call).total_seconds() >= force_after:
                self._remove_lane(target)
        if not self._chain_lanes:
            self._sweep_timer.stop()
        self._update_visibility()

    def _on_overlay_event(self, event: OverlayEvent) -> None:
        if event.section == "utility":
            self._on_utility_event(event)
            return
        if event.reset:
            # EQTool only clears when the reset matches what is displayed.
            if self._center_text.text() == event.text:
                self.clear_text()
            return
        self._center_text.setText(event.text)
        self._set_text_color(resolve_color(event.foreground, DEFAULT_TEXT_COLOR))
        self._clear_timer.start()
        self._update_visibility()

    def _on_utility_event(self, event: OverlayEvent) -> None:
        """Render a utility alert line in the dedicated utility section (#14)."""
        if event.reset:
            self._remove_utility_line(event.text)
            return
        color = resolve_color(event.foreground, DEFAULT_TEXT_COLOR)
        label = self._utility_lines.get(event.text)
        if label is None:
            label = QLabel(event.text, self)
            label.setObjectName("OverlayUtilityLine")
            label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            self._utility_layout.addWidget(label, 0, Qt.AlignmentFlag.AlignHCenter)
            self._utility_lines[event.text] = label
        label.setStyleSheet(f"color: {color}; font-size: 20px; font-weight: bold;")
        label.show()
        # Self-clearing safety net; the trigger engine also sends a reset.
        timer = self._utility_timers.get(event.text)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda t=event.text: self._remove_utility_line(t))
            self._utility_timers[event.text] = timer
        timer.start(self._clear_after_ms)
        self._utility_header.show()
        self._update_visibility()

    def _remove_utility_line(self, text: str) -> None:
        label = self._utility_lines.pop(text, None)
        if label is not None:
            self._utility_layout.removeWidget(label)
            label.deleteLater()
        timer = self._utility_timers.pop(text, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()
        if not self._utility_lines:
            self._utility_header.hide()
        self._update_visibility()

    def current_utility_texts(self) -> list[str]:
        """Utility section line texts (test/debug hook)."""
        return list(self._utility_lines.keys())

    def _make_bar_widget(
        self, name: str, color: str | None, total: int, remaining: int
    ) -> QProgressBar:
        """Build a styled countdown bar (shared by live bars and preview)."""
        total = max(1, int(total))
        bar = QProgressBar(self)
        bar.setObjectName("EventOverlayBar")
        bar.setRange(0, total)
        bar.setValue(max(0, min(total, int(remaining))))
        bar.setFixedHeight(22)
        bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        resolved = resolve_color(color, DEFAULT_BAR_COLOR)
        bar.setStyleSheet(
            "QProgressBar { background-color: rgba(10, 10, 10, 200);"
            " border: 1px solid #ffffff; color: #ffffff; font-weight: bold; }"
            f"QProgressBar::chunk {{ background-color: {resolved}; }}"
        )
        bar.setFormat(f"{name}  {max(0, int(remaining))}s")
        return bar

    def _on_timer_bar_event(self, event: TimerBarEvent) -> None:
        existing = self._bars.pop(event.name, None)
        if existing is not None:  # re-raise restarts the bar
            self._bars_layout.removeWidget(existing.widget)
            existing.widget.deleteLater()
        total = max(1, int(event.total_seconds))
        bar = self._make_bar_widget(event.name, event.bar_color, total, total)
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
        if self._region_mode():
            # Content height changes (bars/lanes added/removed) shift the
            # downward-growing regions; keep them anchored.
            self._layout_regions()
        if self._edit_mode:
            if not self.isVisible():
                self.show()
            return
        active = (
            bool(self._center_text.text())
            or bool(self._bars)
            or bool(self._chain_lanes)
            or bool(self._utility_lines)
        )
        if active and not self.isVisible():
            self.show()
        elif not active and self.isVisible():
            self.hide()

    # -- test/debug hooks --------------------------------------------------------

    def current_text(self) -> str:
        return self._center_text.text()

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
