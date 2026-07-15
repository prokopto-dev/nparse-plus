"""RandomRollHandler — RollRows from /random results (RandomRollTests.cs
covers the parser; these cover the handler and grouping)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from tests.core.handlers.conftest import T0, Harness

from nparseplus.core.events import RandomRollEvent
from nparseplus.core.handlers.random_roll import ROLL_WINDOW_SECONDS, RandomRollHandler
from nparseplus.core.timers import RollRow


@pytest.fixture
def h(harness: Harness) -> Harness:
    harness.handler = RandomRollHandler(harness.bus, harness.player, harness.timers)
    return harness


def rolls(h: Harness) -> list[RollRow]:
    return [r for r in h.timers.snapshot() if isinstance(r, RollRow)]


def test_roll_lines_become_a_roll_row(h: Harness) -> None:
    h.push("**A Magic Die is rolled by Whitewitch.")
    h.push("**It could have been any number from 0 to 333, but this time it turned up a 195.")
    row = rolls(h)[0]
    assert row.name == "Whitewitch"
    assert (row.roll, row.max_roll) == (195, 333)
    assert row.group == " Random -- 333"
    assert row.total_duration_s == float(ROLL_WINDOW_SECONDS)
    assert row.ends_at == T0 + timedelta(seconds=ROLL_WINDOW_SECONDS)


def test_rolls_group_by_max_roll(h: Harness) -> None:
    h.bus.publish(RandomRollEvent(timestamp=T0, player_name="Whitewitch", max_roll=333, roll=195))
    h.bus.publish(RandomRollEvent(timestamp=T0, player_name="Steve", max_roll=100, roll=55))
    groups = {r.group for r in rolls(h)}
    assert groups == {" Random -- 333", " Random -- 100"}


def test_new_roll_resets_group_window(h: Harness) -> None:
    h.bus.publish(RandomRollEvent(timestamp=T0, player_name="Whitewitch", max_roll=333, roll=195))
    later = T0 + timedelta(seconds=60)
    h.bus.publish(RandomRollEvent(timestamp=later, player_name="Steve", max_roll=333, roll=12))
    rows = rolls(h)
    assert len(rows) == 2
    # Every roll in the group shares the newest window (RollViewModel reset).
    assert all(r.ends_at == later + timedelta(seconds=ROLL_WINDOW_SECONDS) for r in rows)
