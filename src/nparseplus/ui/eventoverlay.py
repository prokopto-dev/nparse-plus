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

import logging
import weakref
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from PySide6.QtCore import QPoint, QPropertyAnimation, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLayout,
    QProgressBar,
    QSizeGrip,
    QSizePolicy,
    QStyle,
    QStyleOption,
    QVBoxLayout,
    QWidget,
)

from nparseplus.config.settings import (
    MIN_REGION_HEIGHT,
    MIN_REGION_WIDTH,
    OverlayRegion,
    WindowState,
)
from nparseplus.core.events import (
    CompleteHealCadenceEvent,
    CompleteHealEvent,
    OverlayEvent,
    TimerBarEvent,
)
from nparseplus.core.timers import seconds_left, snap_to_second
from nparseplus.ui import chrome, skins
from nparseplus.ui.overlaybase import (
    RESIZE_MARGIN,
    cursor_for_edges,
    edge_at,
    start_second_aligned,
)
from nparseplus.ui.skinwidgets import paint_hairline, qcolor, set_caps

logger = logging.getLogger(__name__)

DEFAULT_CLEAR_AFTER_S = 4.0
BAR_TICK_MS = 200
BAR_WIDTH = 320
LANES_WIDTH = 520
DEFAULT_TEXT_COLOR = "red"
DEFAULT_BAR_COLOR = "steelblue"
DEFAULT_TEXT_SIZE = 32
# An alert that does not fit its region is shrunk to fit rather than clipped.
# A raid warning cut off mid-sentence is worse than a small one: the whole
# point is that it is readable at a glance without looking away from the game.
MIN_ALERT_TEXT_SIZE = 12
# What a headline may occupy of the overlay's height before it shrinks. Only
# the default: an Alerts region resized in position mode says so itself.
ALERT_HEIGHT_FRACTION = 0.42
# Floor for the DEFAULT budget above (a tiny overlay window still gets a
# line). A configured Alerts region is never floored against this — it is
# exact, and ``_min_region_height`` keeps it from being dragged below it.
MIN_ALERT_BUDGET = 40
DEFAULT_FONT_SIZE = 12
DEFAULT_EMPHASIS = "pulse"
# Alert pulse cadence. Slow on purpose: fast enough to catch the eye at the
# edge of vision, slow enough that reading the word is never a chase.
PULSE_INTERVAL_MS = 700
PULSE_DIM = 0.42

# Separators an alert's "kicker — HEADLINE" may be split on. Only the FIRST
# one in the text is ever considered, and only when what precedes it reads
# like a name (see ``looks_like_a_kicker``).
ALERT_SEPARATORS = (" — ", " -- ", " - ", ": ")
# What "reads like a name" means, in the three checks a user can apply to
# their own trigger text by eye. These are deliberately tight: the kicker is
# a label naming WHO, and every separator here also occurs mid-sentence in
# ordinary prose, so anything longer than a label is the alert itself (#102).
KICKER_MAX_CHARS = 28
KICKER_MAX_WORDS = 4
# Stand-in text for measuring how tall a POPULATED kicker is (see
# ``_measure_kicker_height``). Any non-empty string does; the glyphs do not
# matter, only that there are some.
KICKER_HEIGHT_PROBE = "Ag"
# Punctuation that marks structured text rather than a name. A mob-info dump
# opens with "<Mob> [Slowable, baneable] - ..." — brackets are the tell.
KICKER_FORBIDDEN_CHARS = frozenset("[]{}()<>|/*")

# -- alert crawl ---------------------------------------------------------------
# A headline that does not fit even at MIN_ALERT_TEXT_SIZE is scrolled past a
# fixed window rather than cut off. The crawl is a plain ``move()`` of the
# label inside a clipping parent — NOT a second QGraphicsEffect, which the
# label cannot have (it already carries the shadow/glow).
SCROLL_TICK_MS = 33
# Fractions of the alert's lifetime parked at the top before the crawl starts
# and at the bottom once it arrives, so the first and last lines are readable
# standing still rather than swinging past.
SCROLL_HEAD_HOLD = 0.18
SCROLL_TAIL_HOLD = 0.12
# Readability ceiling, in text lines per second. Above roughly this the text
# is moving faster than it can be read and the crawl is pointless.
MAX_SCROLL_LINES_PER_S = 2.5
# Room left around a headline that DOES fit, so the drop shadow/glow can spill
# past the glyphs exactly as it did before the clipping parent existed.
ALERT_SHADOW_PAD = 30

# Positioning-mode chrome.
EDIT_HINT_HEIGHT = 56
# The empty ``Qt.Edge`` flag — a module singleton because ruff (B008)
# rightly refuses a constructor call in an argument default.
NO_EDGES = Qt.Edge(0)
# The empty alignment flag: what the Alerts host is added to the stacked
# layout with, i.e. "take the whole cell" rather than being centered in it.
NO_ALIGNMENT = Qt.AlignmentFlag(0)
# Smallest a region may be dragged to in position mode: MIN_REGION_WIDTH /
# MIN_REGION_HEIGHT, imported above from the model that persists them. They
# live there because the settings layer is the one that has to repair a
# hand-edited size and may not import Qt to learn the number (#107); one
# definition means the drag floor and the load-time bound cannot drift apart.
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
# A chip is one lane cell wide and must stay legible at that size, so its type
# is pinned rather than following general.font_size like everything else.
CH_CHIP_FONT_SIZE = 11


def first_alert_separator(text: str) -> tuple[int, str] | None:
    """The earliest ``ALERT_SEPARATORS`` occurrence in ``text``, or None.

    Earliest *in the text*, not first in the tuple: "FTE: Someone - and more"
    has to kick off "FTE", and scanning the tuple in order would have taken
    the later ` - ` instead. Ties go to the longer separator.
    """
    best: tuple[int, str] | None = None
    for separator in ALERT_SEPARATORS:
        at = text.find(separator)
        if at < 0:
            continue
        if best is None or (at, -len(separator)) < (best[0], -len(best[1])):
            best = (at, separator)
    return best


def looks_like_a_kicker(head: str) -> bool:
    """Whether ``head`` reads like a NAME rather than the start of a clause.

    The kicker is a label naming who the alert is about, so it is short, a
    few words at most, and free of the punctuation that marks structured
    text. Everything else is the alert itself and must not be shrunk into
    gold caps — which is exactly what a mob-info dump used to get (#102):
    ``"<Dozekar The Cursed> [Slowable, baneable] - [CH Unslowed: 2s…"`` split
    on its first ` - ` and lost its opening 41 characters to the kicker.
    """
    head = head.strip()
    if not head or len(head) > KICKER_MAX_CHARS:
        return False
    if len(head.split()) > KICKER_MAX_WORDS:
        return False
    return not KICKER_FORBIDDEN_CHARS.intersection(head)


def split_alert_text(text: str) -> tuple[str, str]:
    """Split an alert into its kicker caps and its headline.

    Alerts arrive as one string ("Gorenaire — ENRAGED", "FTE: Someone"), and
    the design gives the two halves different jobs: a small tracked-out cap
    naming *who*, and the big word saying *what*. That only makes sense when
    the text is actually shaped that way, so the rule is narrow and stated in
    one place (docs/windows/event-overlay.md says the same in prose):

        the FIRST separator in the text, and only if everything before it
        passes ``looks_like_a_kicker`` and something is left after it.

    A text that fails any part of that is one headline, at one size. Only the
    first separator is ever tried — falling through to the next one would go
    looking for a split the author did not write.

    Splitting is presentational only — ``current_text`` and the reset match
    still use the whole string. Returns ``("", text)`` when there is no split.
    """
    found = first_alert_separator(text)
    if found is None:
        return "", text
    at, separator = found
    head, tail = text[:at], text[at + len(separator) :]
    if not tail.strip() or not looks_like_a_kicker(head):
        return "", text
    return head.strip(), tail.strip()


def alert_scroll_speed(overflow_px: int, lifetime_ms: int, line_height_px: int) -> float:
    """Pixels per second for the headline crawl. Pure; 0 means "do not crawl".

    Two requirements pull against each other and both are honored in the
    order the reporter asked for them (#102):

    *Fast enough to finish.* The whole overflow is walked inside the alert's
    own lifetime (``general.overlay_text_seconds``, live via
    ``apply_timings``) less the dwells at each end — never a hardcoded rate,
    so raising the alert duration slows the crawl rather than leaving it to
    finish early and sit there.

    *Slow enough to read.* Never more than ``MAX_SCROLL_LINES_PER_S`` lines a
    second. When an alert is so long that finishing would need more than
    that, the ceiling wins and the tail is what the reader does not reach —
    the fix for that is a longer alert duration (or a bigger Alerts region),
    not a crawl nobody can follow.
    """
    if overflow_px <= 0 or lifetime_ms <= 0:
        return 0.0
    travel_s = max(0.1, (lifetime_ms / 1000.0) * (1.0 - SCROLL_HEAD_HOLD - SCROLL_TAIL_HOLD))
    ceiling = MAX_SCROLL_LINES_PER_S * max(1, line_height_px)
    return min(overflow_px / travel_s, ceiling)


def region_origin(
    anchor: str, dx: int, dy: int, host_w: int, host_h: int, win_w: int, win_h: int
) -> tuple[int, int]:
    """Where a region host's top-left lands — THE placement rule, one copy.

    Horizontally a region is centered on the window and nudged by ``dx``;
    vertically it hangs off its anchor line. ``region_offsets`` is the exact
    inverse, which is what makes an edge-drag resize expressible as "move
    this rect, then ask what offsets put it there".
    """
    x = win_w // 2 + dx - host_w // 2
    if anchor == "top":
        y = REGION_MARGIN_TOP + dy
    elif anchor == "center":
        y = win_h // 2 + dy
    else:  # bottom
        y = win_h - REGION_MARGIN_BOTTOM - host_h + dy
    return x, y


def region_offsets(
    anchor: str, x: int, y: int, host_w: int, host_h: int, win_w: int, win_h: int
) -> tuple[int, int]:
    """The ``(dx, dy)`` that puts a host of this size with its top-left at
    ``(x, y)`` — the inverse of :func:`region_origin`."""
    dx = x - win_w // 2 + host_w // 2
    if anchor == "top":
        dy = y - REGION_MARGIN_TOP
    elif anchor == "center":
        dy = y - win_h // 2
    else:  # bottom
        dy = y - (win_h - REGION_MARGIN_BOTTOM - host_h)
    return dx, dy


def region_resize_margin(width: int, height: int) -> int:
    """The grab band along a region's edges, in px.

    ``RESIZE_MARGIN`` is written for whole windows. A region can be 30 px
    tall (an empty CH-lane strip), and two 7 px bands would leave almost
    nothing to drag it *by*, so the band never eats more than a third of the
    smaller dimension.
    """
    return max(1, min(RESIZE_MARGIN, min(width, height) // 3))


def resize_rect(rect: QRect, edges: Qt.Edge, dx: int, dy: int, min_w: int, min_h: int) -> QRect:
    """Apply an edge/corner drag to ``rect``, holding the opposite edge.

    Pure so the region-resize math is testable without a window; ``edges``
    comes from ``overlaybase.edge_at``, the same hit-test the frameless
    windows have used since 1.8.
    """
    left, top = rect.left(), rect.top()
    width, height = rect.width(), rect.height()
    if edges & Qt.Edge.LeftEdge:
        step = min(dx, width - min_w)
        left += step
        width -= step
    elif edges & Qt.Edge.RightEdge:
        width = max(min_w, width + dx)
    if edges & Qt.Edge.TopEdge:
        step = min(dy, height - min_h)
        top += step
        height -= step
    elif edges & Qt.Edge.BottomEdge:
        height = max(min_h, height + dy)
    return QRect(left, top, width, height)


def fit_text_size(
    measure: Callable[[int], QRect],
    max_height: int,
    max_size: int,
    min_size: int = MIN_ALERT_TEXT_SIZE,
) -> int:
    """The largest size in ``[min_size, max_size]`` whose wrapped text fits.

    ``measure(size)`` returns the rect the text would occupy at that size;
    injecting it keeps the search itself Qt-free and testable. Steps down one
    px at a time rather than binary-searching: the range is ~30 wide, it runs
    once per alert, and monotonicity is an assumption worth not making about
    a font's line-breaking.
    """
    if max_size <= min_size:
        return max_size
    for size in range(max_size, min_size - 1, -1):
        if measure(size).height() <= max_height:
            return size
    return min_size


def qss_id(key: str) -> str:
    """``key`` as something a Qt ``#id`` selector can name.

    A region's chrome is applied through ``#{objectName}``, and a stylesheet
    whose selector is malformed is discarded WHOLE by Qt with only a runtime
    warning — the window then renders undressed while every string-level test
    passes (``ui/chrome.py`` says the same about a stray brace). Plugin region
    keys are namespaced ``plugin.<id>.<key>``, so the dots have to go.
    """
    return "".join(c if c.isalnum() or c == "_" else "_" for c in key) or "region"


def weak_hook[T](method: Callable[[], T], gone: T) -> Callable[[], T]:
    """A zero-arg call of ``method`` that does not keep its object alive.

    The overlay owns its own region records, so a record holding a BOUND
    METHOD of the overlay puts the window itself in a Python reference cycle
    — and that takes its destruction away from refcounting and hands it to
    the cyclic collector, which runs whenever it likes. A QWidget freed there
    rather than by Qt is a use-after-free the next repaint walks straight
    into; it segfaulted the suite from inside ``paintEvent``, with the crash
    landing in whichever overlay happened to be painting.

    Only the BUILT-IN records need this, and only because their hooks are the
    overlay's own methods. A contributed region's hooks close over the plugin
    that supplied them, not over the overlay, so they are stored as they are.

    ``gone`` is what the call answers once the overlay has been collected —
    unobservable in practice, since every caller of these hooks is the
    overlay itself.
    """
    ref = weakref.WeakMethod(method)

    def call() -> T:
        target = ref()
        return gone if target is None else target()

    return call


@dataclass
class RegionRecord:
    """Everything the overlay knows about ONE region — the single record.

    The four regions used to be four parallel literals (hosts, titles,
    default placements, default widths) plus two more shaped like them
    (``_show_preview``'s injection and ``_clear_preview``'s layout list), and
    two of those raised ``KeyError`` on a key they had not been told about —
    into the Qt event loop of a translucent, always-on-top window over a
    running game (#154). One record is what makes adding a region a single
    call instead of a five-site edit, and what makes ``add_region`` /
    ``remove_region`` genuinely live rather than merely extensible.

    ``host`` is the widget the region places and ``layout`` the layout inside
    it that position-mode sample content is inserted into. ``default`` is the
    placement the region takes until the user drags it, and its ``anchor``
    doubles as the region's band in the legacy stacked layout. Sizes are
    CALLABLES, never precomputed numbers: two of the built-ins derive their
    width from a live size hint and one from the window's own width.

    Deliberately nothing input-related, and there never will be: a region is
    a PAINT SURFACE. ``_apply_locked_flags`` sets
    ``Qt.WindowType.WindowTransparentForInput`` on the whole window and Qt has
    no per-child exemption, so outside position mode a region gets no mouse,
    no keyboard, no hover and no wheel — and inside it, every click belongs to
    the repositioning. Something that needs clicks is a WINDOW, not a region
    (owner decision on #155). ``has_content`` is the one behaviour hook, and
    it exists because ``_update_visibility`` has to ask whether the overlay is
    worth showing at all.
    """

    key: str
    title: str
    host: QWidget
    layout: QLayout | None
    default: OverlayRegion
    default_width: Callable[[], int]
    #: Whether this region has anything to show. ``_update_visibility`` ORs
    #: these: without one per region a fifth region could never keep the
    #: overlay on screen by itself.
    has_content: Callable[[], bool]
    #: Builds this region's sample content, already inserted into ``layout``,
    #: and hands back the widgets so they can be taken out again.
    preview: Callable[[], list[QWidget]] | None = None
    #: Run once this region's sample content has been removed.
    on_preview_cleared: Callable[[], None] | None = None
    #: The floor an edge drag may shrink this region to; None = the structural
    #: ``MIN_REGION_HEIGHT``.
    min_height: Callable[[], int] | None = None
    #: How the host is added to the legacy stacked layout.
    stack_alignment: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignHCenter
    #: A built-in cannot be removed, and its key cannot be taken over.
    builtin: bool = False
    # -- live bookkeeping, owned by the overlay -------------------------------
    #: The dashed title chip drawn over the host in position mode.
    chip: QLabel | None = None
    #: The host's own stylesheet, put back when the dashed chrome comes off.
    host_style: str = ""
    #: This region's position-mode sample widgets.
    preview_widgets: list[QWidget] = field(default_factory=list)


class _AlertViewport(QWidget):
    """The slot the headline is read through.

    A headline that still does not fit at ``MIN_ALERT_TEXT_SIZE`` used to
    simply clip — the shrink-to-fit search bottomed out and the rest of the
    sentence was gone (#102). Instead the label keeps its whole wrapped
    height and this parent shows a slice of it: Qt clips a child widget to
    its parent's rect, so the crawl is a ``move()`` and needs no painting
    trick and no second QGraphicsEffect.

    When the headline DOES fit — which is the normal case, since the size
    search runs first — the viewport takes the label's full height plus up to
    ``ALERT_SHADOW_PAD`` of the budget's leftover slack, so nothing clips and
    the shadow spills as it did before this widget existed.
    """

    def __init__(self, label: QLabel, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setObjectName("EventOverlayAlertViewport")
        # Fill the alert host's width. Stated as a policy rather than left to
        # the layout's default because the whole alert once vanished for want
        # of it: see ``sizeHint`` (#107).
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._label = label
        label.setParent(self)
        self._natural_height = 0
        self._offset = 0.0
        self._pad = 0

    def sizeHint(self) -> QSize:
        """A real width, so this can never be laid out zero pixels wide.

        Qt answers ``QSize(-1, -1)`` for a widget with no layout of its own,
        which ``QWidgetItem`` resolves to width 0 — and a layout item carrying
        a horizontal alignment flag is clamped to its hint width instead of
        being stretched to the cell. Together those laid the entire alert out
        at zero width: the headline was there, styled, correctly sized, and
        one pixel wide inside a parent that clipped it (#107). The alignment
        flag is off this item now (``apply_skin``), and this hint is the
        second lock — a width nothing can silently collapse.

        Height is not this hint's business: ``apply_text_layout`` and
        ``collapse`` set it outright with ``setFixedHeight``.
        """
        return QSize(MIN_REGION_WIDTH, self.height())

    def apply_text_layout(self, natural_height: int, budget: int) -> int:
        """Size to a headline ``natural_height`` px tall inside ``budget``.

        Returns the overflow in px — 0 when it all fits, which is also the
        signal that no crawl is needed.
        """
        budget = max(1, budget)
        self._natural_height = max(1, natural_height)
        overflow = max(0, self._natural_height - budget)
        if overflow:
            self._pad = 0
            self.setFixedHeight(budget)
        else:
            # The pad is opportunistic: only ever spent out of the slack the
            # budget already has. Taking it unconditionally would push the
            # viewport past a configured Alerts region and reintroduce the
            # very clipping this widget exists to stop.
            self._pad = max(0, min(ALERT_SHADOW_PAD, (budget - self._natural_height) // 2))
            self.setFixedHeight(self._natural_height + 2 * self._pad)
            self._offset = 0.0
        self._place_label()
        return overflow

    def collapse(self) -> None:
        """No headline: take no room at all, so a cleared alert leaves none."""
        self._natural_height = 0
        self._offset = 0.0
        self._pad = 0
        self.setFixedHeight(0)
        self._place_label()

    def set_offset(self, px: float) -> None:
        self._offset = max(0.0, px)
        self._place_label()

    def reset(self) -> None:
        self._offset = 0.0
        self._place_label()

    def _place_label(self) -> None:
        # The label spans the viewport EXACTLY — that is what centers the
        # headline (the label's own AlignHCenter) and what makes the width the
        # text was measured against the width it is drawn at. Deliberately not
        # ``max(1, self.width())``: flooring it at one pixel is what turned a
        # zero-width viewport into a legal-looking label that drew nothing, so
        # every height-and-text assertion in the suite still passed (#107). A
        # zero here now shows as a zero.
        self._label.setGeometry(
            0, self._pad - round(self._offset), self.width(), self._natural_height
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_label()


class _Hairline(QWidget):
    """The rule under an alert — solid in the middle, gone at both ends."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._color = ""
        self.setFixedHeight(1)

    def sizeHint(self) -> QSize:
        """The height ``_alert_chrome_height`` charges the region for.

        A widget with no layout of its own answers ``QSize(-1, -1)``, and that
        -1 was being *subtracted* from the alert's height budget — crediting
        the headline with two pixels the region does not have, on the one
        function whose whole job is that the two agree (#107).
        """
        return QSize(self.minimumWidth(), self.minimumHeight())

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
        self.apply_skin(skins.skin())

    def apply_skin(self, skin: skins.Skin) -> None:
        """Re-dress this lane. Called for live lanes on a skin change, so a
        chain already on screen mid-raid changes with everything else."""
        self._skin = skin
        self.setStyleSheet(
            f"#ChChainLane {{ background-color: {skin.lane_bg};"
            f" border: 1px solid {skin.lane_border}; border-radius: 3px; }}"
        )
        for chip in self.chips:
            self._style_chip(chip)

    def _style_chip(self, chip: QLabel) -> None:
        skin = self._skin
        chip.setStyleSheet(
            skins.typography_style(CH_CHIP_FONT_SIZE, skins.SMALL_DISPLAY, color=chrome.PILL_TEXT)
            + f" background-color: {chrome.GOOD};"
            f" border: 1px solid {skin.glass_border}; border-radius: 3px;"
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
        self._style_chip(chip)
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
        font_size: int = DEFAULT_FONT_SIZE,
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
        self._font_size = max(6, int(font_size))
        self._text_size = max(10, int(text_size))
        self._emphasis = emphasis if emphasis in ("plain", "pulse", "glow") else DEFAULT_EMPHASIS
        self._skin = skins.skin()
        #: The whole un-split alert string — what ``current_text`` reports and
        #: what a reset event must match (the labels show it in two pieces).
        self._alert_text = ""
        self._edit_mode = False
        self._drag_offset: QPoint | None = None
        self.setObjectName("EventOverlayWindow")
        # Child labels with role-specific sizes/weights inherit the same
        # bundled family too; this covers utility lines, CH lanes and timer
        # bars without turning their data text into tracked display caps.
        self.setStyleSheet(f'font-family: "{skins.NOTO_SANS}";')
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
        # Positioning-mode sample widgets live on their own region's record
        # (``_preview_widgets`` flattens them) — never registered in
        # ``_bars``/``_chain_lanes`` and never written to ``_center_text``.
        self._preview_shown = False

        self._alert_kicker = QLabel("", self)
        self._alert_kicker.setObjectName("EventOverlayKicker")
        # Trigger display text is user data, never markup: Qt's AutoText
        # heuristic renders anything that opens with a known HTML tag name as
        # rich text, which would swallow "<b>" out of an alert (and eat an
        # "&lt;" as an escape). Same reasoning as settingswindow.py's
        # plugin-name label. Applies to every label that shows event text.
        self._alert_kicker.setTextFormat(Qt.TextFormat.PlainText)
        set_caps(self._alert_kicker)
        self._alert_kicker.hide()
        # How tall the kicker is once it has text — the number the Alerts
        # region's floor is charged for. Re-measured by every apply_skin.
        self._kicker_worst_height = 0
        self._center_text = QLabel("", self)
        self._center_text.setObjectName("EventOverlayText")
        self._center_text.setTextFormat(Qt.TextFormat.PlainText)
        self._center_text.setWordWrap(True)
        # The headline lives inside a clipping viewport so an alert too long
        # for its region can crawl instead of being cut off (#102).
        self._alert_viewport = _AlertViewport(self._center_text, self)
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

        # Headline crawl (#102): only runs while an alert overflows its region.
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setInterval(SCROLL_TICK_MS)
        self._scroll_timer.timeout.connect(self._advance_scroll)
        self._scroll_speed = 0.0  # px/s, from alert_scroll_speed
        self._scroll_offset = 0.0
        self._scroll_travel = 0  # px of overflow still to walk
        self._scroll_hold_ticks = 0

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
        self._alert_layout.addWidget(self._alert_viewport)
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
        set_caps(self._utility_header)
        self._utility_header.hide()
        self._utility_layout.addWidget(self._utility_header, 0, Qt.AlignmentFlag.AlignHCenter)
        self._utility_host = QWidget(self)
        self._utility_host.setObjectName("OverlayUtilityHost")
        self._utility_host.setLayout(self._utility_layout)
        self._utility_lines: dict[str, QLabel] = {}
        self._utility_timers: dict[str, QTimer] = {}

        self._main_layout = QVBoxLayout()
        self._main_layout.setContentsMargins(20, 40, 20, 60)
        self.setLayout(self._main_layout)

        # THE region registry, in stacked-layout order. Every region-aware
        # path re-reads this dict — which is what makes add_region/
        # remove_region live rather than merely extensible (#154) — and the
        # small dashed title chips shown over each host while editing are
        # minted with the record that owns them.
        self._regions: dict[str, RegionRecord] = {}
        for record in self._builtin_regions():
            self._register_region(record)
        self._rebuild_stacked_layout()

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
            "Event overlay — drag to move, use the corner grip to resize.\n"
            "Drag a dashed region to move it, or its edge to resize it. "
            "Double-click to lock in place.",
            self,
        )
        self._edit_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._edit_hint.setObjectName("OverlayEditHint")
        self._edit_hint.hide()
        self._size_grip = QSizeGrip(self)
        self._size_grip.setFixedSize(24, 24)
        self._size_grip.hide()

        # Region-drag bookkeeping (populated while dragging a single region).
        self._drag_region: str | None = None
        self._region_drag_start: QPoint | None = None
        self._region_drag_base = (0, 0)
        # Empty edges = the drag is a move; otherwise it is a resize of these
        # edges, measured from the rect the region had when it was grabbed.
        self._region_resize_edges: Qt.Edge = NO_EDGES
        self._region_resize_base = QRect()

        # If regions were persisted, switch out of the stacked QVBoxLayout now.
        if self._region_mode():
            self._activate_region_layout()

        # Also where a loaded region too short to hold a headline is repaired:
        # apply_skin clamps it, and it has to be the skin that does so, since
        # the floor is not knowable until the kicker and rule know their font.
        self.apply_skin()
        self.hide()

    # -- timings ------------------------------------------------------------------

    def apply_timings(
        self,
        clear_after_s: float | None = None,
        ch_lane_retention_s: float | None = None,
    ) -> None:
        """Re-time the on-game surface — live (#67).

        How long an alert stays up and how long an idle CH lane lingers were
        constructor-only, so the two settings that most obviously want a
        preview needed a restart. Both land in plain attributes read at use
        time, so an assignment is the whole fix; the alert's clear timer is
        the one piece of state that has to be told separately.

        Deliberately NOT part of ``apply_skin``: these are behavior, not
        appearance, and that path doubles as the skin picker's live preview —
        clicking a card must not restart the alert timers.
        """
        if clear_after_s is not None:
            self._clear_after_ms = max(1000, int(clear_after_s * 1000))
            # Qt restarts a running timer when its interval changes, so an
            # alert already on screen is re-timed rather than left on the old
            # clock. The utility lines read the attribute at start().
            self._clear_timer.setInterval(self._clear_after_ms)
            # The crawl is paced off that same lifetime, so a crawl in flight
            # is re-derived from the new one rather than finishing on the old
            # clock (or never finishing on a shortened one).
            if self._alert_text:
                self._restyle_alert()
        if ch_lane_retention_s is not None:
            self._ch_lane_retention_s = max(0.0, ch_lane_retention_s)

    # -- skin --------------------------------------------------------------------

    def apply_skin(
        self,
        font_size: int | None = None,
        text_size: int | None = None,
        emphasis: str | None = None,
        shadow: bool | None = None,
    ) -> None:
        """Re-dress the on-game surface from the active skin — live.

        The overlay is the one surface that sits directly on EverQuest, so it
        follows the skin like every other window but never follows the *theme*
        (a pale panel over the game is a flashbang; ``ui/theme.py`` says the
        same). Optional arguments let Settings push changed base typography,
        headline size, emphasis or shadow through in the same call.
        """
        self._skin = skins.skin()
        if font_size is not None:
            self._font_size = max(6, int(font_size))
        if text_size is not None:
            self._text_size = max(10, int(text_size))
        if emphasis is not None and emphasis in ("plain", "pulse", "glow"):
            self._emphasis = emphasis
        if shadow is not None and shadow != self._text_shadow:
            self._text_shadow = shadow
            self._center_text.setGraphicsEffect(None)
            self._apply_text_shadow(self._center_text)
        skin = self._skin
        align = Qt.AlignmentFlag.AlignHCenter
        for widget in (self._alert_kicker, self._center_text):
            widget.setAlignment(align | Qt.AlignmentFlag.AlignVCenter)
        # The kicker and the rule are centered as layout items; the headline is
        # centered INSIDE its viewport, by the label's own AlignHCenter above.
        # The viewport itself must take the full host width and so must carry
        # NO horizontal alignment flag: Qt clamps an aligned item to its size
        # hint, and this one is a bare QWidget whose hint width was 0, which
        # laid every alert out zero pixels wide (#107). Pre-#103 this loop
        # named the label — a QLabel with a real hint — which is why passing a
        # widget swap through it broke silently. The alert panel still stays
        # centered in its region under every skin (the deliberate exception).
        for widget in (self._alert_kicker, self._alert_rule):
            self._alert_layout.setAlignment(widget, align)
        kicker_role = skins.TypographyRole(
            skin.alert_kicker_scale, "bold", skin.alert_kicker_tracking
        )
        self._alert_kicker.setStyleSheet(
            skins.typography_style(self._font_size, kicker_role, color=skin.alert_kicker_color)
            + " background: transparent;"
        )
        # After its stylesheet, before anything asks for the floor.
        self._kicker_worst_height = self._measure_kicker_height()
        display_style = skins.typography_style(
            self._font_size, skins.SMALL_DISPLAY, color=skin.overlay_chip_text
        )
        self._utility_header.setStyleSheet(
            display_style + f" background-color: {skin.overlay_chip_fill};"
            " padding: 1px 6px; border-radius: 3px;"
        )
        for record in self._regions.values():
            if record.chip is not None:
                self._style_region_chip(record.chip)
        self._edit_hint.setStyleSheet(
            skins.typography_style(
                self._font_size, skins.TypographyRole(1.3, "bold"), color=skin.overlay_chip_text
            )
            + f" background-color: {skins.rgba(skins.base_color((skin.overlay_chip_fill,)), 0.55)};"
        )
        for lane in self._chain_lanes.values():
            lane.apply_skin(skin)
            if lane.row is not None:
                for label in lane.row.findChildren(QLabel):
                    if label.objectName() == "ChLaneName":
                        label.setStyleSheet(
                            skins.typography_style(
                                self._font_size, skins.SMALL_DISPLAY, color=skin.name_color
                            )
                        )
        for label in self._utility_lines.values():
            label.setStyleSheet(self._utility_line_style(label.property("line_color")))
        self._alert_rule.apply_skin(skin)
        # The floor is font-relative — it is the kicker's height plus the
        # rule's — so raising the base font in Settings can put a region that
        # was legal when it was drawn back under it, live and silently. Only
        # ever grows, and only to the size the drag would now enforce anyway.
        if self._region_mode():
            self._clamp_alert_region()
        self._restyle_alert()
        for entry in self._bars.values():
            self._style_bar(entry.widget)
        for widget in self._preview_widgets:
            if isinstance(widget, QProgressBar):
                self._style_bar(widget)
            elif widget.objectName() == "EventOverlayPreviewAlert":
                self._style_preview_alert(widget)
        self._sync_pulse()

    def _utility_line_style(self, color: str) -> str:
        """One line in the Utility section.

        ``color`` is the caller's: for a live line it comes from the trigger's
        own config through ``resolve_color`` and is the user's choice, so it is
        deliberately NOT a skin token. Only the size and weight are ours.
        """
        role = skins.TypographyRole(1.0, "bold")
        return (
            skins.typography_style(max(12, round(self._text_size * 0.62)), role, color=color)
            + " background: transparent;"
        )

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
        # Hover moves only arrive with tracking on — that is what feeds the
        # region resize cursor. Off again when locked, since the window goes
        # transparent for input anyway.
        self.setMouseTracking(editing)
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
            self.unsetCursor()
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
        # A narrower window rewraps the headline, which changes both the size
        # it fits at and whether it needs to crawl at all.
        if self._alert_text:
            self._restyle_alert()
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

    def _region_edges_at(self, key: str, pos: QPoint) -> Qt.Edge:
        """Which edges of region ``key`` the (self-local) ``pos`` is grabbing.

        Reuses ``overlaybase.edge_at`` — the same margin-band hit test the
        frameless windows have used since 1.8 — translated into the host's
        own coordinates.
        """
        rect = self._region_hosts()[key].geometry()
        return edge_at(
            QPoint(pos.x() - rect.x(), pos.y() - rect.y()),
            QRect(QPoint(0, 0), rect.size()),
            region_resize_margin(rect.width(), rect.height()),
        )

    def mousePressEvent(self, event) -> None:
        if self._edit_mode and event.button() == Qt.MouseButton.LeftButton:
            # Hit-test the regions first; a hit near an edge resizes that
            # region, a hit anywhere else inside it drags that region alone.
            if self._state is not None:
                pos = event.position().toPoint()
                key = self._region_at(pos)
                if key is not None:
                    self._begin_region_edit(
                        key, event.globalPosition().toPoint(), self._region_edges_at(key, pos)
                    )
                    event.accept()
                    return
            # Miss: fall back to moving the whole window.
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._edit_mode and self._drag_region is not None:
            # The region may have been retired since the press: position mode
            # is the one time the overlay is NOT transparent for input, so it
            # is also the one time a drag can be in flight while a plugin
            # takes its region away (#154). Read it, never index it.
            region = self._dragged_region()
            if region is None:
                self._drag_region = None
                super().mouseMoveEvent(event)
                return
            delta = event.globalPosition().toPoint() - self._region_drag_start
            if self._region_resize_edges:
                self._apply_region_resize(delta)
            else:
                region.dx = self._region_drag_base[0] + delta.x()
                region.dy = self._region_drag_base[1] + delta.y()
            self._layout_regions()
            # AFTER the hosts have their new geometry. The Alerts region is a
            # text budget, so a live headline refits as the box is dragged —
            # but measuring before this line reads the box the region just
            # left, which shrinks into a clip and grows into a stale crawl.
            if self._drag_region == "alert" and self._alert_text:
                self._restyle_alert()
            self._position_region_chrome()
            event.accept()
        elif self._edit_mode and self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
        elif self._edit_mode and not event.buttons():
            self._update_region_cursor(event.position().toPoint())
            super().mouseMoveEvent(event)
        else:
            super().mouseMoveEvent(event)

    def _update_region_cursor(self, pos: QPoint) -> None:
        """Show the matching resize cursor while hovering a region's edge."""
        key = self._region_at(pos)
        cursor = cursor_for_edges(self._region_edges_at(key, pos)) if key is not None else None
        if cursor is None:
            self.unsetCursor()
        else:
            self.setCursor(cursor)

    def _min_region_height(self, key: str) -> int:
        """The floor an edge drag may shrink region ``key`` to.

        Alerts gets its own, larger one. Its height is a text budget, and a
        floor shared with the bar and lane strips let it be dragged to a box
        that could not hold one line of the smallest headline plus the kicker
        and rule — the budget then had to overrun the region to stay
        readable. Derived from the same two numbers the budget is, so the two
        cannot drift apart.

        Against WORST-CASE chrome, which is the whole guarantee: this is
        evaluated in position mode, where no alert is live and the kicker is
        hidden, so charging live chrome floored the region at a number the
        very next ``"Gorenaire — ENRAGED"`` overran (#107).

        Which region wants its own floor is the record's business, so a
        region contributed at runtime can have one — and a key that is not
        registered at all still answers the structural floor.
        """
        record = self._regions.get(key)
        if record is None or record.min_height is None:
            return MIN_REGION_HEIGHT
        return max(MIN_REGION_HEIGHT, record.min_height())

    def _min_alert_height(self) -> int:
        """The Alerts region's own floor — see ``_min_region_height``."""
        return MIN_ALERT_BUDGET + self._alert_chrome_height(worst_case=True)

    def _clamp_alert_region(self) -> None:
        """Bring a loaded Alerts region up to the floor the drag enforces.

        The drag floor cannot police a settings.json — a file written before
        that floor existed, or edited by hand, or saved at a smaller font than
        the one now configured, can all name a height with no room for a
        headline. ``_alert_budget_height`` honors the drawn box EXACTLY (and
        should: clamping the BUDGET up made the headline taller than the
        region, which is the clipping that path exists to stop), so the
        terminal form of that was a 1px viewport and an alert nobody saw
        (#107). The floor therefore lands on the region, once, here.

        Widening rather than shrinking is the safe direction: the region only
        grows toward the size the user could have dragged it to anyway.
        ``config.settings`` has already repaired anything structurally
        impossible; this is the part of the floor that needs a font to know.
        """
        regions = self._state.overlay_regions if self._state is not None else None
        region = regions.get("alert") if regions else None
        if region is None or not region.height:
            return  # None = content-driven, which is never too small
        floor = self._min_region_height("alert")
        if region.height >= floor:
            return
        region.height = floor
        self._layout_regions()

    def _apply_region_resize(self, delta: QPoint) -> None:
        """Turn an edge drag into the region's new size and offsets.

        The rect is moved first and the offsets are then *derived* from where
        it landed (``region_offsets`` is the exact inverse of the placement
        rule), so every anchor comes out right without a case per edge per
        anchor — a bottom-anchored region grows upward from its own baseline,
        a top-anchored one downward.

        A no-op if the dragged region has gone (see ``mouseMoveEvent``).
        """
        region = self._dragged_region()
        if region is None:
            return
        rect = resize_rect(
            self._region_resize_base,
            self._region_resize_edges,
            delta.x(),
            delta.y(),
            MIN_REGION_WIDTH,
            self._min_region_height(self._drag_region),
        )
        region.width = rect.width()
        region.height = rect.height()
        region.dx, region.dy = region_offsets(
            region.anchor,
            rect.x(),
            rect.y(),
            rect.width(),
            rect.height(),
            self.width(),
            self.height(),
        )

    def _dragged_region(self) -> OverlayRegion | None:
        """The stored placement of the region being dragged, or None.

        A read, never an index: ``_drag_region`` can outlive the region it
        names (#154), and both drag paths run inside a live Qt event handler
        on a translucent always-on-top window, where a KeyError is a crash
        over the game.
        """
        if self._drag_region is None or self._state is None:
            return None
        regions = self._state.overlay_regions
        return regions.get(self._drag_region) if regions else None

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        self._drag_region = None
        self._region_resize_edges = NO_EDGES
        super().mouseReleaseEvent(event)

    # -- the region registry -----------------------------------------------------

    def _builtin_regions(self) -> list[RegionRecord]:
        """The four regions the overlay ships with, in stacked-layout order.

        A method rather than a module table because every field closes over
        instance state — the hosts, the live content dicts, the size hints —
        which is exactly what a fifth region contributed at runtime brings
        with it too.
        """
        return [
            RegionRecord(
                key="lanes",
                title="CH chains",
                host=self._lanes_host,
                layout=self._lanes_layout,
                default=OverlayRegion(anchor="top"),
                default_width=weak_hook(self._lanes_width, MIN_REGION_WIDTH),
                has_content=weak_hook(self._has_lanes, False),
                preview=weak_hook(self._preview_lanes, []),
                builtin=True,
            ),
            RegionRecord(
                key="utility",
                title="Utility",
                host=self._utility_host,
                layout=self._utility_layout,
                default=OverlayRegion(anchor="top", dy=96),
                default_width=weak_hook(self._utility_width, MIN_REGION_WIDTH),
                has_content=weak_hook(self._has_utility, False),
                preview=weak_hook(self._preview_utility, []),
                on_preview_cleared=weak_hook(self._hide_empty_utility_header, None),
                builtin=True,
            ),
            RegionRecord(
                key="alert",
                title="Alerts",
                host=self._alert_host,
                layout=self._alert_layout,
                default=OverlayRegion(anchor="center"),
                # The alert spans the window by default — the panel inside it
                # does its own centering, which is why it also takes the whole
                # stacked cell (NO_ALIGNMENT) rather than being centered in it.
                default_width=weak_hook(self._alert_width, MIN_REGION_WIDTH),
                has_content=weak_hook(self._has_alert, False),
                preview=weak_hook(self._preview_alert, []),
                min_height=weak_hook(self._min_alert_height, MIN_REGION_HEIGHT),
                stack_alignment=NO_ALIGNMENT,
                builtin=True,
            ),
            RegionRecord(
                key="bars",
                title="Timer bars",
                host=self._bars_host,
                layout=self._bars_layout,
                default=OverlayRegion(anchor="bottom"),
                default_width=lambda: BAR_WIDTH,  # no ``self``, so no weak_hook
                has_content=weak_hook(self._has_bars, False),
                preview=weak_hook(self._preview_bars, []),
                builtin=True,
            ),
        ]

    # The built-ins' hooks, as named methods rather than lambdas, so
    # ``weak_hook`` can hold them weakly (see its docstring).

    def _lanes_width(self) -> int:
        return max(LANES_WIDTH, self._lanes_host.sizeHint().width())

    def _utility_width(self) -> int:
        return max(320, self._utility_host.sizeHint().width())

    def _alert_width(self) -> int:
        """The Alerts region spans the window; the panel inside it centers."""
        return self.width()

    def _has_lanes(self) -> bool:
        return bool(self._chain_lanes)

    def _has_utility(self) -> bool:
        return bool(self._utility_lines)

    def _has_alert(self) -> bool:
        return bool(self._alert_text)

    def _has_bars(self) -> bool:
        return bool(self._bars)

    def _register_region(self, record: RegionRecord) -> None:
        """Put a record in the registry and mint its position-mode chip."""
        host = record.host
        if host.parent() is not self:
            host.setParent(self)
        if not host.objectName():
            host.setObjectName(f"OverlayRegion_{qss_id(record.key)}")
        record.host_style = host.styleSheet()
        chip = QLabel(record.title, self)
        # A contributed region's title is somebody else's string, and Qt's
        # AutoText renders anything tag-shaped as markup (same reasoning as
        # every label on this window that shows text we did not write).
        chip.setTextFormat(Qt.TextFormat.PlainText)
        set_caps(chip)
        chip.hide()
        self._style_region_chip(chip)
        record.chip = chip
        self._regions[record.key] = record

    def _style_region_chip(self, chip: QLabel) -> None:
        """The dashed-chrome title chip's type — one copy, so a chip minted by
        ``add_region`` after startup is dressed exactly like the built-ins."""
        chip.setStyleSheet(
            skins.typography_style(
                self._font_size, skins.SMALL_DISPLAY, color=self._skin.overlay_chip_text
            )
            + f" background-color: {self._skin.overlay_chip_fill}; padding: 1px 4px;"
        )

    def add_region(
        self,
        key: str,
        host: QWidget,
        *,
        title: str,
        has_content: Callable[[], bool],
        default: OverlayRegion | None = None,
        default_width: Callable[[], int] | None = None,
        layout: QLayout | None = None,
        preview: Callable[[], list[QWidget]] | None = None,
        min_height: Callable[[], int] | None = None,
    ) -> bool:
        """Register a region and place it — live, at any moment.

        In position mode the new host is given its dashed chrome and its
        sample content immediately; in the legacy stacked layout it is
        inserted into the band its ``default.anchor`` names rather than
        appended after the stretch items, which is where a plain
        ``addWidget`` would have put it (under the timer bars).

        Idempotent: re-adding a key replaces the record, which is what a
        plugin re-enabling looks like. A built-in key is never taken over.
        Returns False when the call was refused.

        The caller owns ``host``: ``remove_region`` hands it back hidden and
        unparented rather than deleting it.
        """
        existing = self._regions.get(key)
        if existing is not None and existing.builtin:
            logger.warning("refusing to replace built-in overlay region %r", key)
            return False
        self.remove_region(key)
        record = RegionRecord(
            key=key,
            title=title,
            host=host,
            layout=layout if layout is not None else host.layout(),
            default=default.model_copy() if default is not None else OverlayRegion(),
            default_width=(
                default_width
                if default_width is not None
                else (lambda: max(MIN_REGION_WIDTH, host.sizeHint().width()))
            ),
            has_content=has_content,
            preview=preview,
            min_height=min_height,
        )
        self._register_region(record)
        if self._edit_mode:
            self._apply_region_chrome(record, True)
        if self._preview_shown:
            self._populate_preview(record)
        if self._region_mode():
            self._layout_regions()  # shows the host as part of placing it
        else:
            self._rebuild_stacked_layout()
            # ``remove_region`` hides a host on its way out, and Qt never
            # un-hides an explicitly hidden widget that rejoins a layout.
            host.show()
        if self._edit_mode:
            self._position_region_chrome()
        self._update_visibility()
        return True

    def remove_region(self, key: str) -> bool:
        """Retire a region — live. Idempotent; refuses the four built-ins.

        Everything the overlay attached to the region goes with it: the title
        chip, any position-mode sample content, its place in the stacked
        layout, and the DRAG. That last one is a real bug this closes (#154):
        position mode drops ``WindowTransparentForInput`` so the overlay is
        clickable, which means a region genuinely can vanish mid-drag, and a
        ``_drag_region`` left naming it then indexed a key
        ``overlay_regions`` no longer had.

        The host comes back hidden and unparented; whoever contributed it
        owns it. Its persisted placement is deliberately left in
        ``overlay_regions`` — a plugin disabled and re-enabled comes back
        where the user put it.
        """
        record = self._regions.get(key)
        if record is None or record.builtin:
            return False
        del self._regions[key]
        if self._drag_region == key:
            self._drag_region = None
            self._region_drag_start = None
            self._region_resize_edges = NO_EDGES
        self._discard_preview(record)
        if record.chip is not None:
            record.chip.hide()
            record.chip.setParent(None)
            record.chip.deleteLater()
            record.chip = None
        record.host.hide()
        record.host.setParent(None)  # also takes it out of _main_layout
        if not self._region_mode():
            self._rebuild_stacked_layout()
        self._update_visibility()
        return True

    def _rebuild_stacked_layout(self) -> None:
        """Re-lay the registered hosts in the legacy stacked QVBoxLayout.

        Rebuilt whole rather than patched, because that layout is
        insertion-order-sensitive: the hosts sit around two stretch items, so
        a region registered later would be appended BELOW the bottom stretch
        — under the timer bars — instead of landing in its own band (#154).
        A region's band is its default anchor, which is what the three groups
        between the stretches already mean.

        Placement only — never ``show()``. This also runs during construction,
        where the hosts must stay unshown until ``apply_skin`` has dressed
        what is inside them (which is how master behaved); ``add_region``
        shows a host it has just placed instead.
        """
        while self._main_layout.count():
            self._main_layout.takeAt(0)
        for anchor, stretch in (("top", 0), ("center", 2), ("bottom", 3)):
            if stretch:
                self._main_layout.addStretch(stretch)
            for record in self._regions.values():
                if record.default.anchor != anchor:
                    continue
                self._main_layout.addWidget(record.host, 0, record.stack_alignment)

    @property
    def _region_titles(self) -> dict[str, QLabel]:
        """The position-mode title chips by region key (test/debug hook)."""
        return {
            key: record.chip for key, record in self._regions.items() if record.chip is not None
        }

    @property
    def _preview_widgets(self) -> list[QWidget]:
        """Every region's position-mode sample widgets, flattened (test hook)."""
        return [widget for record in self._regions.values() for widget in record.preview_widgets]

    # -- per-region positioning --------------------------------------------------

    def _region_hosts(self) -> dict[str, QWidget]:
        """The registered regions' hosts, in registration order."""
        return {key: record.host for key, record in self._regions.items()}

    def _default_region(self, key: str) -> OverlayRegion:
        """The stacked-layout default placement for a region host — the single
        source of truth used to seed and to backfill missing keys (e.g. the
        'utility' region absent from a layout saved before 1.11).

        A COPY every time: callers store what they get in ``overlay_regions``
        and then drag it, which would otherwise walk the record's own default
        off its anchor. An unregistered key answers a bare default rather
        than raising.
        """
        record = self._regions.get(key)
        return record.default.model_copy() if record is not None else OverlayRegion()

    def _region_mode(self) -> bool:
        return self._state is not None and self._state.overlay_regions is not None

    def _begin_region_edit(
        self, key: str, global_start: QPoint, edges: Qt.Edge | None = None
    ) -> None:
        """Start moving (``edges`` empty) or resizing one region."""
        edges = edges if edges is not None else NO_EDGES
        # First region edit initializes overlay_regions to defaults matching
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
        self._region_resize_edges = edges
        self._region_resize_base = self._region_rect(key)

    def _activate_region_layout(self) -> None:
        """Take every registered host out of the stacked QVBoxLayout so they
        can be placed manually. The stretch items stay behind harmlessly."""
        for record in self._regions.values():
            self._main_layout.removeWidget(record.host)
        self._layout_regions()

    def _region_size(self, key: str, region: OverlayRegion) -> tuple[int, int]:
        """The (width, height) a region host is laid out at.

        A configured ``height`` is a FLOOR for the content-driven regions: a
        bars region dragged short still grows when a fifth bar lands, rather
        than swallowing it. **Alerts is the exception and reads it exactly**,
        because that region has a way to honor it — ``_alert_budget_height``
        takes the same number as the headline's budget, so the text is
        shrunk (then crawled) into the box the user drew instead of the box
        growing to the text.

        A key no longer registered — a region retired between a layout pass
        and this call — answers a usable size rather than raising.
        """
        record = self._regions.get(key)
        if region.width is not None:
            host_w = region.width
        elif record is not None:
            host_w = record.default_width()
        else:
            host_w = MIN_REGION_WIDTH
        if key == "alert" and region.height:
            host_h = self._alert_region_height(region)
        else:
            hint = record.host.sizeHint().height() if record is not None else 1
            host_h = max(1, hint, region.height or 0)
        return max(MIN_REGION_WIDTH, host_w), host_h

    def _region_rect(self, key: str) -> QRect:
        """Where a region host currently sits, from its persisted placement."""
        region = self._region_for(key)
        host_w, host_h = self._region_size(key, region)
        x, y = region_origin(
            region.anchor, region.dx, region.dy, host_w, host_h, self.width(), self.height()
        )
        return QRect(x, y, host_w, host_h)

    def _region_for(self, key: str) -> OverlayRegion:
        """The stored region, backfilling one absent from an older layout
        (e.g. 'utility', missing from anything saved before 1.11)."""
        regions = self._state.overlay_regions if self._state is not None else None
        if not regions:
            return self._default_region(key)
        return regions.get(key) or self._default_region(key)

    def _layout_regions(self) -> None:
        """Place each host at its anchor line + (dx, dy), centered horizontally
        on the window center by default. Lanes/bars grow downward from the
        anchor point; the legacy (None) path never calls this."""
        regions = self._state.overlay_regions if self._state is not None else None
        if not regions:
            return
        for key, record in self._regions.items():
            rect = self._region_rect(key)
            record.host.resize(rect.width(), rect.height())
            record.host.move(rect.x(), rect.y())
            record.host.show()

    # -- positioning-mode preview & chrome ---------------------------------------

    def _set_region_chrome(self, on: bool) -> None:
        """Dashed border + title chip on every region host while editing."""
        for record in self._regions.values():
            self._apply_region_chrome(record, on)
        if on:
            self._position_region_chrome()

    def _apply_region_chrome(self, record: RegionRecord, on: bool) -> None:
        """One region's dashed border and chip — also the path a region added
        while position mode is already up takes.

        The border is APPENDED to the host's own stylesheet and that sheet is
        what is put back when the chrome comes off, so a contributed host
        that paints itself is not stripped by a visit to position mode.
        """
        host = record.host
        if on:
            own = f"{record.host_style}\n" if record.host_style else ""
            host.setStyleSheet(
                own + f"#{host.objectName()} {{ border: 1px dashed"
                f" {skins.rgba(self._skin.chrome_accent, 0.66)}; }}"
            )
        else:
            host.setStyleSheet(record.host_style)
        if record.chip is None:
            return
        if on:
            record.chip.show()
            record.chip.raise_()
        else:
            record.chip.hide()

    def _position_region_chrome(self) -> None:
        for record in self._regions.values():
            chip = record.chip
            if chip is None or not chip.isVisible():
                continue
            chip.adjustSize()
            p = record.host.pos()
            chip.move(p.x(), max(0, p.y()))
            chip.raise_()

    def _preview_lanes(self) -> list[QWidget]:
        """Sample CH lane with two static chips (and a sample cadence marker so
        the muted "next cast" tick is visible while positioning, #15)."""
        lane = _ChainLane("Sample Target", self)
        lane.setFixedWidth(LANES_WIDTH)
        lane.cadence_seconds = 4
        row = self._build_lane_row("Sample Target", lane)
        self._lanes_layout.addWidget(row)
        lane.show()
        lane.add_static_chip("CH", 2)
        lane.add_static_chip("CH", 6)
        return [row]

    def _preview_alert(self) -> list[QWidget]:
        """Sample alert label styled exactly like ``_center_text`` (yellow, like
        the bard counter). Divergence from the Phase-1 note: inserted into the
        alert host's layout (not the main layout) so it rides the alert region.
        """
        label = QLabel("ENRAGED", self)
        label.setObjectName("EventOverlayPreviewAlert")
        label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        label.setWordWrap(True)
        self._style_preview_alert(label)
        self._apply_text_shadow(label)
        self._alert_layout.insertWidget(
            self._alert_layout.indexOf(self._alert_viewport) + 1,
            label,
            0,
            Qt.AlignmentFlag.AlignHCenter,
        )
        label.show()
        return [label]

    def _preview_utility(self) -> list[QWidget]:
        """Sample line under the "Utility" header (#14)."""
        self._utility_header.show()
        util = QLabel("Rebuff: Sample — buff faded", self)
        util.setObjectName("OverlayUtilityLine")
        util.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        util.setProperty("line_color", self._skin.alert_kicker_color)
        util.setStyleSheet(self._utility_line_style(self._skin.alert_kicker_color))
        self._utility_layout.addWidget(util, 0, Qt.AlignmentFlag.AlignHCenter)
        util.show()
        return [util]

    def _hide_empty_utility_header(self) -> None:
        """The header the sample line was shown under, back down once the
        sample goes — unless real utility lines are up."""
        if not self._utility_lines:
            self._utility_header.hide()

    def _preview_bars(self) -> list[QWidget]:
        """Sample timer bars (do NOT start ``_bar_timer`` — these never tick)."""
        bars: list[QWidget] = []
        for bar in (
            self._make_bar_widget("Sample Timer", DEFAULT_BAR_COLOR, 60, 45),
            self._make_bar_widget("CH Warning", "red", 10, 6),
        ):
            self._bars_layout.addWidget(bar)
            bar.show()
            bars.append(bar)
        return bars

    def _show_preview(self) -> None:
        """Populate each region with labeled sample content so the user sees
        where CH lanes, alerts, and timer bars land. Idempotent; adds nothing
        to live state and publishes no events.

        Which regions, and what each shows, is the registry's business now —
        a region contributed at runtime brings its own sample factory, and one
        added WHILE position mode is up is populated by ``add_region``.
        """
        if self._preview_shown:
            return
        self._preview_shown = True
        for record in list(self._regions.values()):
            self._populate_preview(record)
        if self._region_mode():
            self._layout_regions()

    def _populate_preview(self, record: RegionRecord) -> None:
        """One region's sample content. Idempotent; a region with no preview
        factory simply shows nothing while positioning."""
        if record.preview is None or record.preview_widgets:
            return
        record.preview_widgets.extend(record.preview())

    def _clear_preview(self) -> None:
        """Remove all preview widgets from their layouts. Idempotent."""
        if not self._preview_shown:
            return
        self._preview_shown = False
        for record in list(self._regions.values()):
            self._discard_preview(record)
        if self._region_mode():
            self._layout_regions()

    def _discard_preview(self, record: RegionRecord) -> None:
        """Take one region's sample content back out — also what a region
        retired mid-position-mode goes through, so nothing is left parented to
        a host the overlay has handed back."""
        if not record.preview_widgets:
            return
        for widget in record.preview_widgets:
            if record.layout is not None:
                record.layout.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        record.preview_widgets.clear()
        if record.on_preview_cleared is not None:
            record.on_preview_cleared()

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
        name.setObjectName("ChLaneName")
        name.setTextFormat(Qt.TextFormat.PlainText)  # a player name, not markup
        name.setStyleSheet(
            skins.typography_style(
                self._font_size, skins.SMALL_DISPLAY, color=self._skin.name_color
            )
        )
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
            label.setTextFormat(Qt.TextFormat.PlainText)  # trigger text, not markup
            label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            self._utility_layout.addWidget(label, 0, Qt.AlignmentFlag.AlignHCenter)
            self._utility_lines[event.text] = label
        # ``color`` comes from the trigger's own config via resolve_color — it
        # is the user's choice and must NOT become a skin token. Only the size
        # and weight below are ours.
        label.setProperty("line_color", color)
        label.setStyleSheet(self._utility_line_style(color))
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
        # A bar's name is the trigger's timer name — user data, same rich-text
        # hazard as the alert labels.
        name_label.setTextFormat(Qt.TextFormat.PlainText)
        value_label = QLabel(f"{max(0, int(remaining))}s", bar)
        value_label.setObjectName("OverlayBarValue")
        value_label.setTextFormat(Qt.TextFormat.PlainText)
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

    def _alert_budget_height(self) -> int:
        """How tall the headline may be before it shrinks — and, past the size
        floor, scrolls.

        A resized Alerts region says so itself (#102); otherwise it is a
        fraction of the overlay's own height, as it always was. The kicker
        and the rule are inside the region too, so they come off the top of a
        configured budget or the region would grow past what the user drew.
        """
        region = None
        if self._region_mode():
            regions = self._state.overlay_regions or {}
            region = regions.get("alert")
        if region is not None and region.height:
            # EXACT, never floored: the region height is the box the user
            # drew and the viewport has to fit inside it. Clamping this up to
            # MIN_ALERT_BUDGET made a short region's headline taller than the
            # region itself, which is the clipping this whole path exists to
            # stop. The floor lives on the resize instead (_min_region_height)
            # so the box can never get small enough to need one here.
            return max(1, self._alert_region_height(region) - self._alert_chrome_height())
        return max(MIN_ALERT_BUDGET, round((self.height() or 600) * ALERT_HEIGHT_FRACTION))

    def _alert_region_height(self, region: OverlayRegion) -> int:
        """The exact height the Alerts host is laid out at for ``region``.

        THE number for that region: ``_region_size`` lays the host out at it
        and ``_alert_budget_height`` fits the headline inside it. Two
        expressions for it is precisely how the headline came to be budgeted
        for a box the host did not have.
        """
        return max(MIN_REGION_HEIGHT, region.height or 0)

    def _alert_chrome_height(self, *, worst_case: bool = False) -> int:
        """What the kicker, the rule and the gaps cost inside that region.

        Two callers that must agree, asking in different tenses. The budget
        subtracts what the chrome costs RIGHT NOW, because that is what is
        sharing the region with this headline. The region's resize floor adds
        back the most it could EVER cost (``worst_case``), because the floor is
        evaluated in position mode — where no alert is live, so the kicker is
        hidden and costs nothing. Reading the live number there promised a
        floor the next alert with a kicker broke by the kicker's own height:
        measured, a region dragged to 47 answered a 29px budget against a
        ``MIN_ALERT_BUDGET`` of 40 (#107).

        A skin with no rule still costs nothing in the worst case — a hidden
        ``_Hairline`` is sized (0, 0) — so this is worst case over what the
        ALERT can do, not over what the skin might have been.

        ``isHidden`` not ``isVisible``: this runs while the overlay itself is
        still hidden (it is shown once there is something to show), and a
        child of a hidden window is never "visible".
        """
        chrome = self._alert_layout.spacing() * 2
        if worst_case:
            chrome += self._kicker_worst_height
        elif not self._alert_kicker.isHidden():
            chrome += self._alert_kicker.sizeHint().height()
        if worst_case or not self._alert_rule.isHidden():
            chrome += self._alert_rule.sizeHint().height()
        return chrome

    def _measure_kicker_height(self) -> int:
        """The tallest the kicker can be, whatever it ends up saying.

        A single-line QLabel's height is its font's line height and does not
        depend on which glyphs it holds — but it DOES depend on whether it
        holds any, and the floor is computed in position mode, where the
        kicker is empty. The difference is platform-specific and goes both
        ways: measured in CI, the populated label is one pixel taller on
        Linux (so the floor under-charged and the "guaranteed" budget came
        out at 39 against a ``MIN_ALERT_BUDGET`` of 40 — the same defect
        #107 is about, one pixel instead of eleven) and two pixels shorter
        on Windows, while macOS reports the same number either way. So ask
        the widget both ways and keep the larger.

        Measured through the real widget rather than from a ``QFontMetrics``
        of our own: the size is set by a stylesheet, and a QLabel's own
        ``font()`` does not carry that (the trap ``_measure_headline``
        documents). Called from ``apply_skin`` and cached, because it is the
        skin and the base font that move this number — and because the
        alternative is mutating a widget from inside ``_min_region_height``,
        which a region drag calls on every mouse move.
        """
        kicker = self._alert_kicker
        current = kicker.sizeHint().height()
        if kicker.text():
            return current  # already the populated measurement
        kicker.setText(KICKER_HEIGHT_PROBE)
        try:
            populated = kicker.sizeHint().height()
        finally:
            kicker.setText("")
        return max(current, populated)

    def _measure_headline(self, size: int) -> QRect:
        """The rect the current headline wraps into at ``size`` px.

        Measured from font metrics rather than the label's ``sizeHint``: a
        stylesheet font size is not resolved until the widget is polished, so
        asking the label right after ``setStyleSheet`` would read the old
        font. This is also what makes the fit search testable.
        """
        # The host's width, not the viewport's: the viewport is the host's
        # only full-width layout item, and the host is the one that has a
        # real width before the first layout pass. That equality is asserted
        # rather than assumed now — it was false for the whole of #103, and
        # measuring against the host is exactly why nothing here looked wrong
        # while the text was being drawn one pixel wide (#107).
        width = max(80, (self._alert_host.width() or self.width()) - 8)
        font = QFont(self._center_text.font())
        font.setPixelSize(size)
        font.setBold(True)
        return QFontMetrics(font).boundingRect(
            QRect(0, 0, width, 0),
            int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignHCenter),
            self._center_text.text(),
        )

    def _headline_size(self) -> int:
        """The headline's size after shrink-to-fit.

        Long alerts (a full raid-mob description pasted into a trigger) used to
        wrap past the bottom of the region and clip mid-word. Shrinking is the
        first answer; ``_relayout_headline`` handles what is still too long.
        """
        if not self._center_text.text():
            return self._text_size
        return fit_text_size(self._measure_headline, self._alert_budget_height(), self._text_size)

    def _restyle_alert(self) -> None:
        """Paint the headline at the fitted size, color and pulse phase."""
        color = self._text_color or DEFAULT_TEXT_COLOR
        if not self._pulse_on:
            dim = qcolor(color)
            dim.setAlpha(round(255 * PULSE_DIM))
            color = f"rgba({dim.red()}, {dim.green()}, {dim.blue()}, {dim.alpha()})"
        size = self._headline_size()
        self._center_text.setStyleSheet(
            skins.typography_style(size, skins.TypographyRole(1.0, "bold"), color=color)
            + " background: transparent;"
        )
        self._relayout_headline(size)

    # -- headline crawl ------------------------------------------------------------

    def _relayout_headline(self, size: int) -> None:
        """Fit the headline into its viewport, and crawl it if it overflows.

        Called from every path that changes what the headline says or how big
        its slot is (a new alert, a skin/appearance change, a window or region
        resize). Restarting a crawl already in flight is deliberate: the text
        it was walking is no longer the text on screen.
        """
        if not self._center_text.text():
            # Also the constructor's path (``_set_text_color`` restyles before
            # ``_alert_layout`` exists), so it must not ask for the budget.
            self._stop_scroll()
            self._alert_viewport.collapse()
            return
        measured = self._measure_headline(size)
        overflow = self._alert_viewport.apply_text_layout(
            measured.height(), self._alert_budget_height()
        )
        if not overflow:
            self._stop_scroll()
            return
        font = QFont(self._center_text.font())
        font.setPixelSize(size)
        font.setBold(True)
        self._start_scroll(overflow, QFontMetrics(font).lineSpacing())

    def _start_scroll(self, overflow: int, line_height: int) -> None:
        self._scroll_travel = overflow
        self._scroll_offset = 0.0
        self._scroll_speed = alert_scroll_speed(overflow, self._clear_after_ms, line_height)
        self._scroll_hold_ticks = round((self._clear_after_ms * SCROLL_HEAD_HOLD) / SCROLL_TICK_MS)
        self._alert_viewport.reset()
        if self._scroll_speed > 0 and not self._scroll_timer.isActive():
            self._scroll_timer.start()

    def _stop_scroll(self) -> None:
        self._scroll_timer.stop()
        self._scroll_travel = 0
        self._scroll_offset = 0.0
        self._scroll_speed = 0.0
        self._alert_viewport.reset()

    def _advance_scroll(self) -> None:
        if self._scroll_hold_ticks > 0:
            self._scroll_hold_ticks -= 1
            return
        self._scroll_offset += self._scroll_speed * (SCROLL_TICK_MS / 1000.0)
        if self._scroll_offset >= self._scroll_travel:
            # Arrived: park on the last line for whatever the alert has left.
            self._scroll_offset = float(self._scroll_travel)
            self._scroll_timer.stop()
        self._alert_viewport.set_offset(self._scroll_offset)

    def is_scrolling(self) -> bool:
        """Whether a headline crawl is in flight (test/debug hook)."""
        return self._scroll_timer.isActive()

    def scroll_offset(self) -> float:
        """How far the headline has crawled, in px (test/debug hook)."""
        return self._scroll_offset

    def _style_preview_alert(self, label: QLabel) -> None:
        """Keep an existing edit-mode sample in step with live appearance."""
        label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        label.setStyleSheet(
            skins.typography_style(
                self._text_size, skins.TypographyRole(1.0, "bold"), color="yellow"
            )
            + " background: transparent;"
        )
        self._alert_layout.setAlignment(label, Qt.AlignmentFlag.AlignHCenter)

    def clear_text(self) -> None:
        self._clear_timer.stop()
        self._stop_scroll()
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
                f"color: {skin.value_color}; font-size: {text_size}px; font-weight: bold;"
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
        # Per region, off its own record: an OR over four literals could
        # never let a fifth region keep the overlay on screen by itself (#154).
        active = any(record.has_content() for record in self._regions.values())
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
