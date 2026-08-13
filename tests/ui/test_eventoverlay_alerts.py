"""Event-overlay alert presentation (#102).

The report was that part of a long trigger "Display text" turned into small
gold caps for no reason the user could see. The rules that decide how alert
text is shown are pure functions, tested here without a window wherever that
is possible, plus the widget-level checks that they are wired to what the
user actually sees.
"""

from datetime import datetime

import pytest
from PySide6.QtCore import Qt

from nparseplus.core.events import OverlayEvent, TimerBarEvent
from nparseplus.ui.eventoverlay import (
    KICKER_MAX_CHARS,
    EventOverlayWindow,
    first_alert_separator,
    looks_like_a_kicker,
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


def _alert(overlay: EventOverlayWindow, text: str) -> None:
    overlay.handle_event(
        OverlayEvent(timestamp=datetime.now(), line="", line_number=1, text=text, foreground="Red")
    )


# -- what may become a kicker ---------------------------------------------------


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
    overlay.handle_event(TimerBarEvent(name="<p>Sand Giant</p>", total_seconds=60))

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
