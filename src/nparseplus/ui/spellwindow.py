"""Spell timer overlay — the new-core replacement for the legacy spells window.

A small self-contained frameless overlay (same Qt flag recipe as
``helpers.parser.ParserWindow``, but reading/writing the NEW
``Settings.windows['spells']`` model instead of the legacy config dict).
It polls ``backend.timers.snapshot()`` on a 250 ms QTimer and renders the
rows grouped by target, YOU_GROUP first.
"""

from __future__ import annotations

import colorsys
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from PySide6.QtCore import QCoreApplication, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QMouseEvent, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QProgressBar,
    QScrollArea,
    QSizeGrip,
    QVBoxLayout,
    QWidget,
)

from nparseplus.config.settings import Settings, WindowState, find_player
from nparseplus.core.handlers.boat import BOATS_GROUP
from nparseplus.core.player import ActivePlayer
from nparseplus.core.spells.matching import hide_spell
from nparseplus.core.spells.models import Spell
from nparseplus.core.timers import (
    MOB_TIMER_GROUP,
    ROLL_TIMER_GROUP,
    TRIGGER_TIMER_GROUP,
    YOU_GROUP,
    CounterRow,
    DisplayGroup,
    RollRow,
    Row,
    SpellRow,
    countdown_target,
    fraction_remaining,
    group_rows_for_display,
    in_pop_window,
    series_label,
)
from nparseplus.ui import appquit, chrome, skins, theme
from nparseplus.ui.overlaybase import EdgeResizeMixin, format_mmss, start_second_aligned
from nparseplus.ui.skinwidgets import (
    SkinPanel,
    SkinTitleBar,
    paint_full_row_bar,
    set_caps,
)
from nparseplus.ui.spellicons import ICON_SIZE, spell_icon_pixmap

WINDOW_KEY = "spells"
REFRESH_INTERVAL_MS = 250
FLASH_INTERVAL_MS = 500  # post-expiry rebuff-prompt flash cadence (#16)
DEFAULT_GEOMETRY = (400, 0, 220, 400)

# Progress-bar chunk colors per row kind. These name a row's MEANING, so they
# read from the semantic tokens rather than repeating the hex — several of
# which are shared with unrelated surfaces (a zone exit is the same green) and
# must stay free to diverge.
COLOR_BENEFICIAL = chrome.GOOD
COLOR_DETRIMENTAL = chrome.BAD
COLOR_COOLDOWN = chrome.COOLDOWN
COLOR_TIMER = chrome.TIMER
COLOR_ROLL = chrome.ROLL
COLOR_POP_WINDOW = chrome.POP_WINDOW

# Prefixed to the countdown of a respawn row inside its pop window (#125), so
# the digits say what they are counting: "POP 11:59:58" is time left to the
# LATEST possible spawn, not time until one.
POP_WINDOW_PREFIX = "POP "

BAR_MAX = 1000

# Separator under a full-row (Ledger) row — see _RowWidget.paintEvent.
ROW_RULE_COLOR = QColor(0, 0, 0, 140)

# Bar color fade (``bar_fade_to_red``). NOT an EQTool port — its
# BaseTriggerViewModel.ProgressBarColor is a static brush picked once from the
# row type, and SpellWindow.xaml binds it with no converter. We diverge on
# purpose: a bar that drifts toward red reads as urgent without reading digits.
FADE_TARGET = COLOR_DETRIMENTAL
# The fade is quantized to this many steps. update_row only calls
# setStyleSheet when the color string actually changes; a continuous fade
# would defeat that guard and force a full Qt style re-parse per row at the
# 250 ms refresh. 12 buckets means a row restyles at most 12 times over its
# whole life, whatever its length.
FADE_STEPS = 12

# Fold marker on a collapsed section header (#129). Only a COLLAPSED header
# carries one: an expanded window is unchanged from before this feature, which
# keeps a 220 px-wide overlay from spending a glyph column on an affordance
# that is only ever true for a section or two.
#
# U+25BA and not the smaller U+25B8/U+25B6 triangles: the bundled Noto Sans
# (data/fonts, the family every skin resolves to) has no cmap entry for those,
# so they render only through whatever Qt falls back to — which on a Flatpak
# with a thin font set is exactly where a marker turns into a tofu box.
COLLAPSE_MARKER = "►"


def section_key(group: DisplayGroup) -> str:
    """Stable persisted identity of one displayed section (see #129).

    The orientation is part of the key because the two orientations name
    different things: a target-headed section is keyed by its group (a player,
    an NPC, or a built-in timer section) and a raid-mode section by a spell
    name. Without the prefix, folding the spell "Aegolism" would also fold a
    target who happens to be called that — and, more to the point, a section
    that flips orientation mid-fight (which ``group_rows_for_display``
    recomputes every render, #17) would carry the other one's fold state.
    """
    return f"{group.orientation}:{group.header}"


def collapsed_header_text(label: str, count: int) -> str:
    """The one-line stub a folded section leaves behind.

    The count is the point: a section folded away and then forgotten must not
    look like a section with nothing in it.
    """
    return f"{COLLAPSE_MARKER} {label}  ({count})"


class TimersLike(Protocol):
    def snapshot(self) -> list[Row]: ...


class BackendLike(Protocol):
    """The slice of ``composition.Backend`` this window needs (test-fakeable)."""

    timers: TimersLike
    settings: Settings
    player: ActivePlayer


def row_sort_key(row: Row, now: datetime, mode: str) -> tuple:
    """Sort key for rows under one group header.

    Rolls always sort by roll value descending (name-casefold tiebreak),
    regardless of ``mode`` — every roll in a group shares one window, so
    time-remaining is meaningless between them. Otherwise ``"alphabetical"``
    orders by name and ``"time_remaining"`` (default) orders soonest-to-expire
    first; counters have no ``ends_at`` so they sort last under the time mode,
    name-tiebroken. All keys are ``(number, str)`` tuples so they compare.

    The time-remaining key is ``countdown_target``, not ``ends_at``: a respawn
    row inside its pop window (#125) has ``ends_at`` in the past, so sorting on
    it would pin the row to the top of its section for the whole window — the
    one place a raid least wants a fixed row. Phase 1 is byte-identical, since
    ``countdown_target`` returns ``ends_at`` there. Deliberately NOT extended to
    a post-expiry REBUFF ``SpellRow``, whose ``ends_at`` is also past: that row
    is a flashing prompt and floating it is arguably the point.
    """
    name_key = row.name.casefold()
    if isinstance(row, RollRow):
        return (-row.roll, name_key)
    if mode == "alphabetical":
        return (0, name_key)
    target = countdown_target(row, now)
    if target is None:
        return (float("inf"), name_key)
    return ((target - now).total_seconds(), name_key)


def bar_color(row: Row, now: datetime | None = None) -> str:
    """The row's base color at full duration (its type coding).

    ``now`` is optional so every existing call site keeps working; pass it and
    a respawn row inside its pop window (#125) reads as its own colour. Checked
    FIRST — the phase is a stronger statement about the row than its type is,
    and a window row is always a plain ``TimerRow`` underneath.
    """
    if now is not None and in_pop_window(row, now):
        return COLOR_POP_WINDOW
    if isinstance(row, SpellRow):
        if row.is_cooldown:
            return COLOR_COOLDOWN
        return COLOR_DETRIMENTAL if row.detrimental else COLOR_BENEFICIAL
    if isinstance(row, RollRow):
        return COLOR_ROLL
    return COLOR_TIMER


def _rgb(color: str) -> tuple[float, float, float]:
    value = color.lstrip("#")
    return tuple(int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def fade_color(base: str, fraction: float) -> str:
    """Blend ``base`` toward :data:`FADE_TARGET` as ``fraction`` drains to 0.

    Interpolation is in HSV along the SHORTER hue arc, not straight RGB: an
    RGB blend from the beneficial green to red passes through a muddy khaki
    (#776b4c at the midpoint), while the hue arc gives the expected
    green -> yellow -> amber -> orange -> red ramp and keeps the blue and
    purple bases saturated on their way round.

    ``fraction >= 1`` returns ``base`` untouched, so a freshly-added row looks
    exactly as it did before the fade existed.
    """
    if fraction >= 1.0:
        return base
    # Quantize first — see FADE_STEPS.
    t = min(max(1.0 - fraction, 0.0), 1.0)
    t = round(t * FADE_STEPS) / FADE_STEPS
    if t <= 0.0:
        return base
    h0, s0, v0 = colorsys.rgb_to_hsv(*_rgb(base))
    h1, s1, v1 = colorsys.rgb_to_hsv(*_rgb(FADE_TARGET))
    delta = h1 - h0
    if delta > 0.5:
        delta -= 1.0
    elif delta < -0.5:
        delta += 1.0
    hue = (h0 + delta * t) % 1.0
    red, green, blue = colorsys.hsv_to_rgb(hue, s0 + (s1 - s0) * t, v0 + (v1 - v0) * t)
    return f"#{round(red * 255):02x}{round(green * 255):02x}{round(blue * 255):02x}"


def _fades(row: Row) -> bool:
    """Whether ``row``'s bar is eligible for the fade.

    Boat legs and both kinds of roll row are excluded — not just by taste but
    because their ``remaining / total_duration_s`` is not a progress value.
    Boats set ``total_duration_s`` to the whole trip but ``ends_at`` to the
    next dock; the API roll windows (Ring 8, Scout Charisa) carry the nominal
    10 h / 24 h cycle as their duration; and ``/random`` rolls all share one
    window that every new roll resets. Each would open part-way toward red.
    """
    if row.group in (BOATS_GROUP, ROLL_TIMER_GROUP):
        return False
    return not isinstance(row, RollRow)


def row_bar_color(row: Row, fraction: float, fade: bool, now: datetime | None = None) -> str:
    """The chunk color to paint: the base color, faded toward red if enabled."""
    base = bar_color(row, now)
    if not fade or not _fades(row):
        return base
    return fade_color(base, fraction)


def header_kind(group: str, rows: list[Row]) -> str:
    """Which accent a group header wears (a ``skins.KIND_*`` value).

    Headers and the bars beneath them have to agree — a red "A SAND GIANT"
    cap over red debuff bars, tan over your own buffs. The group key settles
    the special sections; for a target group the dominant row kind decides,
    so a mob you have debuffed reads red while a buffed groupmate reads as a
    player. Pure, so the mapping is testable without a window.
    """
    if group == YOU_GROUP:
        return skins.KIND_YOU
    if group in (TRIGGER_TIMER_GROUP, MOB_TIMER_GROUP, BOATS_GROUP):
        return skins.KIND_TIMER
    if group == ROLL_TIMER_GROUP or (rows and all(isinstance(r, RollRow) for r in rows)):
        return skins.KIND_ROLL
    spells = [r for r in rows if isinstance(r, SpellRow)]
    if any(r.detrimental and not r.is_cooldown for r in spells):
        return skins.KIND_DETRIMENTAL
    if spells and all(r.is_cooldown for r in spells):
        return skins.KIND_COOLDOWN
    return skins.KIND_PLAYER


class _RowWidget(QFrame):
    """One timer row: name + remaining time above a thin progress bar."""

    def __init__(
        self,
        parent: QWidget | None = None,
        warning_threshold: Callable[[], int] = lambda: 0,
        fade_enabled: Callable[[], bool] = lambda: False,
        skin: skins.Skin | None = None,
        font_size: int = 12,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SpellTimerRow")
        self.row_name = ""
        #: The last-rendered Row — snapshot() copies the list, not the rows,
        #: so this identity works for TimersService.remove_row (context menu).
        self.row: Row | None = None
        self._color = ""
        self._warning_threshold = warning_threshold
        self._fade_enabled = fade_enabled
        self._warning = False
        #: True while this row is a post-expiry rebuff prompt (#16) — it drives
        #: the flash and makes a left-click dismiss the row.
        self.expired = False
        self._flash_on = False
        self._skin = skin if skin is not None else skins.skin()
        #: Remaining fraction, kept for the Ledger skin's painted full-row bar
        #: (the stacked skins read it straight off the QProgressBar instead).
        self._fraction = 1.0

        self._icon = QLabel(self)
        self._icon.setObjectName("SpellTimerRowIcon")
        self._icon.setFixedSize(ICON_SIZE, ICON_SIZE)
        self._icon.setVisible(False)
        self._icon_index: int | None = None
        self._name = QLabel(self)
        self._name.setObjectName("SkinRowName")
        self._value = QLabel(self)
        self._value.setObjectName("SkinRowValue")
        self._value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self._text_row = QHBoxLayout()
        self._text_row.setContentsMargins(0, 0, 0, 0)
        self._text_row.setSpacing(4)
        self._text_row.addWidget(self._icon, 0)
        self._text_row.addWidget(self._name, 1)
        self._text_row.addWidget(self._value, 0)

        self._bar = QProgressBar(self)
        self._bar.setObjectName("SpellTimerRowBar")
        self._bar.setRange(0, BAR_MAX)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(5)

        layout = QVBoxLayout()
        layout.setSpacing(1)
        layout.addLayout(self._text_row)
        layout.addWidget(self._bar)
        self.setLayout(layout)
        self.apply_skin(self._skin, font_size)

    def apply_skin(self, skin: skins.Skin, font_size: int) -> None:
        """Re-lay the row for ``skin``: stacked bar vs. painted full-row bar.

        Under ``row_style == "full"`` (Ledger) the progress bar is not a
        widget at all — the row paints the draining block behind its own
        labels — so the QProgressBar is hidden and the row takes a fixed
        height instead of hugging its two stacked lines.
        """
        self._skin = skin
        top, right, bottom, left = skin.row_pad
        self.layout().setContentsMargins(left, top, right, bottom)
        self._icon.setFixedSize(skin.icon_size, skin.icon_size)
        full = skin.row_style == "full"
        self._bar.setVisible(not full and not self.expired)
        if full:
            self.setFixedHeight(skins.full_row_height(skin, font_size))
            self.layout().setSpacing(0)
        else:
            self.setMinimumHeight(0)
            self.setMaximumHeight(16777215)
            self._bar.setFixedHeight(skin.bar_height)
            self.layout().setSpacing(1)
        self._color = ""  # force the next update_row to restyle the bar
        self.update()

    def paintEvent(self, event) -> None:
        """Ledger's row background — drawn here, under the child labels.

        Qt paints a widget before its children, so the block lands behind the
        name and countdown without a stacking-order dance.
        """
        super().paintEvent(event)
        if self._skin.row_style != "full" or not self._color or self.expired:
            return
        painter = QPainter(self)
        rect = self.rect()
        paint_full_row_bar(painter, rect, self._color, self._fraction, self._skin.row_rule)
        # Rows butt against each other in this skin; a dark hairline is what
        # separates them (the stacked skins get that separation for free from
        # the gap between a bar and the next row's name).
        painter.fillRect(QRect(rect.left(), rect.bottom(), rect.width(), 1), ROW_RULE_COLOR)
        painter.end()

    def update_row(self, row: Row, now: datetime, label: str | None = None) -> None:
        """Render ``row`` — read-only; never mutates the model.

        ``label`` overrides the displayed name (raid mode shows the target
        under a spell header instead of the spell name); the row identity used
        by the context menu stays ``row`` regardless.
        """
        self.row_name = row.name
        self.row = row
        # Candidate windows of one spawn share a name, so the label is what
        # tells them apart: "Lodizal  (2 of 3)" (#125).
        display = label if label is not None else row.name
        series = series_label(row)
        self._name.setText(f"{display}  ({series})" if series else display)
        self._update_icon(row)
        expired = isinstance(row, SpellRow) and row.expired_at is not None
        if expired != self.expired:
            self.expired = expired
            if not expired:
                # Reused for a live row again — clear ALL flash styling (name and
                # value) so a recast row's countdown isn't stuck red/bold, and
                # reset the warning flag so _update_warning re-applies from clean.
                self._name.setStyleSheet("")
                self._value.setStyleSheet("")
                self._warning = False
        full = self._skin.row_style == "full"
        if expired:
            # Post-expiry rebuff prompt: no countdown, flashing handled below.
            self._value.setText("REBUFF")
            self._bar.setVisible(False)
            self._fraction = 0.0
            self.apply_flash(self._flash_on)
            return
        if isinstance(row, CounterRow):
            self._value.setText(f"x{row.count}")
            self._bar.setVisible(False)
            self._fraction = 0.0
            self.update()
            return
        # countdown_target, not ends_at: inside a pop window (#125) the row is
        # counting to the LATEST possible pop, not to an end that has passed.
        # The phase comes from ``now`` and never from ``window_opened_at`` —
        # the driver ticks at 100 ms and this repaints at 250 ms, so the stamp
        # and this frame's clock disagree by up to a quarter second, and only
        # deriving from ``now`` keeps digits, bar and colour on the same side
        # of the crossover.
        target = countdown_target(row, now) or row.ends_at
        remaining = max(0.0, (target - now).total_seconds())
        if isinstance(row, RollRow):
            self._value.setText(f"{row.roll}/{row.max_roll}  {format_mmss(remaining)}")
        elif in_pop_window(row, now):
            self._value.setText(f"{POP_WINDOW_PREFIX}{format_mmss(remaining)}")
        else:
            self._value.setText(format_mmss(remaining))
        self._update_warning(row, remaining)
        fraction = fraction_remaining(row, now)
        self._bar.setValue(int(fraction * BAR_MAX))
        self._bar.setVisible(not full)
        color = row_bar_color(row, fraction, self._fade_enabled(), now)
        if color != self._color:
            self._color = color
            if not full:
                self._bar.setStyleSheet(skins.row_bar_style(self._skin, color))
        if full:
            # The painted bar redraws only when the block visibly moves —
            # a 250 ms repaint of every row is the cost this guard avoids.
            step = round(fraction * self.width())
            if step != getattr(self, "_painted_step", None) or color != getattr(
                self, "_painted_color", None
            ):
                self._painted_step = step
                self._painted_color = color
                self._fraction = fraction
                self.update()
            else:
                self._fraction = fraction

    def apply_flash(self, on: bool) -> None:
        """Toggle the post-expiry flash (#16). No-op unless this row is an
        expired rebuff prompt; the window's flash timer drives ``on``."""
        self._flash_on = on
        if not self.expired:
            return
        style = f"color: {theme.palette().warning_text}; font-weight: bold;" if on else ""
        self._name.setStyleSheet(style)
        self._value.setStyleSheet(style)

    def _update_warning(self, row: Row, remaining: float) -> None:
        """Buff-fade pre-warning: the time label turns red inside the window
        (visual side of core/handlers/buff_warning.py)."""
        threshold = self._warning_threshold()
        warning = (
            threshold > 0
            and isinstance(row, SpellRow)
            and row.group == YOU_GROUP
            and not row.is_cooldown
            and not row.detrimental
            and 0 < remaining <= threshold
        )
        if warning != self._warning:
            self._warning = warning
            self._value.setStyleSheet(
                f"color: {theme.palette().warning_text}; font-weight: bold;" if warning else ""
            )

    def _update_icon(self, row: Row) -> None:
        """Gem icon for spell rows (bundled sprite sheets); hidden otherwise."""
        icon_index = row.spell.spell_icon if isinstance(row, SpellRow) else None
        if icon_index == self._icon_index:
            return
        self._icon_index = icon_index
        pixmap = spell_icon_pixmap(icon_index) if icon_index else None
        if pixmap is None:
            self._icon.clear()
            self._icon.setVisible(False)
        else:
            self._icon.setPixmap(pixmap)
            self._icon.setVisible(True)


class SpellTimerWindow(EdgeResizeMixin, QWidget):
    """Frameless always-on-top overlay listing the backend's timer rows."""

    def __init__(
        self,
        backend: BackendLike,
        on_save: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._backend = backend
        self._on_save = on_save
        self._drag_offset: QPoint | None = None
        # A left-press over a group header ARMS a fold (#129); the toggle
        # happens on release and only if the window did not move, so a header
        # stays a drag handle — on a full window it is most of the surface.
        self._press_section: str | None = None
        self._press_pos: QPoint | None = None
        self._quitting = False
        self._headers: dict[str, QLabel] = {}
        self._row_widgets: dict[tuple[str, str, str, int], _RowWidget] = {}

        state = backend.settings.windows.get(WINDOW_KEY)
        if state is None:
            state = WindowState(shown=True)  # first run: show the window
            backend.settings.windows[WINDOW_KEY] = state
        self._state = state

        self.setObjectName("SpellTimerWindow")
        self.setWindowTitle("Timers")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._apply_flags()
        self.setGeometry(*(state.geometry or DEFAULT_GEOMETRY))
        self.setWindowOpacity(state.opacity)

        self._skin = skins.skin()
        self._font_size = max(6, backend.settings.general.font_size)

        # Skinned title bar: the gem mark + the window's caps.
        self._title_bar = SkinTitleBar(self._skin, "TIMERS", count=True, parent=self)
        self._title_count = self._title_bar.count

        self._rows_layout = QVBoxLayout()
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(1)

        # The rows live inside a scroll area so the WINDOW size is the user's
        # choice: it no longer inflates as rows arrive (and then sticks huge
        # after they leave) — overflow scrolls instead.
        rows_host = QWidget(self)
        host_layout = QVBoxLayout(rows_host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(0)
        host_layout.addLayout(self._rows_layout, 0)
        host_layout.addStretch(1)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setWidget(rows_host)

        self._container = SkinPanel(self._skin, parent=self)
        self._container.setObjectName("SpellTimerContainer")
        container_layout = QVBoxLayout(self._container)
        container_layout.setSpacing(0)
        container_layout.addWidget(self._title_bar, 0)
        container_layout.addWidget(self._scroll, 1)

        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._container)
        self.setLayout(outer)
        self.setMinimumSize(140, 120)

        # Frameless windows have no OS resize border; the corner grip is one
        # resize affordance, edge/corner drag (EdgeResizeMixin) is the other.
        # Track the mouse so hover moves reach mouseMoveEvent for edge cursors.
        self.setMouseTracking(True)
        self._grip = QSizeGrip(self)
        self._grip.raise_()
        self._persist_resize = QTimer(self)
        self._persist_resize.setSingleShot(True)
        self._persist_resize.setInterval(400)
        self._persist_resize.timeout.connect(self.persist_state)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._on_refresh_tick)
        # Phased to the wall-clock second so every countdown digit changes ON
        # the second, in step with the event overlay's bars (250 divides 1000,
        # so the alignment holds once set).
        start_second_aligned(self._refresh_timer, REFRESH_INTERVAL_MS)

        # Post-expiry rebuff prompts flash (#16); cheap and always running.
        self._flash_on = False
        self._flash_timer = QTimer(self)
        self._flash_timer.timeout.connect(self._toggle_flash)
        self._flash_timer.start(FLASH_INTERVAL_MS)

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._on_app_quit)

        # Last: apply_skin renders through refresh(), which needs the flash
        # phase and the timers above to exist.
        self.apply_skin()

        if state.shown:
            self.show()

    # -- rendering -------------------------------------------------------------

    def _on_refresh_tick(self) -> None:
        """Poll-timer entry: skip all render work while hidden (showEvent
        re-renders immediately on reopen). refresh() itself stays unguarded
        so tests and explicit callers always render."""
        if self.isVisible():
            self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()

    def _row_hidden(self, row: Row, show_classes: list[int] | None) -> bool:
        """Visibility pass — SpellWindowViewModel.cs order: the YOU group is
        always visible, NPC targets are never hidden, then you_only_spells,
        then the active character's class filter (HideSpell).

        ``show_classes`` is the active character's class filter, resolved once
        by the caller for the whole refresh pass."""
        if row.group == YOU_GROUP:
            return False
        sw = self._backend.settings.spellwindow
        if row.group == BOATS_GROUP and not sw.show_boats:
            return True
        if row.group == MOB_TIMER_GROUP and not sw.show_mob_timers:
            return True
        if row.group == ROLL_TIMER_GROUP and not sw.show_roll_timers:
            return True
        if row.group == TRIGGER_TIMER_GROUP and not sw.show_custom_timers:
            return True
        if isinstance(row, RollRow) and not sw.show_random_rolls:
            return True
        if isinstance(row, SpellRow) and not row.is_target_player:
            return False
        if sw.you_only_spells and isinstance(row, SpellRow):
            # Only OTHER PLAYERS' spell rows — boats, custom/respawn, trigger
            # timers, counters, and rolls are not "spells" and stay visible.
            return True
        if isinstance(row, SpellRow):
            return hide_spell(show_classes, row.spell.class_levels)
        return False

    def _active_player_info(self):
        player = self._backend.player
        server_key = player.server_key
        if server_key is None or not player.name:
            return None
        return find_player(self._backend.settings, player.name, server_key)

    def _buff_fade_warning_seconds(self) -> int:
        return self._backend.settings.spellwindow.buff_fade_warning_seconds

    def _bar_fade_enabled(self) -> bool:
        return self._backend.settings.spellwindow.bar_fade_to_red

    def refresh(self, now: datetime | None = None) -> None:
        """Re-render from ``timers.snapshot()`` (rows are never mutated).

        Rebuilds the layout order each tick but reuses the per-row widgets
        keyed by (kind, name, group, dup-index) — cheap at overlay scale.
        """
        now = now if now is not None else datetime.now()
        rows = self._backend.timers.snapshot()
        # The active-character class filter is constant across one refresh pass;
        # resolve it once here instead of per SpellRow inside _row_hidden.
        info = self._active_player_info()
        show_classes = info.show_spells_for_classes if info is not None else None
        rows = [row for row in rows if not self._row_hidden(row, show_classes)]

        # Orientation is computed in the Qt-free core (#17): target-headed by
        # default, spell-headed for raid buffs only when opt-in AND targets
        # outnumber spells. Recomputed every tick, so it never gets stuck.
        display_groups = group_rows_for_display(
            rows, group_by_spell=self._backend.settings.spellwindow.raid_group_by_spell
        )
        sort_mode = self._backend.settings.spellwindow.row_sort

        # Widgets in display order; the layout itself is only rebuilt when
        # this order changes (see below) — per-widget text/flash updates
        # happen every pass either way.
        entries: list[QWidget] = []
        order: list[object] = []

        used_headers: set[str] = set()
        used_rows: set[tuple[str, str, str, int]] = set()
        dup_counter: dict[tuple[str, str, str], int] = {}
        collapsed = set(self._state.collapsed_groups)
        total_rows = 0
        for group in display_groups:
            spell_headed = group.orientation == "spell"
            # Header widgets are keyed by (orientation, header) so a spell name
            # can never collide with a same-named target group.
            hkey = f"{group.orientation}\x00{group.header}"
            skey = section_key(group)
            folded = skey in collapsed
            label = group.header if spell_headed else self._group_label(group.header)
            text = collapsed_header_text(label, len(group.rows)) if folded else label
            header = self._headers.get(hkey)
            if header is None:
                header = QLabel(text, self._container)
                header.setObjectName("SpellTimerGroup")
                # Only target headers map to a clearable group (context menu).
                header.setProperty("group_key", None if spell_headed else group.header)
                # EVERY header collapses, raid-mode spell headers included:
                # clearing is destructive and a spell section spans several
                # target groups, so there is no single group to clear (which is
                # why ``group_key`` is None there) — but folding is
                # display-only, and one buff over forty raiders is exactly the
                # section worth folding. Its identity is the orientation-keyed
                # ``section_key``, not the clearable group key.
                header.setProperty("section_key", skey)
                set_caps(header)
                self._headers[hkey] = header
            elif header.text() != text:
                # Target class can arrive later (PlayerTracker /who sync), and
                # a folded header's count moves with its rows.
                header.setText(text)
            kind = header_kind(group.header, group.rows)
            if header.property("kind") != kind:
                header.setProperty("kind", kind)
                header.setStyleSheet(skins.header_style(self._skin, self._font_size, kind))
            entries.append(header)
            # ``folded`` rides in the order key so a fold is a layout change
            # even for a section whose rows are all that would have differed.
            order.append(("H", hkey, folded))
            used_headers.add(hkey)
            total_rows += len(group.rows)
            if folded:
                # Display-only: the rows are not built, but they are still in
                # the service, still counting, and still announcing on expiry —
                # the same contract as the per-section show/hide toggles.
                continue
            # Target sections re-sort live by the user's mode; spell sections
            # keep the core's deterministic by-target order.
            ordered = (
                group.rows
                if spell_headed
                else sorted(group.rows, key=lambda r: row_sort_key(r, now, sort_mode))
            )
            for row in ordered:
                base = (type(row).__name__, row.name.casefold(), row.group.casefold())
                index = dup_counter.get(base, 0)
                dup_counter[base] = index + 1
                key = (*base, index)
                widget = self._row_widgets.get(key)
                if widget is None:
                    widget = _RowWidget(
                        self._container,
                        warning_threshold=self._buff_fade_warning_seconds,
                        fade_enabled=self._bar_fade_enabled,
                        skin=self._skin,
                        font_size=self._font_size,
                    )
                    self._row_widgets[key] = widget
                # In a spell section the row shows its target, not the spell.
                widget.update_row(row, now, label=row.group.strip() if spell_headed else None)
                widget.apply_flash(self._flash_on)
                entries.append(widget)
                order.append(("R", key))
                used_rows.add(key)

        for hkey in [h for h in self._headers if h not in used_headers]:
            self._headers.pop(hkey).deleteLater()
        for key in [k for k in self._row_widgets if k not in used_rows]:
            self._row_widgets.pop(key).deleteLater()

        # The window's own count is every row it is showing you, folded
        # sections included — a fold must not read as timers that went away.
        self._title_count.setText(str(total_rows) if total_rows else "")

        # Only touch the layout when the widget sequence actually changed —
        # the per-tick takeAt/addWidget/adjustSize teardown forced a full
        # relayout+repaint 4x/sec even with a completely stable row set.
        if order == getattr(self, "_layout_order", None):
            return
        self._layout_order = order
        while self._rows_layout.count():
            self._rows_layout.takeAt(0)
        for widget in entries:
            self._rows_layout.addWidget(widget)
            widget.show()
        # Re-fit the scroll host to the rebuilt content: the scroll area's own
        # lazy relayout reliably grows it but not shrinks it, which would leave
        # a stale scroll range after rows leave. (The window itself never
        # resizes — the user's size is authoritative.)
        self._scroll.widget().adjustSize()

    def _toggle_flash(self) -> None:
        """Flip the flash phase and restyle any expired rebuff prompts (#16)."""
        if not self.isVisible():
            return  # refresh() re-applies the phase on reopen
        self._flash_on = not self._flash_on
        for widget in self._row_widgets.values():
            widget.apply_flash(self._flash_on)

    def _group_label(self, group: str) -> str:
        """Header text: the target name, plus its class when the /who
        roster knows it (EQTool's TargetClassString next to the group)."""
        label = group.strip() or group
        tracker = getattr(self._backend, "player_tracker", None)
        if tracker is not None and group != YOU_GROUP:
            player_class = tracker.get_class(group)
            if player_class is not None:
                label = f"{label}  ({player_class.display_name})"
        return label

    def toggle_section(self, key: str) -> None:
        """Fold or unfold one section (#129), and persist the choice.

        Display-only, exactly like the per-section show/hide toggles in
        Settings: nothing is removed from ``TimersService``, so the rows keep
        counting and their expiry announcements still fire.

        A key whose section is not on screen is kept rather than dropped — a
        group folded now is still folded when its rows come back.
        """
        collapsed = list(self._state.collapsed_groups)
        if key in collapsed:
            collapsed.remove(key)
        else:
            collapsed.append(key)
        self._state.collapsed_groups = collapsed
        self.refresh()

    def is_section_collapsed(self, key: str) -> bool:
        return key in self._state.collapsed_groups

    def collapsed_sections(self) -> list[str]:
        """The persisted fold set, in the order the user folded them."""
        return list(self._state.collapsed_groups)

    def current_groups(self) -> list[str]:
        """Group keys in on-screen order (test/debug hook)."""
        out: list[str] = []
        for i in range(self._rows_layout.count()):
            widget = self._rows_layout.itemAt(i).widget()
            if isinstance(widget, QLabel):
                out.append(widget.property("group_key"))
        return out

    def current_row_names(self) -> list[str]:
        """Row names in on-screen order (test/debug hook)."""
        out: list[str] = []
        for i in range(self._rows_layout.count()):
            widget = self._rows_layout.itemAt(i).widget()
            if isinstance(widget, _RowWidget):
                out.append(widget.row_name)
        return out

    def current_header_texts(self) -> list[str]:
        """Header label texts in on-screen order (test/debug hook). Unlike
        ``current_groups`` (group keys), this reflects raid-mode spell headers,
        which have no clearable group key."""
        out: list[str] = []
        for i in range(self._rows_layout.count()):
            widget = self._rows_layout.itemAt(i).widget()
            if isinstance(widget, QLabel):
                out.append(widget.text())
        return out

    def current_row_labels(self) -> list[str]:
        """Displayed row labels in on-screen order (test/debug hook) — the
        target under a raid-mode spell header, else the spell name."""
        out: list[str] = []
        for i in range(self._rows_layout.count()):
            widget = self._rows_layout.itemAt(i).widget()
            if isinstance(widget, _RowWidget):
                out.append(widget._name.text())
        return out

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

    def _resize_frameless(self) -> bool:
        return self._state.frameless

    def apply_skin(self) -> None:
        """Re-style from the active skin (``ui.skins.set_skin``) — live.

        Unlike the theme, a skin switch never needs a restart: the tray's UI
        Skin submenu flips it mid-fight, so every skinned surface has to be
        able to re-dress itself in place.
        """
        self._skin = skins.skin()
        self._font_size = max(6, self._backend.settings.general.font_size)
        self.setStyleSheet(
            skins.overlay_window_style(self._skin, theme.palette(), self._font_size)
            + skins.title_bar_style(self._skin, self._font_size)
        )
        self._container.apply_skin(self._skin, self._backend.settings.general.frame_opacity / 100)
        self._title_bar.apply_skin(self._skin)
        for header in self._headers.values():
            kind = header.property("kind") or skins.KIND_PLAYER
            header.setStyleSheet(skins.header_style(self._skin, self._font_size, kind))
        for widget in self._row_widgets.values():
            widget.apply_skin(self._skin, self._font_size)
        self.refresh()

    def apply_window_state(self) -> None:
        """Re-apply opacity/flags from the (possibly just-edited) state.
        (Copy of OverlayWindowBase.apply_window_state — this window predates
        the base class.)"""
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
        """Write geometry/opacity/shown into settings.windows['spells'] and save."""
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

    # -- drag-to-move / edge-resize ------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._maybe_begin_edge_resize(event.position().toPoint()):
                event.accept()
                return
            # Click-to-dismiss a post-expiry rebuff prompt (#16) before drag.
            if self._dismiss_expired_at(event.position().toPoint()):
                event.accept()
                return
            self._press_section = self._section_at(event.position().toPoint())
            self._press_pos = event.globalPosition().toPoint()
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def _dismiss_expired_at(self, pos: QPoint) -> bool:
        """If ``pos`` is over a flashing post-expiry row, remove it (#16)."""
        row, _ = self._context_target(pos)
        if isinstance(row, SpellRow) and row.expired_at is not None:
            self._backend.timers.remove_row(row)
            self.refresh()
            return True
        return False

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
            section = self._press_section
            self._press_section = None
            if section is not None and not self._dragged_since_press(event):
                self.toggle_section(section)
            self._press_pos = None
            self.persist_state()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def _dragged_since_press(self, event: QMouseEvent) -> bool:
        """Did this press-release pair move the window rather than click it?

        Qt's own start-drag distance is the threshold, so the hand-jitter that
        every click carries does not read as a drag (and a header fold does not
        need a perfectly still mouse)."""
        if self._press_pos is None:
            return False
        moved = event.globalPosition().toPoint() - self._press_pos
        return moved.manhattanLength() > QApplication.startDragDistance()

    def wheelEvent(self, event) -> None:
        # Reaches here only when the scroll area didn't consume it (nothing
        # to scroll): stay inert so wheels never pass through to the game.
        event.accept()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        rect = self.rect()
        self._grip.move(rect.right() - self._grip.width(), rect.bottom() - self._grip.height())
        if self.isVisible():
            # Debounced: persists once the grip-drag (or layout change) settles.
            self._persist_resize.start()

    # -- context menu (manual timer clearing) -----------------------------------
    # Note: with click-through enabled the OS never delivers right-clicks
    # here, same as drag-to-move.

    def _section_at(self, pos: QPoint) -> str | None:
        """The collapse key of the group header under ``pos`` (else None).

        Deliberately not folded into ``_context_target``: that one answers with
        a *clearable* group key, which raid-mode spell headers do not have and
        must not gain — see the header construction in ``refresh``.
        """
        child = self.childAt(pos)
        while child is not None and child is not self:
            if isinstance(child, _RowWidget):
                return None
            if isinstance(child, QLabel):
                key = child.property("section_key")
                if key:
                    return key
            child = child.parentWidget()
        return None

    def _context_target(self, pos: QPoint) -> tuple[Row | None, str | None]:
        """Resolve a click position: a row widget yields (row, its group), a
        group header yields (None, group), empty space yields (None, None)."""
        child = self.childAt(pos)
        while child is not None and child is not self:
            if isinstance(child, _RowWidget):
                row = child.row
                return row, (row.group if row is not None else None)
            if isinstance(child, QLabel):
                group = child.property("group_key")
                if group:
                    return None, group
            child = child.parentWidget()
        return None, None

    def _clear_row(self, row: Row) -> None:
        self._backend.timers.remove_row(row)
        self.refresh()

    def _clear_series(self, series: str) -> None:
        self._backend.timers.remove_series(series)
        self.refresh()

    def _clear_group(self, group: str) -> None:
        self._backend.timers.remove_group(group)
        self.refresh()

    def _clear_all(self) -> None:
        self._backend.timers.clear_all()
        self.refresh()

    def _clear_other_players(self) -> None:
        self._backend.timers.clear_all_other_spells()
        self.refresh()

    def _respell_row(self, row: SpellRow, spell: Spell) -> None:
        """Relabel an ambiguously-guessed row as the candidate the user picked
        (#177).

        The whole timer is re-derived from the new spell — duration against the
        original start, bar colour, gem icon and the post-expiry flash opt-in,
        which is per spell and so must be re-read rather than inherited from
        the name that was guessed. ``TimersService.respell_row`` owns the
        arithmetic so it is testable without a window.
        """
        player = self._backend.player
        self._backend.timers.respell_row(
            row, spell, player.player_class, player.level, self._flash_persist_for(spell.name)
        )
        self.refresh()

    def _flash_persist_for(self, spell_name: str) -> float:
        """Seconds a just-expired row of this spell lingers as a rebuff prompt
        (#16), read from the per-spell allowlist ``_toggle_flash_spell`` edits."""
        sw = self._backend.settings.spellwindow
        if not sw.post_expiry_flash_enabled:
            return 0.0
        key = spell_name.casefold()
        if any(n.casefold() == key for n in sw.post_expiry_flash_spells):
            return float(sw.post_expiry_flash_seconds)
        return 0.0

    def _toggle_flash_spell(self, spell_name: str) -> None:
        """Add/remove a spell from the post-expiry flash allowlist (#16) and
        apply it live to any loaded rows of that spell."""
        sw = self._backend.settings.spellwindow
        key = spell_name.casefold()
        present = any(n.casefold() == key for n in sw.post_expiry_flash_spells)
        if present:
            sw.post_expiry_flash_spells = [
                n for n in sw.post_expiry_flash_spells if n.casefold() != key
            ]
            persist = 0.0
        else:
            sw.post_expiry_flash_spells = [*sw.post_expiry_flash_spells, spell_name]
            sw.post_expiry_flash_enabled = True  # make the per-row toggle self-sufficient
            persist = float(sw.post_expiry_flash_seconds)
        for r in self._backend.timers.snapshot():
            if isinstance(r, SpellRow) and not r.is_cooldown and r.spell.name.casefold() == key:
                r.post_expiry_persist_s = persist
                if persist == 0.0:
                    r.expired_at = None
        if self._on_save is not None:
            self._on_save()
        self.refresh()

    def contextMenuEvent(self, event) -> None:
        self._build_context_menu(event.pos()).exec(event.globalPos())

    def _build_context_menu(self, pos: QPoint) -> QMenu:
        """Assemble the row/group menu for ``pos``.

        Split from ``contextMenuEvent`` so the menu can be inspected without
        entering ``exec``'s modal loop, which never returns under a test.
        """
        row, group = self._context_target(pos)
        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        if row is not None:
            menu.addAction(f"Clear '{row.name}'", lambda r=row: self._clear_row(r))
        series = getattr(row, "window_series", "") if row is not None else ""
        if series:
            # When the mob finally pops, its other candidate windows are
            # answered too; clearing them one row at a time is busywork.
            menu.addAction(
                f"Clear all {row.window_count} windows for '{row.name}'",
                lambda s=series: self._clear_series(s),
            )
        if isinstance(row, SpellRow) and not row.is_cooldown:
            spell_name = row.spell.name
            flash_action = menu.addAction(
                "Flash on expiry", lambda n=spell_name: self._toggle_flash_spell(n)
            )
            flash_action.setCheckable(True)
            flash_action.setChecked(
                any(
                    n.casefold() == spell_name.casefold()
                    for n in self._backend.settings.spellwindow.post_expiry_flash_spells
                )
            )
            if row.alternatives:
                # Several spells share this cast message, so the name on the
                # row is a guess (#177). Offer the ones it passed over rather
                # than leaving the user to clear the row and wonder.
                # Parented explicitly rather than via ``addMenu(title)``: the
                # submenu that call returns is not kept alive by the parent
                # menu, and Qt deletes it out from under the action.
                others = QMenu("Other matches", menu)
                menu.addMenu(others)
                for alternative in row.alternatives:
                    others.addAction(
                        alternative.name,
                        lambda r=row, s=alternative: self._respell_row(r, s),
                    )
        if group is not None:
            label = self._group_label(group)
            menu.addAction(f"Clear group '{label}'", lambda g=group: self._clear_group(g))
        if menu.actions():
            menu.addSeparator()
        menu.addAction("Clear other players' timers", self._clear_other_players)
        menu.addAction("Clear all timers", self._clear_all)
        return menu
