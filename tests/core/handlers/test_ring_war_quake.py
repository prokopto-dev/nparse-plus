"""RingWarHandler wave schedule and QuakeHandler announcements."""

from __future__ import annotations

import pytest
from tests.core.handlers.conftest import FakeSpeaker, Harness

from nparseplus.core.events import OverlayEvent
from nparseplus.core.handlers.quake import QUAKE_TEXT, QuakeHandler
from nparseplus.core.handlers.ring_war import RingWarHandler, wave_group

RING_WAR_LINE = "Seneschal Aldikar shouts, TROOPS, TAKE YOUR POSITIONS!"


@pytest.fixture
def h(harness: Harness) -> Harness:
    harness.speaker = FakeSpeaker()
    harness.handlers = [
        RingWarHandler(harness.bus, harness.player, harness.timers),
        QuakeHandler(harness.bus, harness.player, harness.speaker),
    ]
    return harness


def test_ring_war_full_schedule(h: Harness) -> None:
    h.push(RING_WAR_LINE)
    rows = h.timers.snapshot()
    assert len(rows) == 24  # 3 waves x (7 rounds + break)
    for wave in (1, 2, 3):
        group_rows = [r for r in rows if r.group == wave_group(wave)]
        assert [r.name for r in group_rows] == [f"Round {i}" for i in range(1, 8)] + ["-- Break --"]


def test_ring_war_cumulative_durations(h: Harness) -> None:
    h.push(RING_WAR_LINE)

    def duration(wave: int, name: str) -> float:
        row = h.timers.find(name, wave_group(wave))
        assert row is not None
        return row.total_duration_s

    assert duration(1, "Round 1") == 210.0
    assert duration(1, "Round 7") == 7 * 210.0
    assert duration(1, "-- Break --") == 7 * 210.0 + 300.0  # 1770
    assert duration(2, "Round 1") == 1770.0 + 210.0
    assert duration(2, "-- Break --") == 3540.0
    assert duration(3, "-- Break --") == 5314.0  # C#'s extra 4s on the final break


def test_quake_line_speaks_and_overlays(h: Harness) -> None:
    h.push("You feel you should get somewhere safe as soon as possible.")
    assert h.speaker.spoken == ["Earthquake"]
    assert h.collector.single(OverlayEvent).text == QUAKE_TEXT
