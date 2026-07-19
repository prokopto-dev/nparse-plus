"""FightEntity — port of the EntittyDPS damage/window math."""

from datetime import datetime, timedelta

from nparseplus.core.dps import FightEntity

T0 = datetime(2026, 7, 8, 21, 0, 0)


def make_entity() -> FightEntity:
    return FightEntity(attacker_name="You", target_name="a gnoll", start_time=T0)


def test_add_damage_accumulates_totals_and_highest_hit() -> None:
    entity = make_entity()
    entity.add_damage(T0, 10)
    entity.add_damage(T0 + timedelta(seconds=2), 50)
    entity.add_damage(T0 + timedelta(seconds=4), 30)
    assert entity.total_damage == 90
    assert entity.highest_hit == 50


def test_trailing_window_drops_hits_older_than_12_seconds() -> None:
    entity = make_entity()
    entity.add_damage(T0, 100)
    entity.add_damage(T0 + timedelta(seconds=5), 40)
    entity.update_trailing(T0 + timedelta(seconds=13))
    # cutoff is t+1: the t+0 hit fell out, the t+5 hit remains.
    assert entity.trailing_damage == 40
    entity.update_trailing(T0 + timedelta(seconds=30))
    assert entity.trailing_damage == 0
    assert entity.trailing_dps == 0


def test_trailing_dps_is_window_damage_over_12_seconds() -> None:
    entity = make_entity()
    entity.add_damage(T0, 60)
    entity.add_damage(T0 + timedelta(seconds=3), 60)
    entity.update_trailing(T0 + timedelta(seconds=3))
    assert entity.trailing_damage == 120
    assert entity.trailing_dps == 10  # 120 / 12


def test_best_window_is_total_while_fight_shorter_than_window() -> None:
    entity = make_entity()
    entity.add_damage(T0, 100)
    entity.add_damage(T0 + timedelta(seconds=5), 50)
    assert entity.best_window_damage == 150


def test_best_window_tracks_hottest_12_second_span() -> None:
    entity = make_entity()
    entity.add_damage(T0, 100)
    entity.add_damage(T0 + timedelta(seconds=5), 50)
    entity.add_damage(T0 + timedelta(seconds=20), 200)
    entity.add_damage(T0 + timedelta(seconds=25), 300)
    # Hottest span is [t+20, t+25] = 500; the early span was only 150.
    assert entity.best_window_damage == 500


def test_death_freezes_trailing_and_duration() -> None:
    entity = make_entity()
    entity.add_damage(T0, 100)
    entity.add_damage(T0 + timedelta(seconds=10), 100)
    entity.death_time = T0 + timedelta(seconds=10)
    frozen_trailing = entity.trailing_damage
    entity.update_trailing(T0 + timedelta(seconds=60))
    assert entity.trailing_damage == frozen_trailing
    assert entity.total_seconds(T0 + timedelta(seconds=60)) == 10
    assert entity.total_dps(T0 + timedelta(seconds=60)) == 20  # 200 / 10s


def test_total_dps_uses_whole_fight_duration() -> None:
    entity = make_entity()
    entity.add_damage(T0, 150)
    entity.add_damage(T0 + timedelta(seconds=10), 150)
    assert entity.total_dps(T0 + timedelta(seconds=10)) == 30
    # Zero-length fights report 0 rather than dividing by zero.
    fresh = make_entity()
    fresh.add_damage(T0, 50)
    assert fresh.total_dps(T0) == 0


def test_is_stale_after_40_seconds_without_damage() -> None:
    entity = make_entity()
    entity.add_damage(T0, 10)
    assert not entity.is_stale(T0 + timedelta(seconds=40))
    assert entity.is_stale(T0 + timedelta(seconds=41))


def test_tick_recompute_is_stable_without_new_hits() -> None:
    # The tick path calls update_trailing repeatedly as `now` advances with no
    # new hits: best_window (a pure function of hits) must never drift, and the
    # trailing window must decay correctly.
    entity = make_entity()
    entity.add_damage(T0, 100)
    entity.add_damage(T0 + timedelta(seconds=20), 300)
    entity.add_damage(T0 + timedelta(seconds=25), 400)
    assert entity.best_window_damage == 700  # hottest span [t+20, t+25]
    for s in range(26, 60):
        entity.update_trailing(T0 + timedelta(seconds=s))
        assert entity.best_window_damage == 700
    entity.update_trailing(T0 + timedelta(seconds=40))
    assert entity.trailing_damage == 0  # cutoff t+28, last hit at t+25


def test_best_window_seeds_from_total_then_switches_at_twelve_seconds() -> None:
    # While span < 12s best_window is the running total; once span >= 12s it
    # becomes the hottest sliding window (and never regresses below the seed).
    entity = make_entity()
    entity.add_damage(T0, 100)
    entity.add_damage(T0 + timedelta(seconds=5), 200)
    assert entity.best_window_damage == 300  # span 5s < 12s -> total
    entity.add_damage(T0 + timedelta(seconds=11), 50)
    assert entity.best_window_damage == 350  # span 11s < 12s -> total
    entity.add_damage(T0 + timedelta(seconds=13), 10)
    # span now 13s >= 12s; the t+13 window drops the t+0 hit but the earlier
    # seed (350) is preserved.
    assert entity.best_window_damage == 350


def test_trailing_includes_hit_exactly_at_cutoff() -> None:
    entity = make_entity()
    entity.add_damage(T0, 100)
    entity.add_damage(T0 + timedelta(seconds=6), 50)
    entity.update_trailing(T0 + timedelta(seconds=12))
    # cutoff = now - 12s = T0; the boundary hit (t == cutoff) is included.
    assert entity.trailing_damage == 150


def test_level_guess_only_raises() -> None:
    entity = make_entity()
    entity.update_level(10)
    entity.update_level(None)
    entity.update_level(8)
    assert entity.level == 10
    entity.update_level(12)
    assert entity.level == 12
