"""Session Best/Current/Last stats — PlayerDamage semantics."""

from collections.abc import Callable
from datetime import datetime, timedelta

from nparseplus.core.dps import FightTracker, PlayerDamage
from nparseplus.core.events import DamageEvent


def test_short_fights_do_not_count(
    tracker: FightTracker, hit: Callable[..., DamageEvent], t0: datetime
) -> None:
    tracker.add_damage(hit("You", "a gnoll", 500))
    tracker.end_fight("a gnoll", t0 + timedelta(seconds=10))  # 10s <= 20s floor
    summary = tracker.session_summary()
    assert summary.current_session == PlayerDamage()
    assert summary.best == PlayerDamage()


def test_fight_end_rolls_your_stats_into_best_and_current(
    tracker: FightTracker, hit: Callable[..., DamageEvent], t0: datetime
) -> None:
    tracker.add_damage(hit("You", "a gnoll", 100))
    tracker.add_damage(hit("You", "a gnoll", 200, offset_s=6))
    tracker.add_damage(hit("You", "a gnoll", 60, offset_s=25))
    # Someone else's damage never feeds your session stats.
    tracker.add_damage(hit("Vebanab", "a gnoll", 9999, offset_s=25))
    tracker.end_fight("a gnoll", t0 + timedelta(seconds=25))
    summary = tracker.session_summary()
    assert summary.current_session.total_damage == 360
    assert summary.current_session.highest_hit == 200
    # Trailing window at t+25 holds only the 60-damage hit: 60 / 12 = 5.
    assert summary.current_session.highest_dps == 5
    assert summary.best == summary.current_session


def test_tick_also_updates_session_stats_mid_fight(
    tracker: FightTracker, hit: Callable[..., DamageEvent], t0: datetime
) -> None:
    tracker.add_damage(hit("You", "a gnoll", 100))
    tracker.add_damage(hit("You", "a gnoll", 140, offset_s=21))
    tracker.tick(t0 + timedelta(seconds=21))
    summary = tracker.session_summary()
    assert summary.current_session.total_damage == 240
    assert summary.current_session.highest_dps == 11  # 140 / 12


def test_bogus_32000_hits_never_become_highest_hit(
    tracker: FightTracker, hit: Callable[..., DamageEvent], t0: datetime
) -> None:
    tracker.add_damage(hit("You", "a gnoll", 32000))
    tracker.add_damage(hit("You", "a gnoll", 150, offset_s=21))
    tracker.end_fight("a gnoll", t0 + timedelta(seconds=21))
    summary = tracker.session_summary()
    assert summary.current_session.highest_hit == 0


def test_end_session_moves_current_to_last(
    tracker: FightTracker, hit: Callable[..., DamageEvent], t0: datetime
) -> None:
    tracker.add_damage(hit("You", "a gnoll", 300))
    tracker.add_damage(hit("You", "a gnoll", 300, offset_s=21))
    tracker.end_fight("a gnoll", t0 + timedelta(seconds=21))
    before = tracker.session_summary()
    assert before.last_session is None

    tracker.end_session()
    after = tracker.session_summary()
    assert after.last_session == before.current_session
    assert after.current_session == PlayerDamage()
    assert after.best == before.best  # best survives session rollover

    tracker.remove_last_session()
    assert tracker.session_summary().last_session is None


def test_session_summary_returns_copies(
    tracker: FightTracker, hit: Callable[..., DamageEvent], t0: datetime
) -> None:
    tracker.add_damage(hit("You", "a gnoll", 300))
    tracker.add_damage(hit("You", "a gnoll", 300, offset_s=21))
    tracker.end_fight("a gnoll", t0 + timedelta(seconds=21))
    summary = tracker.session_summary()
    summary.current_session.total_damage = 0
    assert tracker.session_summary().current_session.total_damage == 600
