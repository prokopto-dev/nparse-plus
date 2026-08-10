"""The DPS meter's configurable counting rules (settings > DPS Meter)."""

from collections.abc import Callable
from datetime import datetime, timedelta

from nparseplus.core.damagetypes import MELEE_DAMAGE_TYPES, NON_MELEE_DAMAGE_TYPE, is_melee
from nparseplus.core.dps import FightTracker
from nparseplus.core.events import DamageEvent


def _damage(attacker: str, target: str, amount: int, dmg_type: str, when: datetime) -> DamageEvent:
    return DamageEvent(
        timestamp=when,
        target_name=target,
        attacker_name=attacker,
        damage_done=amount,
        damage_type=dmg_type,
        level_guess=None,
    )


# -- melee-only ---------------------------------------------------------------


def test_melee_only_is_the_default(tracker: FightTracker) -> None:
    assert tracker.melee_only is True


def test_every_verb_the_parser_emits_reads_as_melee() -> None:
    # Both conjugations: "You slash" and "Soandso slashes".
    for verb in ("slash", "slashes", "kick", "kicks", "punch", "backstabs", "crush", "bashes"):
        assert is_melee(verb), verb
    assert not is_melee(NON_MELEE_DAMAGE_TYPE)
    # An unknown type is NOT melee: a damage line the parser learns later must
    # not silently join a melee-only meter.
    assert not is_melee("dot-tick")
    assert NON_MELEE_DAMAGE_TYPE not in MELEE_DAMAGE_TYPES


def test_melee_only_drops_non_melee_damage(tracker: FightTracker, t0: datetime) -> None:
    tracker.add_damage(_damage("You", "a gnoll", 100, "slash", t0))
    tracker.add_damage(_damage("You", "a gnoll", 900, NON_MELEE_DAMAGE_TYPE, t0))
    rows = tracker.snapshot(t0)
    assert len(rows) == 1
    assert rows[0].total_damage == 100  # the nuke never landed in the row


def test_melee_only_does_not_open_a_fight_for_a_lone_nuke(
    tracker: FightTracker, t0: datetime
) -> None:
    tracker.add_damage(_damage("You", "a gnoll", 900, NON_MELEE_DAMAGE_TYPE, t0))
    assert tracker.fights == []


def test_melee_only_off_counts_non_melee(t0: datetime) -> None:
    tracker = FightTracker(melee_only=False)
    tracker.add_damage(_damage("You", "a gnoll", 100, "slash", t0))
    tracker.add_damage(_damage("You", "a gnoll", 900, NON_MELEE_DAMAGE_TYPE, t0))
    assert tracker.snapshot(t0)[0].total_damage == 1000


def test_misses_still_count_as_melee(tracker: FightTracker, t0: datetime) -> None:
    # A miss is a 0-damage melee event; it must still open/keep the row.
    tracker.add_damage(_damage("You", "a gnoll", 0, "slash", t0))
    assert [r.attacker_name for r in tracker.snapshot(t0)] == ["You"]


# -- attacker dropoff ---------------------------------------------------------


def test_retention_is_configurable(hit: Callable[..., DamageEvent], t0: datetime) -> None:
    tracker = FightTracker(fight_retention_s=120.0)
    tracker.add_damage(hit("You", "a gnoll", 10))
    tracker.tick(t0 + timedelta(seconds=120))
    assert len(tracker.fights) == 1
    tracker.tick(t0 + timedelta(seconds=121))
    assert tracker.fights == []


def test_zero_retention_never_retires(hit: Callable[..., DamageEvent], t0: datetime) -> None:
    tracker = FightTracker(fight_retention_s=0.0)
    tracker.add_damage(hit("You", "a gnoll", 10))
    tracker.tick(t0 + timedelta(hours=6))
    assert len(tracker.fights) == 1
    # ...but zoning still clears it.
    tracker.clear()
    assert tracker.fights == []


# -- averaging window ---------------------------------------------------------


def test_trailing_window_divides_by_the_configured_span(t0: datetime) -> None:
    for window, expected in ((12.0, 33), (4.0, 100), (1.0, 400)):
        tracker = FightTracker(trailing_window_s=window)
        tracker.add_damage(_damage("You", "a gnoll", 200, "slash", t0))
        tracker.add_damage(_damage("You", "a gnoll", 200, "slash", t0 + timedelta(seconds=0.5)))
        assert tracker.snapshot(t0)[0].dps == expected, window


def test_narrowing_the_window_invalidates_the_best_window(
    hit: Callable[..., DamageEvent], t0: datetime
) -> None:
    # best_window_damage measured over 12s is not comparable to one measured
    # over 2s; the max-merge must not carry the wider number forward.
    tracker = FightTracker(trailing_window_s=12.0)
    for offset in range(0, 10):
        tracker.add_damage(hit("You", "a gnoll", 100, offset_s=offset))
    entity = tracker.fights[0].entities["you"]
    assert entity.best_window_damage == 1000
    tracker.configure(trailing_window_s=2.0)
    tracker.add_damage(hit("You", "a gnoll", 100, offset_s=10))
    tracker.tick(t0 + timedelta(seconds=10))
    assert entity.best_window_damage < 1000


# -- session minimum ----------------------------------------------------------


def test_session_minimum_is_configurable(hit: Callable[..., DamageEvent], t0: datetime) -> None:
    # The default 20s gate keeps a 12s fight out of the footer...
    default = FightTracker()
    for offset in range(0, 13):
        default.add_damage(hit("You", "a gnoll", 100, offset_s=offset))
    default.tick(t0 + timedelta(seconds=12))
    assert default.session_summary().best.highest_dps == 0
    # ...lowering it lets the same fight count.
    lowered = FightTracker(session_min_fight_s=5.0)
    for offset in range(0, 13):
        lowered.add_damage(hit("You", "a gnoll", 100, offset_s=offset))
    lowered.tick(t0 + timedelta(seconds=12))
    assert lowered.session_summary().best.highest_dps > 0


# -- live reconfiguration -----------------------------------------------------


def test_configure_moves_the_knobs_and_notifies(tracker: FightTracker) -> None:
    calls: list[int] = []
    tracker.on_change.append(lambda: calls.append(1))
    tracker.configure(
        melee_only=False,
        fight_retention_s=90.0,
        trailing_window_s=6.0,
        session_min_fight_s=0.0,
    )
    assert (tracker.melee_only, tracker.fight_retention_s) == (False, 90.0)
    assert (tracker.trailing_window_s, tracker.session_min_fight_s) == (6.0, 0.0)
    assert tracker.trailing_window == timedelta(seconds=6)
    assert calls == [1]


def test_configure_reaches_fights_already_running(
    hit: Callable[..., DamageEvent], t0: datetime
) -> None:
    tracker = FightTracker(trailing_window_s=12.0)
    tracker.add_damage(hit("You", "a gnoll", 120))
    assert tracker.snapshot(t0)[0].dps == 10
    tracker.configure(trailing_window_s=4.0)
    tracker.tick(t0)  # the tick re-stamps every live entity
    assert tracker.snapshot(t0)[0].dps == 30


def test_configure_ignores_omitted_knobs(tracker: FightTracker) -> None:
    tracker.configure(melee_only=False)
    assert tracker.fight_retention_s == 40.0
    assert tracker.trailing_window_s == 12.0
