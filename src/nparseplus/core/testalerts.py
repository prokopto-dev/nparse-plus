"""Rehearse an alert instead of finding out when the dragon enrages (#85).

Port of EQTool's ``Services/SettingsTestRunOverlay.cs``, and the mechanism is
the part worth porting: it pushes **synthetic log lines through the real
parser**, so what you see is exactly what a real event produces rather than a
mock of it. Here that means ``LogPipeline.process`` — parser chain, bus, the
handler that speaks and publishes the ``OverlayEvent``, the overlay window.

The thread contract is not widened to do it. The bus and ``TimersService``
belong to the driver thread, so a button in a Qt window never calls
``process`` itself: :class:`AlertTestRunner` submits a closure through
``LogDriver.submit_to_driver`` — the inbox #45 built, drained at the one point
in ``_iterate`` immediately before a line is read — and the whole rehearsal
runs there, between two real lines and never inside one. (The issue predates
that inbox and asks for a new one; there is nothing left to build.)

Divergence from the C#: EQTool's ``RunTest`` **forces the alert's per-character
toggles on** before pushing. nParse+ honours whatever is configured, because
"does my alert fire, as I have set it up" is the question the button exists to
answer — an alert that only speaks in test mode has told you nothing. A toggle
changed but not yet applied is not live, so the settings page says so.

What a rehearsal is allowed to leave behind
-------------------------------------------

Firing the real path means real handlers run, and some of them keep state. The
rule here is that the *alert* may be visible and nothing else may survive:

* **Rows the test creates are stamped** ``owner=TEST_OWNER`` and cleared before
  the next fire and when the settings window closes, so they never accumulate,
  are always attributable, and are removable in one call. ``BaseRow.owner``
  and ``TimersService.remove_owner`` already exist for exactly this shape
  (a plugin's rows, #45).
* **They are never persisted.** ``export_respawn_timers`` skips owned rows, so
  a rehearsal cannot reach ``PlayerInfo.respawn_timers`` and come back after a
  restart as a row nothing owns.
* **Nothing else is touched.** Each sample below is chosen so the only state it
  produces is its own overlay text, its speech, and (for the two that have one)
  its own timer row. The audit is in each sample's ``leaves``.
* **A rehearsal never removes or edits a row it does not own.** Two real
  handlers would otherwise: the root-break line is also ``SpellTimerHandler``'s
  cue to drop a matching row, so ``_run`` puts back anything that went missing
  while the sample ran; and roll rows are grouped by their maximum alone, with
  ``add_roll`` resetting the window of the whole group, so the ``/random``
  sample picks a maximum no live group is using (:func:`free_roll_max`) and
  lands in a group of its own. The pipeline's
  own bookkeeping is the exception and is deliberate: a rehearsed line counts
  as a line, so it advances the line counter and the log clock, and the root
  break — which starts with "You" — counts as you being active. Pressing a
  button *is* you being active.

That rule is why **the death-loop alert is deliberately not offered**, though
EQTool has it. It fires on four of your own deaths, and your own death is one
of the most consequential lines in the log: ``CorpseWaypointHandler`` persists
a corpse marker to the map store *and* broadcasts a waypoint to everyone
sharing with you, ``PetHandler`` resets pet tracking, and ``FightTracker``
freezes every fight targeting you and folds it into the session stats. None of
those three is undoable, and a rehearsal that costs you your pet attribution
and tells the raid you died is not a rehearsal. The alert it would exercise —
red ``OverlayEvent`` plus TTS — is the same path ``root_break`` below covers.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from nparseplus.core.lineinfo import format_line
from nparseplus.core.pipeline import CommandSink, LogPipeline
from nparseplus.core.timers import YOU_GROUP, RollRow, TimersService

#: Stamped on every timer row a rehearsal creates. Not a plugin id and never
#: will be, so it cannot collide with one; ``remove_owner`` takes it back.
TEST_OWNER = "__test_alert__"

#: The made-up character every sample engages/rolls for. Deliberately not a
#: name anyone has: a rehearsal must read as one at a glance.
TEST_PLAYER = "Testcharacter"

#: Where the ``/random`` sample starts looking for a maximum to roll out of.
#: It walks up from here until it finds one no live roll group is using — see
#: :func:`free_roll_max`.
TEST_ROLL_MAX = 1000

#: The sample rolls, as (roller, result). Rendered at fire time because the
#: maximum they are out of is decided then.
SAMPLE_ROLLS = (("Testalpha", 742), ("Testbeta", 194), ("Testgamma", 908))


#: Builds a sample's lines when it fires. A callable rather than a fixed
#: tuple because one sample has to read the live timers before it can choose
#: what to push (:func:`free_roll_max`).
LineBuilder = Callable[[TimersService], tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class AlertSample:
    """One rehearsable alert: what to push, and what it leaves on screen."""

    key: str
    label: str
    #: What the button does, shown as its tooltip.
    blurb: str
    #: What survives the rehearsal, in the user's terms. Empty means nothing.
    leaves: str
    build_lines: LineBuilder


def _fixed(*lines: str) -> LineBuilder:
    """A sample whose lines never depend on what is on screen."""
    return lambda _timers: lines


def free_roll_max(timers: TimersService) -> int:
    """A ``/random`` maximum no live roll group is using.

    Roll rows are grouped by their maximum **alone**, and ``add_roll`` resets
    the window of every roll already in the group. A fixed maximum would
    therefore drop three fake rollers into the middle of somebody's real loot
    roll and push its expiry out — mutating rows the cleanup deliberately does
    not own and cannot restore. So the maximum is chosen against the live rows
    instead: the rehearsal always gets a group of its own. EQTool randomises
    the maximum per test; walking up from a fixed base is the same idea made
    deterministic, which is what the tests need.
    """
    live = {row.max_roll for row in timers.snapshot() if isinstance(row, RollRow)}
    maximum = TEST_ROLL_MAX
    while maximum in live:
        maximum += 1
    return maximum


def _roll(name: str, roll: int, maximum: int) -> tuple[str, str]:
    """The two lines one ``/random`` prints (RandomParser needs both)."""
    return (
        f"**A Magic Die is rolled by {name}.",
        f"**It could have been any number from 0 to {maximum}, "
        f"but this time it turned up a {roll}.",
    )


def _roll_lines(timers: TimersService) -> tuple[str, ...]:
    maximum = free_roll_max(timers)
    lines: list[str] = []
    for name, roll in SAMPLE_ROLLS:
        lines.extend(_roll(name, roll, maximum))
    return tuple(lines)


SAMPLES: tuple[AlertSample, ...] = (
    AlertSample(
        key="fte",
        label="First to engage",
        blurb=(
            f"Pushes “a training dummy engages {TEST_PLAYER}!” — the yellow FTE "
            "banner and its callout, exactly as the raid's first engage prints it."
        ),
        leaves="",
        # "a training dummy" carries no raid engage rule, so no timer starts.
        build_lines=_fixed(f"a training dummy engages {TEST_PLAYER}!"),
    ),
    AlertSample(
        key="fte_timer",
        label="Engage rule timer",
        blurb=(
            f"Pushes “Zlandicar engages {TEST_PLAYER}!” — the same banner for a "
            "mob that has a raid engage rule, plus the “--97% Rule--” countdown "
            "it starts in the Timers window."
        ),
        leaves="a 61-second “--97% Rule-- Zlandicar” row in the Timers window",
        build_lines=_fixed(f"Zlandicar engages {TEST_PLAYER}!"),
    ),
    AlertSample(
        key="root_break",
        label="Root break",
        blurb=(
            "Pushes “Your Paralyzing Earth spell has worn off.” — the red "
            "root-break alert. Honours the two root-break toggles above."
        ),
        # SpellTimerHandler answers the same line with
        # try_remove_unambiguous_other. That would take a real Paralyzing Earth
        # row off another target, so the runner puts back anything a rehearsal
        # removes (see _run).
        leaves="",
        build_lines=_fixed("Your Paralyzing Earth spell has worn off."),
    ),
    AlertSample(
        key="random_roll",
        label="/random rolls",
        blurb=(
            "Pushes three sample rolls — the roll group as the Timers window "
            "draws it, highest first. The maximum they are rolled out of is "
            "picked so the group is the rehearsal's own, never a real roll's."
        ),
        leaves="three sample rolls in a “ Random -- …” group of their own",
        build_lines=_roll_lines,
    ),
)

SAMPLES_BY_KEY: dict[str, AlertSample] = {sample.key: sample for sample in SAMPLES}


def _eq_group(a: str, b: str) -> bool:
    """Group names compare the way ``TimersService`` compares them."""
    return a.casefold() == b.casefold()


class AlertTestRunner:
    """Fires a sample down the real pipeline, on the driver thread.

    Qt-free and injected into the settings window the way ``ZoneDatabase`` and
    ``SocialSyncWatcher`` already are; ``build_backend`` owns the one instance.
    """

    def __init__(
        self,
        pipeline: LogPipeline,
        timers: TimersService,
        submit: CommandSink,
    ) -> None:
        self._pipeline = pipeline
        self._timers = timers
        self._submit = submit

    def fire(self, key: str) -> bool:
        """Queue the rehearsal named ``key``; False if there is no such sample.

        Returns as soon as the closure is queued — the alert lands on the next
        driver iteration (within the 100 ms poll), not before this returns.
        """
        sample = SAMPLES_BY_KEY.get(key)
        if sample is None:
            return False
        self._submit(lambda: self._run(sample), label=f"test alert {sample.key}")
        return True

    def clear(self) -> None:
        """Take back whatever the last rehearsal left in the Timers window."""
        self._submit(self._clear_now, label="clear test alert rows")

    # -- driver thread ---------------------------------------------------------

    def _clear_now(self) -> None:
        self._timers.remove_owner(TEST_OWNER)

    def _run(self, sample: AlertSample) -> None:
        # Last rehearsal first, so at most one test's rows are ever on screen.
        self._clear_now()
        before = self._timers.snapshot()
        known = {id(row) for row in before}
        when = datetime.now()
        for message in sample.build_lines(self._timers):
            self._pipeline.process(format_line(message, when))
        after = self._timers.snapshot()

        # A rehearsal may ADD rows; it may never take one away. The root-break
        # line is also SpellTimerHandler's cue to drop a matching row, so
        # pressing that button while one real Paralyzing Earth is up on a mob
        # would delete it — and cleanup afterwards can only remove rows, never
        # find a removed one again. Put back whatever went missing while the
        # sample ran, with the state it had.
        present = {id(row) for row in after}
        self._timers.reinstate([row for row in before if id(row) not in present])

        # Stamped after the fact rather than by the handlers, which know
        # nothing about rehearsals and must not: nothing else runs on this
        # thread inside one command, so anything new is this sample's.
        #
        # Except a YOU_GROUP row, which is never a rehearsal's to take back.
        # No sample can produce one — the engage-rule timer is a mob timer and
        # rolls have their own group — but a synthetic line is still a LineEvent,
        # and ``SpellTimerHandler`` finalizes a self-spell that prints no
        # completion message on whatever line arrives after its cast time. Cast
        # Harmshield and press a button inside the same second and that row is
        # yours, not the test's; stamping it would delete a real buff.
        for row in after:
            if id(row) not in known and not _eq_group(row.group, YOU_GROUP):
                row.owner = TEST_OWNER
