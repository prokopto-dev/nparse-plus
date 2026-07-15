"""FightTracker — the DPSWindowViewModel row lifecycle."""

from collections.abc import Callable
from datetime import datetime, timedelta

from nparseplus.core.dps import FightTracker
from nparseplus.core.events import DamageEvent


def test_damage_creates_fight_and_row(
    tracker: FightTracker, hit: Callable[..., DamageEvent], t0: datetime
) -> None:
    tracker.add_damage(hit("You", "a gnoll", 25))
    rows = tracker.snapshot(t0)
    assert len(rows) == 1
    row = rows[0]
    assert row.target_name == "a gnoll"
    assert row.attacker_name == "You"
    assert row.is_your_damage
    assert row.total_damage == 25
    assert row.highest_hit == 25
    assert not row.is_dead


def test_self_damage_is_ignored(
    tracker: FightTracker, hit: Callable[..., DamageEvent], t0: datetime
) -> None:
    # Charmed pet with the same name as the NPC: attacker == target.
    tracker.add_damage(hit("a gnoll", "a gnoll", 25))
    assert tracker.snapshot(t0) == []


def test_attackers_group_under_one_target_with_percentages(
    tracker: FightTracker, hit: Callable[..., DamageEvent], t0: datetime
) -> None:
    tracker.add_damage(hit("You", "a gnoll", 75))
    tracker.add_damage(hit("Vebanab", "a gnoll", 25, offset_s=1))
    assert len(tracker.fights) == 1
    rows = tracker.snapshot(t0 + timedelta(seconds=1))
    # Sorted by total damage descending within the target group.
    assert [r.attacker_name for r in rows] == ["You", "Vebanab"]
    assert all(r.target_total_damage == 100 for r in rows)
    assert [r.percent_of_total for r in rows] == [75, 25]


def test_separate_targets_are_separate_fights(
    tracker: FightTracker, hit: Callable[..., DamageEvent]
) -> None:
    tracker.add_damage(hit("You", "a gnoll", 10))
    tracker.add_damage(hit("You", "an orc pawn", 10))
    assert len(tracker.fights) == 2


def test_end_fight_marks_rows_dead_case_insensitively(
    tracker: FightTracker, hit: Callable[..., DamageEvent], t0: datetime
) -> None:
    tracker.add_damage(hit("You", "a gnoll", 10))
    assert tracker.end_fight("A Gnoll", t0 + timedelta(seconds=5))
    rows = tracker.snapshot(t0 + timedelta(seconds=5))
    assert rows[0].is_dead
    assert tracker.active_fight("a gnoll") is None
    # Ending an already-dead or unknown fight is a no-op.
    assert not tracker.end_fight("a gnoll", t0 + timedelta(seconds=6))
    assert not tracker.end_fight("", t0)


def test_damage_after_death_starts_a_new_fight(
    tracker: FightTracker, hit: Callable[..., DamageEvent], t0: datetime
) -> None:
    tracker.add_damage(hit("You", "a gnoll", 10))
    tracker.end_fight("a gnoll", t0 + timedelta(seconds=5))
    tracker.add_damage(hit("You", "a gnoll", 99, offset_s=10))
    fights = tracker.fights
    assert len(fights) == 2
    assert fights[0].is_dead and not fights[1].is_dead
    assert tracker.active_fight("a gnoll") is fights[1]


def test_misses_keep_the_fight_alive_without_damage(
    tracker: FightTracker, hit: Callable[..., DamageEvent], t0: datetime
) -> None:
    tracker.add_damage(hit("You", "a gnoll", 20))
    tracker.add_damage(hit("You", "a gnoll", 0, offset_s=35))  # a miss
    tracker.tick(t0 + timedelta(seconds=60))  # 25s after the miss
    assert len(tracker.fights) == 1
    row = tracker.snapshot(t0 + timedelta(seconds=60))[0]
    assert row.total_damage == 20
    assert row.highest_hit == 20


def test_tick_prunes_rows_stale_for_over_40_seconds(
    tracker: FightTracker, hit: Callable[..., DamageEvent], t0: datetime
) -> None:
    tracker.add_damage(hit("You", "a gnoll", 10))
    tracker.add_damage(hit("Vebanab", "a gnoll", 10, offset_s=30))
    tracker.tick(t0 + timedelta(seconds=45))
    # Your row (last damage t+0) aged out; Vebanab's (t+30) survived.
    rows = tracker.snapshot(t0 + timedelta(seconds=45))
    assert [r.attacker_name for r in rows] == ["Vebanab"]
    tracker.tick(t0 + timedelta(seconds=90))
    assert tracker.fights == []


def test_tick_refreshes_trailing_windows(
    tracker: FightTracker, hit: Callable[..., DamageEvent], t0: datetime
) -> None:
    tracker.add_damage(hit("You", "a gnoll", 120))
    assert tracker.snapshot(t0)[0].dps == 10
    tracker.tick(t0 + timedelta(seconds=20))
    assert tracker.snapshot(t0 + timedelta(seconds=20))[0].dps == 0


def test_level_guess_applies_to_fights_targeting_the_attacker(
    tracker: FightTracker, hit: Callable[..., DamageEvent], t0: datetime
) -> None:
    tracker.add_damage(hit("You", "a gnoll", 10))
    # The gnoll hits back hard enough to reveal its level.
    tracker.add_damage(hit("a gnoll", "Genartik", 40, offset_s=1, level_guess=20))
    by_target = {row.target_name: row for row in tracker.snapshot(t0 + timedelta(seconds=1))}
    # Both the gnoll's own attack row and your row *against* the gnoll learn it.
    assert by_target["a gnoll"].level == 20
    assert by_target["Genartik"].level == 20


def test_clear_drops_all_fights(
    tracker: FightTracker, hit: Callable[..., DamageEvent], t0: datetime
) -> None:
    tracker.add_damage(hit("You", "a gnoll", 10))
    tracker.add_damage(hit("You", "an orc pawn", 10))
    tracker.clear()
    assert tracker.fights == []
    assert tracker.snapshot(t0) == []


def test_on_change_fires_for_add_end_and_clear(
    tracker: FightTracker, hit: Callable[..., DamageEvent], t0: datetime
) -> None:
    calls: list[int] = []
    tracker.on_change.append(lambda: calls.append(1))
    tracker.add_damage(hit("You", "a gnoll", 10))
    assert len(calls) == 1
    tracker.end_fight("a gnoll", t0 + timedelta(seconds=1))
    assert len(calls) == 2
    tracker.clear()
    assert len(calls) == 3
    tracker.clear()  # already empty: no notification
    assert len(calls) == 3
