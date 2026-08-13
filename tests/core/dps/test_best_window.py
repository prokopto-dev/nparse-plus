"""The best-window computation: same answer as the pre-#86 rescan, O(1) cost.

``FightEntity._update_best_window`` used to re-sweep the entity's whole hit
list on every hit, which made recording a fight quadratic in hits — 93% of all
per-line cost on a raid corpus, paid on the driver thread whether or not the
DPS window was ever opened. It now resumes a carried sliding window instead.

The pre-#86 sweep is kept here as the oracle: these tests assert the two agree
step by step over randomised cadences, and that the incremental one does not
walk the hit list.
"""

from __future__ import annotations

import random
import time
from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest

from nparseplus.core.dps import FightEntity

T0 = datetime(2026, 7, 8, 21, 0, 0)

Hits = list[tuple[datetime, int]]


# -- the oracle ---------------------------------------------------------------


def oracle_best_windows(hits: Hits, window: timedelta) -> Iterator[int]:
    """The pre-#86 ``_update_best_window``, replayed one appended hit at a time.

    Verbatim from the rescan it replaced (v2.3.2 ``core/dps.py``) apart from
    the memo guard, which is dropped because it could never fire on the append
    path — that is precisely what made the old code quadratic. Yields
    ``best_window_damage`` after each hit.
    """
    best = 0
    span_memo: timedelta | None = None
    total = 0
    for count in range(1, len(hits) + 1):
        recorded = hits[:count]
        total += recorded[-1][1]
        if window != span_memo:
            best = 0
            span_memo = window
        span = recorded[-1][0] - recorded[0][0]
        if span < window:
            best = max(best, total)
            yield best
            continue
        window_sum = 0
        left = 0
        for right, (t_right, damage) in enumerate(recorded):
            window_sum += damage
            while t_right - recorded[left][0] >= window:
                window_sum -= recorded[left][1]
                left += 1
            if right >= left:
                best = max(best, window_sum)
        yield best


def make_entity(window: timedelta = timedelta(seconds=12)) -> FightEntity:
    return FightEntity(
        attacker_name="You", target_name="a gnoll", start_time=T0, trailing_window=window
    )


def random_hits(rng: random.Random, count: int) -> Hits:
    """An irregular fight: bursts, multi-second gaps, misses, ties.

    Real log timestamps have one-second resolution, so repeated stamps are the
    common case rather than an edge one; a burst of six swings inside one
    second all carry the same time.
    """
    hits: Hits = []
    offset = 0.0
    for _ in range(count):
        offset += rng.choice([0.0, 0.0, 1.0, 1.0, 1.0, 2.0, 3.0, 7.0, 20.0])
        damage = rng.choice([0, 0, 1, 12, 40, 87, 150, 1200])
        hits.append((T0 + timedelta(seconds=offset), damage))
    return hits


# -- equivalence --------------------------------------------------------------


@pytest.mark.parametrize("window_s", [1.0, 2.0, 4.0, 12.0, 30.0])
def test_matches_the_old_rescan_over_randomised_fights(window_s: float) -> None:
    window = timedelta(seconds=window_s)
    for seed in range(20):
        hits = random_hits(random.Random(seed), 200)
        entity = make_entity(window)
        for step, ((timestamp, damage), expected) in enumerate(
            zip(hits, oracle_best_windows(hits, window), strict=True)
        ):
            entity.add_damage(timestamp, damage)
            assert entity.best_window_damage == expected, (seed, window_s, step)


def test_matches_the_old_rescan_at_a_steady_cadence() -> None:
    # The shape the meter actually sees: four swings a second, every second.
    window = timedelta(seconds=12)
    hits: Hits = [(T0 + timedelta(seconds=i // 4), 50 + (i % 30)) for i in range(400)]
    entity = make_entity(window)
    for (timestamp, damage), expected in zip(hits, oracle_best_windows(hits, window), strict=True):
        entity.add_damage(timestamp, damage)
        assert entity.best_window_damage == expected


def test_ticks_between_hits_do_not_move_the_answer() -> None:
    # The tick path re-runs update_trailing with the same window and a later
    # `now`; the carried window must survive that untouched.
    window = timedelta(seconds=12)
    hits = random_hits(random.Random(99), 150)
    entity = make_entity(window)
    for (timestamp, damage), expected in zip(hits, oracle_best_windows(hits, window), strict=True):
        entity.add_damage(timestamp, damage)
        for extra in (1, 5, 13, 60):
            entity.update_trailing(timestamp + timedelta(seconds=extra), window)
        assert entity.best_window_damage == expected


def test_a_window_change_re_measures_the_whole_fight() -> None:
    # Widening or narrowing the window discards the old best and rescans every
    # hit under the new span — the number stays "best of this fight", not
    # "best since you touched the setting".
    hits = random_hits(random.Random(7), 300)
    entity = make_entity(timedelta(seconds=12))
    for timestamp, damage in hits:
        entity.add_damage(timestamp, damage)
    for window_s in (4.0, 20.0, 12.0):
        window = timedelta(seconds=window_s)
        entity.update_trailing(hits[-1][0], window)
        expected = list(oracle_best_windows(hits, window))[-1]
        assert entity.best_window_damage == expected, window_s
        # ...and the carried state resumes correctly from the rescan.
        entity.add_damage(hits[-1][0] + timedelta(seconds=1), 999)
        resumed = list(oracle_best_windows([*hits, entity.hits[-1]], window))[-1]
        assert entity.best_window_damage == resumed, window_s
        hits = list(entity.hits)


def test_a_hit_list_edited_behind_the_entity_is_re_measured() -> None:
    # Nothing in the app does this, but the carried left/sum are only valid
    # for a single append: anything else must fall back to the full rescan
    # rather than silently score the wrong window.
    window = timedelta(seconds=12)
    entity = make_entity(window)
    entity.add_damage(T0, 10)
    entity.hits.extend([(T0 + timedelta(seconds=1), 500), (T0 + timedelta(seconds=2), 500)])
    entity.total_damage += 1000
    entity.update_trailing(T0 + timedelta(seconds=2), window)
    assert entity.best_window_damage == 1010


# -- cost ---------------------------------------------------------------------


class CountingTime(datetime):
    """A timestamp that counts how often it is subtracted from another.

    Any best-window implementation has to compare a hit's time against the
    hits behind it, so the subtraction count is a cost signal that does not
    depend on how the sweep is written (indexing, iteration, a deque).
    """

    subtractions = 0

    def __sub__(self, other):  # type: ignore[override]
        CountingTime.subtractions += 1
        return datetime.__sub__(self, other)


def time_ops_for_the_last_hit(hit_count: int) -> int:
    """Timestamp comparisons the final ``add_damage`` of a fight costs."""
    start = CountingTime(2026, 7, 8, 21, 0, 0)
    entity = FightEntity(attacker_name="You", target_name="a gnoll", start_time=start)
    for i in range(hit_count - 1):
        entity.add_damage(start + timedelta(seconds=i * 0.25), 100)
    CountingTime.subtractions = 0
    entity.add_damage(start + timedelta(seconds=(hit_count - 1) * 0.25), 100)
    return CountingTime.subtractions


def test_the_cost_of_a_hit_does_not_grow_with_the_fight() -> None:
    # The regression guard for #86: the old rescan cost one comparison per hit
    # already recorded, so this grew 16x between these two fight lengths.
    short = time_ops_for_the_last_hit(250)
    long = time_ops_for_the_last_hit(4000)
    assert long <= short, (short, long)
    assert long < 20, long  # a handful, not a walk of the list


def test_a_long_fight_stays_fast() -> None:
    # Coarse backstop under the deterministic count above, for a rewrite that
    # is quadratic in something other than timestamp comparisons: 20k hits is
    # ~40 ms incrementally and ~34 s with the old rescan.
    entity = make_entity()
    started = time.perf_counter()
    for i in range(20_000):
        entity.add_damage(T0 + timedelta(seconds=i * 0.25), 100)
    assert time.perf_counter() - started < 3.0
