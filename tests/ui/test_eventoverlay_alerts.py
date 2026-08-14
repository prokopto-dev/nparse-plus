"""Event-overlay alert presentation (#102).

Three complaints from one report, and they share one surface:

1. a long trigger "Display text" was cut off at the bottom of its region;
2. the Alerts region could be moved in position mode but not resized;
3. part of the alert turned into small gold caps for no reason the user
   could see.

The rules that answer them are pure functions here, tested without a window
wherever that is possible, plus the widget-level checks that they are wired
to what the user actually sees.
"""

from datetime import datetime
from math import ceil

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtWidgets import QApplication

from nparseplus.config.settings import OverlayRegion, WindowState
from nparseplus.core.events import OverlayEvent
from nparseplus.ui.eventoverlay import (
    KICKER_MAX_CHARS,
    MIN_ALERT_BUDGET,
    MIN_REGION_HEIGHT,
    MIN_REGION_WIDTH,
    SCROLL_HEAD_HOLD,
    SCROLL_TAIL_HOLD,
    SCROLL_TICK_MS,
    EventOverlayWindow,
    alert_scroll_speed,
    first_alert_separator,
    looks_like_a_kicker,
    region_offsets,
    region_origin,
    region_resize_margin,
    resize_rect,
    split_alert_text,
)

pytestmark = pytest.mark.qt

# The exact Display text from the issue. Its first " - " is 41 characters in,
# which is what used to become the gold kicker.
REPORTED_ALERT = (
    "<Dozekar The Cursed> [Slowable, baneable] - [CH Unslowed: 2s, Slowed: 4s] // "
    "Silver Breath (PBAOE, 300 rng, unresistable, 12s CD): 400 dmg + 1 slot dispel "
    "| **Keep junk buff in top slot!**"
)

# Long enough that no size in the search range fits it in a small region.
PARAGRAPH = (
    "Dozekar the Cursed rampages and breathes an unresistable point blank area "
    "effect every twelve seconds; keep a junk buff in the top slot because the "
    "breath strips one, and chain heal the tank without pause. "
) * 3


def _alert(overlay: EventOverlayWindow, text: str) -> None:
    overlay.handle_event(
        OverlayEvent(timestamp=datetime.now(), line="", line_number=1, text=text, foreground="Red")
    )


def _press(overlay: EventOverlayWindow, x: int, y: int) -> None:
    pt = QPointF(x, y)
    overlay.mousePressEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            pt,
            pt,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


def _move(overlay: EventOverlayWindow, x: int, y: int) -> None:
    pt = QPointF(x, y)
    overlay.mouseMoveEvent(
        QMouseEvent(
            QEvent.Type.MouseMove,
            pt,
            pt,
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


# -- part 3: what may become a kicker ------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # The two cases the design is for, both still split.
        ("Gorenaire — ENRAGED", ("Gorenaire", "ENRAGED")),
        ("FTE: Someone", ("FTE", "Someone")),
        ("Gorenaire - ENRAGED", ("Gorenaire", "ENRAGED")),
        ("Lord Nagafen -- ENRAGED", ("Lord Nagafen", "ENRAGED")),
    ],
)
def test_the_design_cases_still_split(text, expected) -> None:
    assert split_alert_text(text) == expected


def test_the_reported_info_dump_is_one_headline() -> None:
    """The bug: an info-dump's first 41 characters became gold caps because
    the line happens to contain " - " before "[CH Unslowed"."""
    kicker, headline = split_alert_text(REPORTED_ALERT)
    assert kicker == ""
    assert headline == REPORTED_ALERT


@pytest.mark.parametrize(
    "text",
    [
        # Every one of these contains a separator and none of them opens with
        # something a user would call a name.
        "<Dozekar The Cursed> [Slowable, baneable] - [CH Unslowed: 2s]",
        "Keep a junk buff in the top slot - the breath strips one",
        "Silver Breath (PBAOE, 300 rng) - 400 dmg",
        "Chain heal the tank and stay out of the breath: it strips a buff",
    ],
)
def test_prose_is_never_split(text) -> None:
    assert split_alert_text(text) == ("", text)


def test_a_short_label_before_a_colon_is_still_a_kicker() -> None:
    """``": "`` earns its place: two shipped built-in triggers are exactly
    this shape (``Death Touch: {target}``, ``Resisted: {spell}``), which is
    why the fix constrains the head rather than dropping the separator."""
    assert split_alert_text("Death Touch: Soandso") == ("Death Touch", "Soandso")
    assert split_alert_text("Resisted: Fear") == ("Resisted", "Fear")
    # The rule is about the head alone, so this splits too — and a user who
    # has read the rule can predict that, which is the whole ask.
    assert split_alert_text("Note: adds are incoming from the west") == (
        "Note",
        "adds are incoming from the west",
    )


@pytest.mark.parametrize(
    ("head", "ok"),
    [
        ("Gorenaire", True),
        ("FTE", True),
        ("Lord Nagafen", True),
        ("Vindi", True),
        ("A" * KICKER_MAX_CHARS, True),
        ("A" * (KICKER_MAX_CHARS + 1), False),  # too long to be a name
        ("one two three four five", False),  # too many words to be a name
        ("<Dozekar>", False),  # markup-ish punctuation
        ("Dozekar [Slowable]", False),
        ("Breath (PBAOE)", False),
        ("CH / heals", False),
        ("", False),
        ("   ", False),
    ],
)
def test_looks_like_a_kicker(head, ok) -> None:
    assert looks_like_a_kicker(head) is ok


def test_only_the_first_separator_in_the_text_is_considered() -> None:
    # Earliest in the TEXT, not first in the separator tuple: ": " at index 3
    # beats the later " - ", so the kicker is "FTE" and not "FTE: Someone".
    assert first_alert_separator("FTE: Someone - and more") == (3, ": ")
    assert split_alert_text("FTE: Someone - and more") == ("FTE", "Someone - and more")


def test_a_failed_first_split_does_not_fall_through_to_a_later_one() -> None:
    """Falling through would go looking for a split the author did not write:
    this text has a perfectly kicker-shaped "Silver Breath: ..." in it, and
    using it would silently reword the alert."""
    text = "Keep a junk buff in the top slot - Silver Breath: strips one"
    assert split_alert_text(text) == ("", text)


def test_the_overlay_shows_the_reported_alert_whole(qtbot) -> None:
    overlay = EventOverlayWindow()
    qtbot.addWidget(overlay)
    overlay.resize(1200, 800)
    overlay.show()
    _alert(overlay, REPORTED_ALERT)

    assert overlay._center_text.text() == REPORTED_ALERT
    assert overlay._alert_kicker.text() == ""
    assert overlay._alert_kicker.isHidden()
    # The invariant: presentation never changes what the reset match sees.
    assert overlay.current_text() == REPORTED_ALERT
    overlay.handle_event(OverlayEvent(text=REPORTED_ALERT, reset=True))
    assert overlay.current_text() == ""


# -- part 1: fit, then crawl ----------------------------------------------------


def test_alert_scroll_speed_finishes_inside_the_alert_lifetime() -> None:
    overflow, lifetime_ms, line = 120, 4000, 20
    speed = alert_scroll_speed(overflow, lifetime_ms, line)
    travel_s = (lifetime_ms / 1000) * (1 - SCROLL_HEAD_HOLD - SCROLL_TAIL_HOLD)
    assert speed > 0
    assert overflow / speed <= travel_s + 1e-9
    # ...and with the dwells at each end it is still inside the lifetime.
    assert overflow / speed + (lifetime_ms / 1000) * SCROLL_TAIL_HOLD <= lifetime_ms / 1000


def test_alert_scroll_speed_is_derived_from_the_current_lifetime() -> None:
    """Never hardcoded: doubling ``overlay_text_seconds`` halves the crawl."""
    fast = alert_scroll_speed(60, 4000, 40)
    slow = alert_scroll_speed(60, 8000, 40)
    assert fast == pytest.approx(slow * 2)


def test_alert_scroll_speed_stops_at_the_readability_ceiling() -> None:
    # An alert far longer than its display time cannot have both; the
    # readable rate wins and the tail is what the reader does not reach.
    line = 16
    assert alert_scroll_speed(100_000, 4000, line) == pytest.approx(2.5 * line)


@pytest.mark.parametrize(("overflow", "lifetime"), [(0, 4000), (-5, 4000), (100, 0)])
def test_alert_scroll_speed_is_zero_when_there_is_nothing_to_walk(overflow, lifetime) -> None:
    assert alert_scroll_speed(overflow, lifetime, 20) == 0.0


def test_a_headline_that_fits_never_crawls(qtbot) -> None:
    overlay = EventOverlayWindow()
    qtbot.addWidget(overlay)
    overlay.resize(1200, 800)
    overlay.show()
    _alert(overlay, "ENRAGED")
    assert not overlay.is_scrolling()
    assert overlay.scroll_offset() == 0.0


def test_a_headline_too_long_for_the_size_floor_crawls_instead_of_clipping(qtbot) -> None:
    """The reported bug: shrink-to-fit bottomed out at MIN_ALERT_TEXT_SIZE and
    the rest of the sentence was simply gone."""
    overlay = EventOverlayWindow()
    qtbot.addWidget(overlay)
    overlay.resize(400, 200)
    overlay.show()
    _alert(overlay, PARAGRAPH)

    assert overlay.is_scrolling()
    travel = overlay._scroll_travel
    assert travel > 0
    # The label keeps its whole wrapped height; the viewport shows a slice.
    assert overlay._center_text.height() > overlay._alert_viewport.height()

    # Driving the crawl walks the entire overflow and then parks.
    for _ in range(5000):
        if not overlay.is_scrolling():
            break
        overlay._advance_scroll()
    assert not overlay.is_scrolling()
    assert overlay.scroll_offset() == pytest.approx(travel)
    # ...and none of it changed what the alert IS.
    assert overlay.current_text() == PARAGRAPH


def test_the_crawl_holds_at_the_top_before_it_moves(qtbot) -> None:
    overlay = EventOverlayWindow()
    qtbot.addWidget(overlay)
    overlay.resize(400, 200)
    overlay.show()
    _alert(overlay, PARAGRAPH)

    held = overlay._scroll_hold_ticks
    assert held == pytest.approx(round(4000 * SCROLL_HEAD_HOLD / SCROLL_TICK_MS), abs=1)
    for _ in range(held):
        overlay._advance_scroll()
    assert overlay.scroll_offset() == 0.0  # still parked on the first line
    overlay._advance_scroll()
    assert overlay.scroll_offset() > 0.0


def test_clearing_an_alert_stops_the_crawl(qtbot) -> None:
    overlay = EventOverlayWindow()
    qtbot.addWidget(overlay)
    overlay.resize(400, 200)
    overlay.show()
    _alert(overlay, PARAGRAPH)
    assert overlay.is_scrolling()

    overlay.clear_text()
    assert not overlay.is_scrolling()
    assert overlay.scroll_offset() == 0.0
    assert overlay._alert_viewport.height() == 0


def test_apply_timings_repaces_a_crawl_already_in_flight(qtbot) -> None:
    """#67 made the alert duration live; the crawl reads the CURRENT value or
    a lengthened alert would crawl on the old clock."""
    overlay = EventOverlayWindow()
    qtbot.addWidget(overlay)
    overlay.resize(400, 200)
    overlay.show()
    _alert(overlay, PARAGRAPH)
    was = overlay._scroll_speed
    assert was > 0

    overlay.apply_timings(clear_after_s=30.0)
    assert overlay._scroll_speed < was


# -- part 2: resizable regions ---------------------------------------------------


@pytest.mark.parametrize(
    ("edges", "dx", "dy", "expected"),
    [
        (Qt.Edge.RightEdge, 40, 0, QRect(100, 100, 240, 80)),
        (Qt.Edge.LeftEdge, 40, 0, QRect(140, 100, 160, 80)),
        (Qt.Edge.BottomEdge, 0, 30, QRect(100, 100, 200, 110)),
        (Qt.Edge.TopEdge, 0, 30, QRect(100, 130, 200, 50)),
        (Qt.Edge.TopEdge | Qt.Edge.LeftEdge, 20, 20, QRect(120, 120, 180, 60)),
        # Clamped: the opposite edge is held, so the rect stops at the minimum.
        (Qt.Edge.LeftEdge, 500, 0, QRect(180, 100, 120, 80)),
        (Qt.Edge.RightEdge, -500, 0, QRect(100, 100, 120, 80)),
    ],
)
def test_resize_rect_holds_the_opposite_edge(edges, dx, dy, expected) -> None:
    rect = QRect(100, 100, 200, 80)
    assert resize_rect(rect, edges, dx, dy, MIN_REGION_WIDTH, MIN_REGION_HEIGHT) == expected


@pytest.mark.parametrize("anchor", ["top", "center", "bottom"])
def test_region_origin_and_region_offsets_are_inverses(anchor) -> None:
    """What makes an edge drag expressible as "move the rect, then ask what
    offsets put it there" — every anchor for free."""
    win_w, win_h, host_w, host_h = 1600, 900, 520, 96
    for dx, dy in ((0, 0), (37, -12), (-200, 300)):
        x, y = region_origin(anchor, dx, dy, host_w, host_h, win_w, win_h)
        assert region_offsets(anchor, x, y, host_w, host_h, win_w, win_h) == (dx, dy)


def test_region_resize_margin_always_leaves_an_interior_to_drag_by() -> None:
    assert region_resize_margin(520, 300) == 7  # a big region: the normal band
    assert region_resize_margin(520, 30) * 2 < 30  # a lane strip: still movable
    assert region_resize_margin(10, 6) >= 1


def _region_state() -> WindowState:
    return WindowState(
        geometry=(0, 0, 1000, 800),
        overlay_regions={
            "lanes": OverlayRegion(anchor="top", width=520),
            "utility": OverlayRegion(anchor="top", dy=96),
            "alert": OverlayRegion(anchor="center", width=600),
            "bars": OverlayRegion(anchor="bottom"),
        },
    )


def test_dragging_a_region_edge_resizes_it_and_persists(qtbot) -> None:
    saves: list[int] = []
    state = _region_state()
    overlay = EventOverlayWindow(state=state, on_save=lambda: saves.append(1))
    qtbot.addWidget(overlay)
    overlay.set_edit_mode(True)
    overlay._layout_regions()

    alert = overlay._alert_host
    before = (alert.x(), alert.width(), alert.height())
    # Grab the right edge, mid-height, and pull it 60px wider.
    _press(overlay, alert.x() + alert.width() - 2, alert.y() + alert.height() // 2)
    assert overlay._drag_region == "alert"
    assert overlay._region_resize_edges & Qt.Edge.RightEdge
    _move(overlay, alert.x() + alert.width() - 2 + 60, alert.y() + alert.height() // 2)

    region = state.overlay_regions["alert"]
    assert region.width == before[1] + 60
    assert region.height == before[2]
    # The left edge is held: the region grew rightward, it did not recenter.
    assert overlay._alert_host.x() == before[0]

    overlay.set_edit_mode(False)
    assert saves == [1]
    restored = WindowState.model_validate_json(state.model_dump_json())
    assert restored.overlay_regions["alert"].width == before[1] + 60
    assert restored.overlay_regions["alert"].height == before[2]


def test_the_alerts_region_height_is_the_headline_budget(qtbot) -> None:
    """Part 1 has to respect the box the user drew in part 2."""
    state = _region_state()
    state.overlay_regions["alert"] = OverlayRegion(anchor="center", width=600, height=90)
    overlay = EventOverlayWindow(state=state)
    qtbot.addWidget(overlay)
    overlay.resize(1000, 800)
    overlay.show()

    # The region is honored exactly (it is a budget, not a floor)...
    assert overlay._alert_host.height() == 90
    assert overlay._alert_budget_height() <= 90
    # ...and it, not the window fraction, is what the headline is fitted into.
    assert overlay._alert_budget_height() < round(800 * 0.42)
    _alert(overlay, PARAGRAPH)
    assert overlay._alert_viewport.height() <= 90
    assert overlay.is_scrolling()


@pytest.mark.parametrize("text", ["ENRAGED", "Gorenaire — ENRAGED", REPORTED_ALERT, PARAGRAPH])
@pytest.mark.parametrize("height", [60, 90, 140, 240])
def test_the_headline_never_overruns_its_region(qtbot, text, height) -> None:
    """The shadow allowance around a headline that fits is spent out of the
    budget's own slack — taking it unconditionally would push the viewport
    past a configured Alerts region and clip the last line again."""
    state = _region_state()
    state.overlay_regions["alert"] = OverlayRegion(anchor="center", width=520, height=height)
    overlay = EventOverlayWindow(state=state)
    qtbot.addWidget(overlay)
    overlay.resize(1000, 800)
    overlay.show()
    _alert(overlay, text)

    budget = overlay._alert_budget_height()
    assert overlay._alert_viewport.height() <= budget
    # The whole label is inside the viewport, or it is crawling past it.
    label = overlay._center_text
    if not overlay.is_scrolling():
        assert label.y() >= 0
        assert label.y() + label.height() <= overlay._alert_viewport.height()


def _alert_layout_state(overlay: EventOverlayWindow) -> tuple:
    return (
        overlay._center_text.styleSheet(),
        overlay._alert_viewport.height(),
        overlay._center_text.height(),
        overlay.is_scrolling(),
        overlay._scroll_travel,
    )


@pytest.mark.parametrize(("grab", "push"), [("right", -260), ("right", 260), ("left", 200)])
def test_dragging_the_alerts_region_refits_the_live_headline_to_the_new_box(
    qtbot, grab, push
) -> None:
    """The headline must be laid out for the box the region has, not the one
    it just left: measuring before the hosts move clips a shrink and leaves a
    grown region crawling for overflow it no longer has."""
    state = _region_state()
    state.overlay_regions["alert"] = OverlayRegion(anchor="center", width=520, height=150)
    overlay = EventOverlayWindow(state=state)
    qtbot.addWidget(overlay)
    overlay.resize(1200, 800)
    overlay.show()
    overlay.set_edit_mode(True)
    overlay._layout_regions()
    _alert(overlay, PARAGRAPH)

    alert = overlay._alert_host
    y = alert.y() + alert.height() // 2
    x = alert.x() + alert.width() - 2 if grab == "right" else alert.x() + 1
    _press(overlay, x, y)
    assert overlay._region_resize_edges
    _move(overlay, x + push, y)

    # A second pass must change nothing: if the drag had measured against the
    # old geometry, re-fitting now would move it.
    settled = _alert_layout_state(overlay)
    overlay._restyle_alert()
    assert _alert_layout_state(overlay) == settled
    # And whatever it settled on still fits the region.
    assert overlay._alert_viewport.height() <= overlay._alert_budget_height()


def test_the_alerts_region_cannot_be_dragged_below_its_own_budget(qtbot) -> None:
    """`MIN_REGION_HEIGHT` is a bar/lane strip floor. Alerts needs room for
    the smallest headline plus its kicker and rule, or the budget has to
    overrun the region that is documented as exact."""
    state = _region_state()
    state.overlay_regions["alert"] = OverlayRegion(anchor="center", width=520, height=300)
    overlay = EventOverlayWindow(state=state)
    qtbot.addWidget(overlay)
    overlay.resize(1200, 800)
    overlay.show()
    overlay.set_edit_mode(True)
    overlay._layout_regions()

    floor = overlay._min_region_height("alert")
    assert floor >= MIN_ALERT_BUDGET + overlay._alert_chrome_height()
    assert overlay._min_region_height("bars") == MIN_REGION_HEIGHT  # unchanged

    alert = overlay._alert_host
    x = alert.x() + alert.width() // 2
    _press(overlay, x, alert.y() + alert.height() - 2)  # bottom edge
    _move(overlay, x, alert.y() - 5000)  # drag it into the ground

    assert state.overlay_regions["alert"].height == floor
    # At that floor the headline is still budgeted INSIDE the region.
    _alert(overlay, PARAGRAPH)
    assert overlay._alert_budget_height() >= MIN_ALERT_BUDGET
    assert overlay._alert_budget_height() + overlay._alert_chrome_height() <= floor


@pytest.mark.parametrize("height", [MIN_REGION_HEIGHT, 33, 48, 64])
def test_a_hand_written_short_alerts_region_never_overflows(qtbot, height) -> None:
    """The drag floor cannot police settings.json, which a user may edit by
    hand — so the budget itself has to stay inside the host either way."""
    state = _region_state()
    state.overlay_regions["alert"] = OverlayRegion(anchor="center", width=520, height=height)
    overlay = EventOverlayWindow(state=state)
    qtbot.addWidget(overlay)
    overlay.resize(1200, 800)
    overlay.show()
    _alert(overlay, PARAGRAPH)

    host_h = overlay._alert_host.height()
    assert overlay._alert_budget_height() + overlay._alert_chrome_height() <= host_h
    assert overlay._alert_viewport.height() <= host_h


def test_a_content_regions_height_is_a_floor_not_a_cap(qtbot) -> None:
    """A bars region dragged short must still grow for a fifth bar rather
    than swallowing it — only Alerts can fit its content to a box."""
    from nparseplus.core.events import TimerBarEvent

    region = OverlayRegion(anchor="bottom", height=MIN_REGION_HEIGHT)
    state = _region_state()
    state.overlay_regions["bars"] = region
    overlay = EventOverlayWindow(state=state)
    qtbot.addWidget(overlay)
    overlay.resize(1000, 800)
    overlay.show()
    assert overlay._bars_host.height() == MIN_REGION_HEIGHT

    for i in range(5):
        overlay.handle_event(TimerBarEvent(name=f"Bar {i}", total_seconds=60))
    qtbot.wait(1)  # let the bars layout settle so its size hint is real
    assert overlay._region_size("bars", region)[1] > MIN_REGION_HEIGHT
    overlay._layout_regions()
    assert overlay._bars_host.height() > MIN_REGION_HEIGHT

    # The Alerts region is the exception: its height is honored exactly,
    # because the headline is fitted into it rather than the other way round.
    alert = OverlayRegion(anchor="center", width=600, height=64)
    assert overlay._region_size("alert", alert)[1] == 64


def test_hovering_a_region_edge_shows_the_resize_cursor(qtbot) -> None:
    overlay = EventOverlayWindow(state=_region_state())
    qtbot.addWidget(overlay)
    overlay.set_edit_mode(True)
    overlay._layout_regions()

    alert = overlay._alert_host
    overlay._update_region_cursor(QPoint(alert.x() + 1, alert.y() + alert.height() // 2))
    assert overlay.cursor().shape() == Qt.CursorShape.SizeHorCursor
    overlay._update_region_cursor(QPoint(alert.x() + alert.width() // 2, alert.y() + 1))
    assert overlay.cursor().shape() == Qt.CursorShape.SizeVerCursor
    # Off every region: no resize cursor.
    overlay._update_region_cursor(QPoint(overlay.width() - 2, 5))
    assert overlay.cursor().shape() == Qt.CursorShape.ArrowCursor


# -- trigger text is never markup ------------------------------------------------


def test_every_label_that_shows_event_text_is_plain_text(qtbot) -> None:
    """Qt's AutoText heuristic renders a string opening with a known HTML tag
    name as rich text, which would eat "<b>" (and "&lt;") out of a trigger's
    display text. Trigger text is user data, never markup."""
    from PySide6.QtWidgets import QLabel

    overlay = EventOverlayWindow()
    qtbot.addWidget(overlay)
    overlay.resize(1000, 800)
    overlay.show()
    _alert(overlay, "Gorenaire — <b>ENRAGED</b>")
    overlay.handle_event(OverlayEvent(text="Rebuff: <i>Clarity</i>", section="utility"))
    overlay.handle_event(
        __import__("nparseplus.core.events", fromlist=["TimerBarEvent"]).TimerBarEvent(
            name="<p>Sand Giant</p>", total_seconds=60
        )
    )

    labels = [overlay._center_text, overlay._alert_kicker]
    labels += list(overlay._utility_lines.values())
    bar = overlay._bars["<p>Sand Giant</p>"].widget
    labels += [bar.property("name_label"), bar.property("value_label")]
    for label in labels:
        assert isinstance(label, QLabel)
        assert label.textFormat() == Qt.TextFormat.PlainText, label.objectName()

    # And it survives the round trip: the tag is still in the text.
    assert overlay._center_text.text() == "<b>ENRAGED</b>"
    assert overlay.current_utility_texts() == ["Rebuff: <i>Clarity</i>"]


# -- #107: the alert is actually on screen --------------------------------------
#
# Every widget assertion above this line is about HEIGHT or TEXT. #103 laid the
# viewport out zero pixels wide on every alert on every path, and not one of
# them noticed, because ``_place_label`` set the label's height itself and
# floored its width at one pixel. So these ask the two questions that were
# never asked: how wide is it, and does anything reach the screen.


def _painted_columns(widget) -> tuple[int, int, int]:
    """(first x, last x, count) of the alert-colored pixels a widget renders.

    ``grab()`` renders the widget and its children exactly as a paint event
    would, so this is the closest a test gets to "the user can see it". The
    alerts here are fired ``foreground="Red"``; the overlay's own chrome is
    tan/gold/grey, so red-dominant pixels are the headline and the headline
    only.
    """
    image = widget.grab().toImage()
    first, last, count = image.width(), -1, 0
    for y in range(image.height()):
        for x in range(image.width()):
            color = QColor(image.pixel(x, y))
            red = color.red()
            if red > 90 and red > color.green() + 40 and red > color.blue() + 40:
                first, last, count = min(first, x), max(last, x), count + 1
    return first, last, count


def _settle() -> None:
    """Deliver the pending LayoutRequests, without waiting on a clock.

    Every assertion below is about a real layout result rather than a size
    hint — which is the entire point, since the hints were the part that lied
    (#107) — and a QVBoxLayout re-lays out only when the LayoutRequest posted
    to its widget is delivered. The app has an event loop; a test has to pump
    one. ``qtbot.wait(1)`` does pump it, but a millisecond of wall clock is a
    race under a loaded full-suite run, and ``layout().activate()`` alone is
    not enough — the request has not reached the widget yet. Draining the
    posted queue is the part that is actually deterministic.
    """
    for _ in range(3):
        QApplication.sendPostedEvents()
        QApplication.processEvents()


def _shown(qtbot, state=None, size=(1200, 800)) -> EventOverlayWindow:
    overlay = EventOverlayWindow(state=state)
    qtbot.addWidget(overlay)
    overlay.resize(*size)
    overlay.show()
    _settle()
    return overlay


def _fire(qtbot, overlay: EventOverlayWindow, text: str) -> None:
    _alert(overlay, text)
    _settle()


@pytest.mark.parametrize(
    ("label", "text"),
    [
        # Unsplit text — most triggers, and what showed literally nothing.
        ("plain", "ENRAGED"),
        # Splits: the kicker is a real QLabel and kept rendering, so the user
        # saw the tiny gold cap and no headline under it.
        ("kicker", "Gorenaire — ENRAGED"),
        # Long enough to overflow and crawl.
        ("paragraph", PARAGRAPH),
    ],
)
@pytest.mark.parametrize("region_mode", [False, True])
def test_a_fired_alert_paints_pixels(qtbot, label, text, region_mode) -> None:
    """The whole bug in one assertion: firing an alert must put something on
    the screen. Nothing else in the suite renders anything (#107)."""
    state = _region_state() if region_mode else WindowState()
    overlay = _shown(qtbot, state)

    assert _painted_columns(overlay)[2] == 0  # nothing before the alert
    _fire(qtbot, overlay, text)
    first, last, count = _painted_columns(overlay)

    assert count > 0, f"{label} alert rendered no pixels"
    # And it is spread across the window, not crushed into a 1px column.
    assert last - first > 20


@pytest.mark.parametrize("region_mode", [False, True])
def test_the_headline_spans_its_host_and_stays_inside_the_window(qtbot, region_mode) -> None:
    """The acceptance criterion: a real width, in both layout modes.

    Measured on master: ``viewport QRect(580, 15, 0, 98)`` and
    ``center_text QRect(0, 30, 1, 38)`` — a legal-looking label one pixel wide
    inside a parent that clips it.
    """
    state = _region_state() if region_mode else WindowState()
    overlay = _shown(qtbot, state)
    _fire(qtbot, overlay, "ENRAGED")

    host = overlay._alert_host
    viewport = overlay._alert_viewport
    label = overlay._center_text

    assert host.width() > 0
    # The invariant ``_measure_headline`` rests on when it measures against the
    # host: the viewport IS the host's full-width layout item. It said so in a
    # comment while being 0 wide.
    assert viewport.width() == host.width()
    assert label.width() == viewport.width()
    assert label.width() > 0

    # ...and the label's rect really lands inside the window.
    top_left = label.mapTo(overlay, QPoint(0, 0))
    assert top_left.x() >= 0
    assert top_left.x() + label.width() <= overlay.width()


def test_the_viewport_carries_no_horizontal_alignment(qtbot) -> None:
    """The mechanism itself, pinned. Qt clamps an aligned layout item to its
    size hint width; a bare QWidget's is 0. The kicker and rule keep the flag
    (they have real hints and are narrower than the panel)."""
    overlay = EventOverlayWindow()
    qtbot.addWidget(overlay)
    layout = overlay._alert_layout

    viewport_item = layout.itemAt(layout.indexOf(overlay._alert_viewport))
    assert not bool(viewport_item.alignment() & Qt.AlignmentFlag.AlignHorizontal_Mask)
    for widget in (overlay._alert_kicker, overlay._alert_rule):
        item = layout.itemAt(layout.indexOf(widget))
        assert bool(item.alignment() & Qt.AlignmentFlag.AlignHCenter)


def test_the_viewport_reports_a_real_width_hint(qtbot) -> None:
    """The second lock: even if something re-aligns that item, the hint it
    would be clamped to is a width, not a zero."""
    overlay = EventOverlayWindow()
    qtbot.addWidget(overlay)
    assert overlay._alert_viewport.sizeHint().width() >= MIN_REGION_WIDTH


def test_the_label_is_not_floored_to_one_pixel(qtbot) -> None:
    """``max(1, self.width())`` is what made a zero-width viewport look legal
    to every height-and-text assertion in this file."""
    overlay = _shown(qtbot)
    _fire(qtbot, overlay, "ENRAGED")

    overlay._alert_viewport.setFixedWidth(0)
    overlay._alert_viewport._place_label()
    assert overlay._center_text.width() == 0  # a zero shows as a zero


# -- #107, cause 2: the minimum alert budget is guaranteed ----------------------


def test_the_floor_is_computed_against_worst_case_chrome(qtbot) -> None:
    """The floor is evaluated in position mode, where no alert is live and the
    kicker is hidden — so it has to charge for the kicker anyway.

    Measured on master: floor 47 (chrome 7), and a live "Gorenaire — ENRAGED"
    then made the chrome 18 and the budget 29 against MIN_ALERT_BUDGET 40.
    """
    overlay = _shown(qtbot)

    idle_floor = overlay._min_region_height("alert")
    assert idle_floor >= MIN_ALERT_BUDGET + overlay._alert_chrome_height(worst_case=True)

    _fire(qtbot, overlay, "Gorenaire — ENRAGED")
    assert not overlay._alert_kicker.isHidden()  # the expensive case is live
    # The floor did not move when the kicker appeared: it already paid for it.
    assert overlay._min_region_height("alert") == idle_floor
    assert idle_floor >= MIN_ALERT_BUDGET + overlay._alert_chrome_height()


def test_the_minimum_budget_holds_for_a_headline_with_a_kicker(qtbot) -> None:
    """The lower-bound tests above use kicker-free text, which is how an 11px
    shortfall shipped. At the smallest region the UI permits, WITH a kicker,
    the promised budget is still there."""
    floor = _shown(qtbot)._min_region_height("alert")

    state = _region_state()
    state.overlay_regions["alert"] = OverlayRegion(anchor="center", width=520, height=floor)
    overlay = _shown(qtbot, state)
    _fire(qtbot, overlay, "Gorenaire — ENRAGED")

    assert overlay._alert_budget_height() >= MIN_ALERT_BUDGET
    # ...and the region still honors the drawn box exactly.
    assert overlay._alert_budget_height() + overlay._alert_chrome_height() <= floor
    # ...and something is on screen at that size.
    assert _painted_columns(overlay)[2] > 0


@pytest.mark.parametrize("stored", [MIN_REGION_HEIGHT, 33, 40])
def test_a_region_loaded_below_the_floor_is_clamped_up(qtbot, stored) -> None:
    """The drag floor cannot police a settings.json written before it existed,
    edited by hand, or saved at a smaller font. ``max(1, ...)`` in the budget
    is the terminal form of that: a 1px viewport (#107)."""
    state = _region_state()
    state.overlay_regions["alert"] = OverlayRegion(anchor="center", width=520, height=stored)
    overlay = _shown(qtbot, state)

    floor = overlay._min_region_height("alert")
    assert stored < floor  # the case under test
    assert state.overlay_regions["alert"].height == floor
    _fire(qtbot, overlay, "Gorenaire — ENRAGED")
    assert overlay._alert_budget_height() >= MIN_ALERT_BUDGET
    assert _painted_columns(overlay)[2] > 0


def test_a_region_above_the_floor_is_left_exactly_as_drawn(qtbot) -> None:
    """The clamp only ever widens, and only what it must: the drawn box is
    still the contract everywhere else."""
    state = _region_state()
    state.overlay_regions["alert"] = OverlayRegion(anchor="center", width=520, height=300)
    overlay = _shown(qtbot, state)

    assert state.overlay_regions["alert"].height == 300
    assert overlay._alert_host.height() == 300


# -- #107: #102/#103 are not regressed ------------------------------------------


def test_a_long_alert_still_scrolls_the_whole_way(qtbot) -> None:
    """#103's crawl, re-asserted now that the viewport has a real width —
    which is what the overflow was always supposed to be measured against.

    Completion, not a deadline. ``alert_scroll_speed`` is capped at a
    readable 2.5 lines/second and #103 is explicit that for an alert far
    longer than its display time the readable rate wins and the remedy is a
    longer duration or a taller region. Whether THIS paragraph fits in four
    seconds is a question about a platform's font metrics, so the deadline is
    pinned on the pure function instead, where it is not.
    """
    overlay = EventOverlayWindow(clear_after_s=4.0)
    qtbot.addWidget(overlay)
    overlay.resize(400, 200)
    overlay.show()
    _fire(qtbot, overlay, PARAGRAPH)

    assert overlay.is_scrolling()
    travel, speed = overlay._scroll_travel, overlay._scroll_speed
    assert travel > 0 and speed > 0
    budget = overlay._scroll_hold_ticks + ceil(travel / (speed * SCROLL_TICK_MS / 1000)) + 2
    for _ in range(budget):
        if not overlay.is_scrolling():
            break
        overlay._advance_scroll()

    assert not overlay.is_scrolling()
    assert overlay.scroll_offset() == pytest.approx(travel)
    # The reset match still sees the whole string, split or not.
    assert overlay.current_text() == PARAGRAPH


def test_splitting_still_only_fires_on_a_short_head(qtbot) -> None:
    """The reporter's line from #102 must still be one headline at one size."""
    overlay = _shown(qtbot)
    _fire(qtbot, overlay, REPORTED_ALERT)

    assert overlay._alert_kicker.isHidden()
    assert overlay._center_text.text() == REPORTED_ALERT


def test_raising_the_base_font_re_clamps_the_region_live(qtbot) -> None:
    """The floor is font-relative — kicker height plus rule — so a region that
    was legal when it was drawn goes under it the moment Settings raises the
    base font, live and with nothing to tell the user. Only ever grows."""
    state = _region_state()
    small = _shown(qtbot)
    state.overlay_regions["alert"] = OverlayRegion(
        anchor="center", width=520, height=small._min_region_height("alert")
    )
    overlay = _shown(qtbot, state)
    drawn = state.overlay_regions["alert"].height

    overlay.apply_skin(font_size=40)
    qtbot.wait(1)
    grown = state.overlay_regions["alert"].height

    assert grown > drawn  # the case under test: the old box is now too short
    assert grown == overlay._min_region_height("alert")
    _fire(qtbot, overlay, "Gorenaire — ENRAGED")
    assert overlay._alert_budget_height() >= MIN_ALERT_BUDGET
    assert _painted_columns(overlay)[2] > 0


def test_the_kicker_height_charged_covers_a_populated_kicker(qtbot) -> None:
    """A QLabel's height depends on whether it holds text at all, and by how
    much is platform-specific.

    CI measured the populated kicker one pixel TALLER than the empty one on
    Linux and two pixels SHORTER on Windows, while macOS reports the same
    number either way. The floor is computed while the kicker is empty, so
    charging that measurement under-charged by a pixel on Linux and the
    "guaranteed" budget came out at 39 against a MIN_ALERT_BUDGET of 40 —
    #107's own defect one pixel wide instead of eleven, and just as
    invisible from a Mac.
    """
    overlay = _shown(qtbot)
    charged = overlay._kicker_worst_height
    assert charged >= overlay._alert_kicker.sizeHint().height()  # empty
    assert overlay._alert_kicker.text() == ""  # the probe put it back

    _fire(qtbot, overlay, "Gorenaire — ENRAGED")
    assert not overlay._alert_kicker.isHidden()
    assert charged >= overlay._alert_kicker.sizeHint().height()  # populated
    # Which is the whole point: worst case really is the worst case, so the
    # floor does not move under a live alert.
    assert overlay._alert_chrome_height(worst_case=True) >= overlay._alert_chrome_height()
