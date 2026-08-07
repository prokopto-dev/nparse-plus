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
from nparseplus.core.timers import seconds_left, snap_to_second
from nparseplus.ui import skins
from nparseplus.ui.overlaybase import start_second_aligned
from nparseplus.ui.skinwidgets import paint_hairline, qcolor, set_caps

DEFAULT_CLEAR_AFTER_S = 4.0
BAR_TICK_MS = 200
BAR_WIDTH = 320
LANES_WIDTH = 520
DEFAULT_TEXT_COLOR = "red"
DEFAULT_BAR_COLOR = "steelblue"
DEFAULT_TEXT_SIZE = 32
DEFAULT_EMPHASIS = "pulse"
# Alert pulse cadence. Slow on purpose: fast enough to catch the eye at the
# edge of vision, slow enough that reading the word is never a chase.
PULSE_INTERVAL_MS = 700
PULSE_DIM = 0.42

# Separators an alert's "kicker — HEADLINE" is split on, longest first so
# " - " never steals the dash out of an em-dash form.
ALERT_SEPARATORS = (" — ", " -- ", " - ", ": ")

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


def split_alert_text(text: str) -> tuple[str, str]:
    """Split an alert into its kicker caps and its headline.

    Alerts arrive as one string ("Gorenaire — ENRAGED", "FTE: Someone"), and
    the design gives the two halves different jobs: a small tracked-out cap
    naming *who*, and the big word saying *what*. Splitting is presentational
    only — ``current_text`` and the reset match still use the whole string.
    Returns ``("", text)`` when there is nothing to split on.
    """
    for separator in ALERT_SEPARATORS:
        head, found, tail = text.partition(separator)
        if found and head.strip() and tail.strip():
            return head.strip(), tail.strip()
    return "", text


class _Hairline(QWidget):
    """The rule under an alert — solid in the middle, gone at both ends."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._color = ""
        self.setFixedHeight(1)

    def apply_skin(self, skin: skins.Skin) -> None:
        self._color = skin.alert_rule_color
        if not self._color or not skin.alert_rule_width:
            self.setFixedSize(0, 0)
            self.setVisible(False)
        else:
            self.setFixedHeight(1)
            self.setMinimumWidth(skin.alert_rule_width)
            self.setMaximumWidth(skin.alert_rule_width)
        self.update()

    def paintEvent(self, event) -> None:
        if not self._color:
            return
        painter = QPainter(self)
        paint_hairline(painter, self.rect(), self._color)
        painter.end()


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
        text_shadow: bool = True,
        text_size: int = DEFAULT_TEXT_SIZE,
        emphasis: str = DEFAULT_EMPHASIS,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._clear_after_ms = max(1000, int(clear_after_s * 1000))
        self._ch_lane_retention_s = max(0.0, ch_lane_retention_s)
        self._state = state
        self._on_save = on_save
        # The blur effect is re-evaluated per repaint of this translucent
        # always-on-top surface — expensive on macOS; setting-gated.
        self._text_shadow = text_shadow
        self._text_size = max(10, int(text_size))
        self._emphasis = emphasis if emphasis in ("plain", "pulse", "glow") else DEFAULT_EMPHASIS
        self._skin = skins.skin()
        #: The whole un-split alert string — what ``current_text`` reports and
        #: what a reset event must match (the labels show it in two pieces).
        self._alert_text = ""
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

        self._alert_kicker = QLabel("", self)
        self._alert_kicker.setObjectName("EventOverlayKicker")
        set_caps(self._alert_kicker)
        self._alert_kicker.hide()
        self._center_text = QLabel("", self)
        self._center_text.setObjectName("EventOverlayText")
        self._center_text.setWordWrap(True)
        self._alert_rule = _Hairline(self)

        # Alert emphasis (pulse/glow): a slow opacity beat on the headline.
        # Driven by a plain stylesheet swap rather than a graphics effect —
        # the label already carries the shadow/glow effect, and a widget only
        # gets one. Set up before the first _set_text_color, which reads the
        # pulse phase.
        self._pulse_on = True
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(PULSE_INTERVAL_MS)
        self._pulse_timer.timeout.connect(self._toggle_pulse)

        self._apply_text_shadow(self._center_text)
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
        self._alert_layout.addWidget(self._alert_kicker)
        self._alert_layout.addWidget(self._center_text)
        self._alert_layout.addWidget(self._alert_rule)
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

        self.apply_skin()
        self.hide()

    # -- skin --------------------------------------------------------------------

    def apply_skin(
        self, text_size: int | None = None, emphasis: str | None = None, shadow: bool | None = None
    ) -> None:
        """Re-dress the on-game surface from the active skin — live.

        The overlay is the one surface that sits directly on EverQuest, so it
        follows the skin like every other window but never follows the *theme*
        (a pale panel over the game is a flashbang; ``ui/theme.py`` says the
        same). Optional arguments let Settings push a changed text size,
        emphasis or shadow through in the same call.
        """
        self._skin = skins.skin()
        if text_size is not None:
            self._text_size = max(10, int(text_size))
        if emphasis is not None and emphasis in ("plain", "pulse", "glow"):
            self._emphasis = emphasis
        if shadow is not None and shadow != self._text_shadow:
            self._text_shadow = shadow
            self._center_text.setGraphicsEffect(None)
            self._apply_text_shadow(self._center_text)

        skin = self._skin
        align = (
            Qt.AlignmentFlag.AlignLeft
            if skin.alert_align == "left"
            else Qt.AlignmentFlag.AlignHCenter
        )
        for widget in (self._alert_kicker, self._center_text):
            widget.setAlignment(align | Qt.AlignmentFlag.AlignVCenter)
        for widget in (self._alert_kicker, self._center_text, self._alert_rule):
            self._alert_layout.setAlignment(widget, align)
        kicker_size = max(8, round(self._text_size * skin.alert_kicker_scale * 0.34))
        self._alert_kicker.setStyleSheet(
            f"color: {skin.alert_kicker_color}; font-size: {kicker_size}px;"
            " font-weight: bold; background: transparent;"
            f" letter-spacing: {kicker_size * skin.alert_kicker_tracking:.2f}px;"
        )
        self._alert_rule.apply_skin(skin)
        self._restyle_alert()
        for entry in self._bars.values():
            self._style_bar(entry.widget)
        for widget in self._preview_widgets:
            if isinstance(widget, QProgressBar):
                self._style_bar(widget)
        self._sync_pulse()

    def _sync_pulse(self) -> None:
        """Run the pulse timer only while an alert is up and asking for it."""
        wants = self._emphasis in ("pulse", "glow") and bool(self._alert_text)
        if wants and not self._pulse_timer.isActive():
            self._pulse_on = True
            self._pulse_timer.start()
        elif not wants and self._pulse_timer.isActive():
            self._pulse_timer.stop()
            self._pulse_on = True
            self._restyle_alert()

    def _toggle_pulse(self) -> None:
        self._pulse_on = not self._pulse_on
        self._restyle_alert()

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
        label = QLabel("ENRAGED", self)
        label.setAlignment(
            Qt.AlignmentFlag.AlignLeft
            if self._skin.alert_align == "left"
            else Qt.AlignmentFlag.AlignHCenter
        )
        label.setWordWrap(True)
        label.setStyleSheet(
            f"color: yellow; font-size: {self._text_size}px; font-weight: bold;"
            " background: transparent;"
        )
        self._apply_text_shadow(label)
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
            # ``self`` as the context object ties the timer to this widget's
            # lifetime: Qt drops the pending call when the overlay is
            # destroyed. Without it the functor still fires and touches
            # already-deleted C++ children, raising into the event loop.
            lane.on_chip_done = lambda t=target: QTimer.singleShot(
                100, self, lambda: self._maybe_remove_lane(t)
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
            self,  # context: cancelled if the overlay is destroyed first
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
            # EQTool only clears when the reset matches what is displayed —
            # matched against the whole alert, not the split headline.
            if self._alert_text == event.text:
                self.clear_text()
            return
        self._alert_text = event.text
        kicker, headline = split_alert_text(event.text)
        if kicker and self._skin.alert_mark:
            kicker = f"◆ {kicker} ◆"
        self._alert_kicker.setText(kicker)
        self._alert_kicker.setVisible(bool(kicker))
        self._center_text.setText(headline)
        self._alert_rule.setVisible(bool(self._skin.alert_rule_color))
        self._set_text_color(resolve_color(event.foreground, DEFAULT_TEXT_COLOR))
        self._restyle_alert()
        self._sync_pulse()
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
        """Build a skinned countdown bar (shared by live bars and preview).

        Still a ``QProgressBar`` — the chunk is what drains — but the text is
        two child labels rather than ``setFormat``: the design puts the
        trigger's name at the left edge and the countdown hard right, which a
        single centered format string cannot express.
        """
        total = max(1, int(total))
        bar = QProgressBar(self)
        bar.setObjectName("EventOverlayBar")
        bar.setRange(0, total)
        bar.setValue(max(0, min(total, int(remaining))))
        bar.setTextVisible(False)
        resolved = resolve_color(color, DEFAULT_BAR_COLOR)
        bar.setProperty("bar_color", resolved)

        row = QHBoxLayout(bar)
        row.setContentsMargins(7, 0, 7, 0)
        row.setSpacing(6)
        name_label = QLabel(name, bar)
        name_label.setObjectName("OverlayBarName")
        value_label = QLabel(f"{max(0, int(remaining))}s", bar)
        value_label.setObjectName("OverlayBarValue")
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(name_label, 1)
        row.addWidget(value_label, 0)
        # Stored as properties so ``_style_bar``/``_render_bar`` can reach them
        # from a plain QProgressBar handle (no bar subclass to keep in step).
        bar.setProperty("name_label", name_label)
        bar.setProperty("value_label", value_label)

        self._style_bar(bar)
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
            # Bars are not TimersService rows, so the row validator never sees
            # them — snap here so they share the spell window's second grid.
            ends_at=snap_to_second(datetime.now() + timedelta(seconds=total)),
            total_seconds=total,
            widget=bar,
        )
        self._bars[event.name] = entry
        self._bars_layout.addWidget(bar)
        self._render_bar(entry, datetime.now())
        if not self._bar_timer.isActive():
            # Phased to the wall-clock second (200 divides 1000) so a bar's
            # digit steps in lockstep with the spell window's rows.
            start_second_aligned(self._bar_timer, BAR_TICK_MS)
        self._update_visibility()

    # -- rendering -------------------------------------------------------------

    def _apply_text_shadow(self, label: QLabel) -> None:
        """Soft halo behind alert text — black by default, the alert's own
        color under the "glow" emphasis, and skipped entirely when the
        overlay_text_shadow setting is off (the blur re-renders per repaint
        of this translucent surface, a macOS compositing cost)."""
        glow = self._emphasis == "glow"
        if not self._text_shadow and not glow:
            return
        shadow = QGraphicsDropShadowEffect(label)
        shadow.setOffset(0, 0)
        if glow:
            halo = QColor(self._text_color or DEFAULT_TEXT_COLOR)
            halo.setAlpha(190)
            shadow.setBlurRadius(26)
            shadow.setColor(halo)
        else:
            shadow.setBlurRadius(8)
            shadow.setColor(QColor("black"))
        label.setGraphicsEffect(shadow)

    def _set_text_color(self, color: str) -> None:
        if color != self._text_color:
            self._text_color = color
            if self._emphasis == "glow" and self._center_text.graphicsEffect() is not None:
                # The halo is tinted from the alert color; re-make it.
                self._center_text.setGraphicsEffect(None)
                self._apply_text_shadow(self._center_text)
            self._restyle_alert()

    def _restyle_alert(self) -> None:
        """Paint the headline at the current size, color and pulse phase."""
        color = self._text_color or DEFAULT_TEXT_COLOR
        if not self._pulse_on:
            dim = qcolor(color)
            dim.setAlpha(round(255 * PULSE_DIM))
            color = f"rgba({dim.red()}, {dim.green()}, {dim.blue()}, {dim.alpha()})"
        self._center_text.setStyleSheet(
            f"color: {color}; font-size: {self._text_size}px; font-weight: bold;"
            " background: transparent;"
        )

    def clear_text(self) -> None:
        self._clear_timer.stop()
        self._alert_text = ""
        self._center_text.setText("")
        self._alert_kicker.setText("")
        self._alert_kicker.hide()
        self._alert_rule.setVisible(False)
        self._sync_pulse()
        self._update_visibility()

    def _style_bar(self, bar: QProgressBar) -> None:
        """(Re)apply the active skin to one countdown bar."""
        skin = self._skin
        color = bar.property("bar_color") or DEFAULT_BAR_COLOR
        bar.setFixedHeight(skin.overlay_bar_height)
        fill = (
            f"qlineargradient(x1: 0, y1: 0, x2: 1, y2: 0,"
            f" stop: 0 {skins.rgba(color, 0.62)}, stop: 1 {skins.rgba(color, 0.16)})"
        )
        chunk = f"QProgressBar::chunk {{ background: {fill};"
        if skin.overlay_bar_style == "full":
            chunk += f" border-left: {skin.row_rule}px solid {color};"
        chunk += " }"
        border = f"1px solid {skin.overlay_bar_border}" if skin.overlay_bar_border else "none"
        bar.setStyleSheet(
            f"QProgressBar {{ background-color: {skin.overlay_bar_bg}; border: {border}; }}" + chunk
        )
        name = bar.property("name_label")
        value = bar.property("value_label")
        text_size = max(8, round(skin.overlay_bar_height * 0.5))
        if name is not None:
            name.setStyleSheet(
                f"color: #f3f5fe; font-size: {text_size}px; font-weight: bold;"
                " background: transparent;"
            )
        if value is not None:
            value.setStyleSheet(
                f"color: {color}; font-size: {text_size + 2}px; font-weight: bold;"
                " background: transparent;"
            )

    def _render_bar(self, entry: _TimerBar, now: datetime) -> None:
        remaining = seconds_left(entry.ends_at, now)
        entry.widget.setValue(min(entry.total_seconds, remaining))
        value = entry.widget.property("value_label")
        if value is not None:
            value.setText(f"{remaining}s")

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
            bool(self._alert_text)
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
        """The whole alert string — the labels show it split (see
        ``split_alert_text``), so the headline label alone is not it."""
        return self._alert_text

    def bar_countdown_text(self, name: str) -> str:
        """The countdown as rendered on ``name``'s bar (test/debug hook).

        The bar's text is two child labels now, not ``QProgressBar.format()``
        — this is the supported way to read what a bar says.
        """
        entry = self._bars.get(name)
        if entry is None:
            return ""
        label = entry.widget.property("value_label")
        return label.text() if label is not None else ""

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
