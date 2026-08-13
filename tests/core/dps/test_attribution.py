"""Non-melee attribution — issue #80.

The meter used to have no setting under which a caster's number was both
present and true: melee-only dropped every point of spell damage, and
counting it credited YOU with every ``was hit by non-melee`` line, other
players' nukes included. These tests pin the replacement: the damage is
credited to you when it lands inside the credit window of one of your own
casts, and never otherwise.
"""

from datetime import datetime, timedelta

import pytest

from nparseplus.core.bus import EventBus
from nparseplus.core.damagetypes import NON_MELEE_DAMAGE_TYPE
from nparseplus.core.dps import (
    SPELL_CREDIT_WINDOW_SECONDS,
    UNATTRIBUTED_SPELL_ATTACKER,
    YOU,
    FightTracker,
)
from nparseplus.core.enums import SpellBenefitDetriment
from nparseplus.core.events import (
    DamageEvent,
    YouBeginCastingEvent,
    YouFinishCastingEvent,
    YourSpellInterruptedEvent,
    YouZonedEvent,
)
from nparseplus.core.handlers.dps import DpsHandler
from nparseplus.core.player import ActivePlayer
from nparseplus.core.spells.models import Spell


def _nuke(target: str, amount: int, when: datetime) -> DamageEvent:
    """The line the parser cannot attribute: no attacker anywhere in it."""
    return DamageEvent(
        timestamp=when,
        line=f"{target} was hit by non-melee for {amount} points of damage.",
        target_name=target,
        attacker_name=YOU,  # what DamageParser.cs has to guess
        damage_done=amount,
        damage_type=NON_MELEE_DAMAGE_TYPE,
        level_guess=None,
    )


def _swing(attacker: str, target: str, amount: int, when: datetime) -> DamageEvent:
    return DamageEvent(
        timestamp=when,
        target_name=target,
        attacker_name=attacker,
        damage_done=amount,
        damage_type="slash",
        level_guess=None,
    )


def _spell(name: str, *, detrimental: bool = True, cast_ms: int = 0) -> Spell:
    return Spell(
        id=1,
        name=name,
        cast_time_ms=cast_ms,
        benefit_detriment=(
            SpellBenefitDetriment.DETRIMENTAL if detrimental else SpellBenefitDetriment.BENEFICIAL
        ),
    )


# -- the credit window --------------------------------------------------------


def test_a_nuke_after_your_cast_is_yours(tracker: FightTracker, t0: datetime) -> None:
    tracker.note_your_cast(t0)
    tracker.add_damage(_nuke("a gnoll", 900, t0 + timedelta(seconds=1)))
    rows = tracker.snapshot(t0)
    assert [(r.attacker_name, r.total_damage) for r in rows] == [(YOU, 900)]
    assert rows[0].is_your_damage is True


def test_a_caster_only_fight_produces_a_true_non_zero_row(
    tracker: FightTracker, t0: datetime
) -> None:
    """The headline case: a wizard who never swings still gets a number.

    Under the old melee-only default this row did not exist at all, and the
    session footer read zero all night.
    """
    for tick in range(0, 40, 4):
        when = t0 + timedelta(seconds=tick)
        tracker.note_your_cast(when)
        tracker.add_damage(_nuke("Gorenaire", 800, when + timedelta(seconds=1)))
    now = t0 + timedelta(seconds=40)
    tracker.tick(now)

    rows = tracker.snapshot(now)
    assert [r.attacker_name for r in rows] == [YOU]
    assert rows[0].total_damage == 8000
    assert rows[0].dps > 0
    # ...and it reaches the session footer, which is what "present" means.
    assert tracker.session_summary().best.highest_dps > 0


def test_a_cold_nuke_is_never_yours(tracker: FightTracker, t0: datetime) -> None:
    # Nobody cast anything: this is somebody else's spell landing in range.
    tracker.add_damage(_nuke("a gnoll", 900, t0))
    assert tracker.fights == []


def test_a_nuke_past_the_window_is_not_yours(tracker: FightTracker, t0: datetime) -> None:
    tracker.note_your_cast(t0)
    tracker.add_damage(_nuke("a gnoll", 900, t0 + timedelta(seconds=3)))
    assert tracker.fights == []


def test_the_window_boundary_is_inclusive(t0: datetime) -> None:
    """Exactly at the edge counts; one tick past it does not.

    Log timestamps have one-second resolution, so the boundary has to be
    inclusive or a 2 s window would really be a 1 s one.
    """
    edge = timedelta(seconds=SPELL_CREDIT_WINDOW_SECONDS)
    inside = FightTracker()
    inside.note_your_cast(t0)
    inside.add_damage(_nuke("a gnoll", 10, t0 + edge))
    assert len(inside.fights) == 1

    outside = FightTracker()
    outside.note_your_cast(t0)
    outside.add_damage(_nuke("a gnoll", 10, t0 + edge + timedelta(milliseconds=1)))
    assert outside.fights == []


def test_the_window_covers_the_whole_cast(t0: datetime) -> None:
    """Armed from the BEGIN line, the damage may land a cast time later.

    The begin line is the only one guaranteed to precede the damage: the
    landing message and the damage line share a log second, so their order
    cannot be relied on.
    """
    tracker = FightTracker()
    tracker.note_your_cast(t0, cast_time_s=4.15)  # Ice Shock
    tracker.add_damage(_nuke("a gnoll", 900, t0 + timedelta(seconds=5)))
    assert tracker.snapshot(t0)[0].attacker_name == YOU


def test_a_wider_window_is_configurable(t0: datetime) -> None:
    tracker = FightTracker(spell_credit_window_s=6.0)
    tracker.note_your_cast(t0)
    tracker.add_damage(_nuke("a gnoll", 900, t0 + timedelta(seconds=5)))
    assert tracker.snapshot(t0)[0].attacker_name == YOU


def test_arming_only_ever_moves_the_window_forward(t0: datetime) -> None:
    tracker = FightTracker()
    tracker.note_your_cast(t0, cast_time_s=8.0)
    tracker.note_your_cast(t0 + timedelta(seconds=1))  # a finish line, sooner
    tracker.add_damage(_nuke("a gnoll", 900, t0 + timedelta(seconds=9)))
    assert tracker.snapshot(t0)[0].attacker_name == YOU


def test_zoning_disarms_the_window(t0: datetime) -> None:
    # Zoning cancels the cast; the first nuke on the other side is not yours.
    tracker = FightTracker()
    tracker.note_your_cast(t0, cast_time_s=10.0)
    tracker.clear()
    tracker.add_damage(_nuke("a gnoll", 900, t0 + timedelta(seconds=1)))
    assert tracker.fights == []


# -- the three modes ----------------------------------------------------------


def test_melee_mode_ignores_the_credit_window(t0: datetime) -> None:
    tracker = FightTracker(damage_sources="melee")
    tracker.note_your_cast(t0)
    tracker.add_damage(_nuke("a gnoll", 900, t0))
    assert tracker.fights == []


def test_all_parks_an_unattributed_nuke_on_a_pseudo_attacker(t0: datetime) -> None:
    """The group total stays right without the meter claiming the damage."""
    tracker = FightTracker(damage_sources="all")
    tracker.add_damage(_swing(YOU, "a gnoll", 100, t0))
    tracker.add_damage(_nuke("a gnoll", 900, t0))
    rows = {row.attacker_name: row for row in tracker.snapshot(t0)}
    assert rows[YOU].total_damage == 100
    assert rows[UNATTRIBUTED_SPELL_ATTACKER].total_damage == 900
    assert rows[UNATTRIBUTED_SPELL_ATTACKER].is_your_damage is False
    assert rows[YOU].target_total_damage == 1000


def test_all_still_credits_your_own_cast(t0: datetime) -> None:
    tracker = FightTracker(damage_sources="all")
    tracker.note_your_cast(t0)
    tracker.add_damage(_nuke("a gnoll", 900, t0))
    assert [r.attacker_name for r in tracker.snapshot(t0)] == [YOU]


@pytest.mark.parametrize("mode", ["melee", "melee+mine", "all"])
def test_every_mode_counts_melee(mode: str, t0: datetime) -> None:
    tracker = FightTracker(damage_sources=mode)
    tracker.add_damage(_swing(YOU, "a gnoll", 100, t0))
    assert tracker.snapshot(t0)[0].total_damage == 100


def test_a_named_non_melee_attacker_is_taken_at_its_word_under_all(t0: datetime) -> None:
    # Nothing in the app publishes one today; a plugin could.
    event = DamageEvent(
        timestamp=t0,
        target_name="a gnoll",
        attacker_name="Soandso",
        damage_done=500,
        damage_type=NON_MELEE_DAMAGE_TYPE,
    )
    permissive = FightTracker(damage_sources="all")
    permissive.add_damage(event)
    assert [r.attacker_name for r in permissive.snapshot(t0)] == ["Soandso"]

    # It is neither melee nor mine, so the middle mode drops it.
    mine_only = FightTracker(damage_sources="melee+mine")
    mine_only.add_damage(event)
    assert mine_only.fights == []


# -- the handler wiring -------------------------------------------------------


def _handler(bus: EventBus, tracker: FightTracker) -> DpsHandler:
    return DpsHandler(bus, ActivePlayer(name="Genartik"), tracker)


def test_a_finished_cast_arms_the_window(bus: EventBus, tracker: FightTracker, t0) -> None:
    _handler(bus, tracker)
    bus.publish(
        YouFinishCastingEvent(timestamp=t0, spell=_spell("Lightning Bolt"), target_name="a gnoll")
    )
    bus.publish(_nuke("a gnoll", 900, t0 + timedelta(seconds=1)))
    assert [r.attacker_name for r in tracker.snapshot(t0)] == [YOU]


def test_a_begun_cast_arms_the_window_for_its_cast_time(
    bus: EventBus, tracker: FightTracker, t0
) -> None:
    _handler(bus, tracker)
    bus.publish(YouBeginCastingEvent(timestamp=t0, spell=_spell("Ice Shock", cast_ms=4150)))
    bus.publish(_nuke("a gnoll", 900, t0 + timedelta(seconds=5)))
    assert [r.attacker_name for r in tracker.snapshot(t0)] == [YOU]


def test_a_beneficial_cast_never_arms_the_window(bus: EventBus, tracker: FightTracker, t0) -> None:
    """A cleric chain-healing must not collect the raid's spell damage."""
    _handler(bus, tracker)
    bus.publish(
        YouBeginCastingEvent(timestamp=t0, spell=_spell("Complete Heal", detrimental=False))
    )
    bus.publish(
        YouFinishCastingEvent(
            timestamp=t0, spell=_spell("Complete Heal", detrimental=False), target_name="Soandso"
        )
    )
    bus.publish(_nuke("Gorenaire", 900, t0))
    assert tracker.fights == []


def test_the_handler_disarms_on_zone(bus: EventBus, tracker: FightTracker, t0) -> None:
    _handler(bus, tracker)
    bus.publish(YouBeginCastingEvent(timestamp=t0, spell=_spell("Ice Shock", cast_ms=4150)))
    bus.publish(YouZonedEvent(timestamp=t0, long_name="Kael Drakkel", short_name="kael"))
    bus.publish(_nuke("a gnoll", 900, t0 + timedelta(seconds=1)))
    assert tracker.fights == []


# -- end to end, through the real parser chain --------------------------------


def test_a_wizards_log_lines_produce_a_true_row() -> None:
    """The whole chain: cast line -> spell book -> credit window -> row.

    The unit tests above stub the cast; this one proves the real parsers,
    the real spells_us.txt and the real handler wiring agree, which is where
    a heuristic like this actually fails.
    """
    from nparseplus.audio.tts import NullSpeaker
    from nparseplus.composition import build_backend
    from nparseplus.config.settings import Settings

    backend = build_backend(Settings(), speaker=NullSpeaker())
    for raw in (
        "[Wed Jul 15 12:00:00 2026] You begin casting Lightning Bolt.",
        "[Wed Jul 15 12:00:03 2026] a gnoll's body spasms as the lightning bolt arcs through them.",
        "[Wed Jul 15 12:00:03 2026] a gnoll was hit by non-melee for 300 points of damage.",
    ):
        backend.pipeline.process(raw)

    now = datetime(2026, 7, 15, 12, 0, 3)
    assert [(r.attacker_name, r.total_damage) for r in backend.fights.snapshot(now)] == [(YOU, 300)]


def test_a_nuke_with_no_cast_of_yours_is_not_yours_end_to_end() -> None:
    from nparseplus.audio.tts import NullSpeaker
    from nparseplus.composition import build_backend
    from nparseplus.config.settings import Settings

    backend = build_backend(Settings(), speaker=NullSpeaker())
    backend.pipeline.process(
        "[Wed Jul 15 12:00:03 2026] a gnoll was hit by non-melee for 300 points of damage."
    )
    assert backend.fights.fights == []


# -- the credit window applies live -------------------------------------------


def test_narrowing_the_window_retires_a_cast_already_armed(t0: datetime) -> None:
    """Apply means Apply, including to the cast in flight.

    The deadline used to be computed at arming time, so tightening the
    window mid-raid — the one situation the setting exists for — changed
    nothing until the next cast.
    """
    tracker = FightTracker(spell_credit_window_s=6.0)
    tracker.note_your_cast(t0)
    tracker.configure(spell_credit_window_s=1.0)
    tracker.add_damage(_nuke("a gnoll", 900, t0 + timedelta(seconds=5)))
    assert tracker.fights == []


def test_widening_the_window_reaches_a_cast_already_armed(t0: datetime) -> None:
    tracker = FightTracker(spell_credit_window_s=1.0)
    tracker.note_your_cast(t0)
    tracker.configure(spell_credit_window_s=6.0)
    tracker.add_damage(_nuke("a gnoll", 900, t0 + timedelta(seconds=5)))
    assert [r.attacker_name for r in tracker.snapshot(t0)] == [YOU]


def test_the_deadline_is_the_landing_plus_the_current_window(t0: datetime) -> None:
    tracker = FightTracker(spell_credit_window_s=2.0)
    assert tracker.credit_deadline is None
    tracker.note_your_cast(t0, cast_time_s=4.0)
    assert tracker.credit_deadline == t0 + timedelta(seconds=6)
    tracker.configure(spell_credit_window_s=0.5)
    assert tracker.credit_deadline == t0 + timedelta(seconds=4.5)
    tracker.clear()
    assert tracker.credit_deadline is None


# -- an interrupted cast disarms ----------------------------------------------


def test_cancel_your_cast_disarms_without_touching_fights(t0: datetime) -> None:
    """An interruption says nothing about the fights on screen."""
    tracker = FightTracker()
    tracker.add_damage(_swing(YOU, "a gnoll", 100, t0))
    tracker.note_your_cast(t0, cast_time_s=8.0)
    tracker.cancel_your_cast()
    assert tracker.credit_deadline is None
    assert len(tracker.fights) == 1  # NOT clear()
    assert tracker.snapshot(t0)[0].total_damage == 100


def test_an_interrupted_cast_stops_collecting_other_peoples_nukes(
    bus: EventBus, tracker: FightTracker, t0
) -> None:
    """begin -> interrupted -> non-melee: the span is no longer yours.

    Arming from the begin line holds the window open for the whole cast
    time, so an 8 s cast interrupted a second in used to keep crediting you
    for another nine seconds.
    """
    _handler(bus, tracker)
    bus.publish(YouBeginCastingEvent(timestamp=t0, spell=_spell("Ice Shock", cast_ms=8000)))
    bus.publish(YourSpellInterruptedEvent(timestamp=t0 + timedelta(seconds=1)))
    bus.publish(_nuke("Gorenaire", 900, t0 + timedelta(seconds=5)))
    assert tracker.fights == []


def test_an_uninterrupted_cast_of_the_same_shape_still_credits(
    bus: EventBus, tracker: FightTracker, t0
) -> None:
    # Guards the test above against passing for the wrong reason.
    _handler(bus, tracker)
    bus.publish(YouBeginCastingEvent(timestamp=t0, spell=_spell("Ice Shock", cast_ms=8000)))
    bus.publish(_nuke("Gorenaire", 900, t0 + timedelta(seconds=5)))
    assert [r.attacker_name for r in tracker.snapshot(t0)] == [YOU]


def test_an_interruption_end_to_end() -> None:
    """Through the real parser: the interrupt line is one nParse+ already reads."""
    from nparseplus.audio.tts import NullSpeaker
    from nparseplus.composition import build_backend
    from nparseplus.config.settings import Settings

    backend = build_backend(Settings(), speaker=NullSpeaker())
    backend.pipeline.process("[Wed Jul 15 12:00:00 2026] You begin casting Ice Shock.")
    # Asserted so this cannot start passing because nothing armed at all.
    deadline = backend.fights.credit_deadline
    assert deadline is not None and deadline > datetime(2026, 7, 15, 12, 0, 4)

    backend.pipeline.process("[Wed Jul 15 12:00:01 2026] Your spell is interrupted.")
    assert backend.fights.credit_deadline is None

    backend.pipeline.process(
        "[Wed Jul 15 12:00:04 2026] a gnoll was hit by non-melee for 300 points of damage."
    )
    assert backend.fights.fights == []
