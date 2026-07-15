"""Death loop detection — DeathLoopService and DeathLoopHandler."""

from __future__ import annotations

from datetime import timedelta

import pytest
from tests.core.handlers.conftest import T0, FakeSpeaker, Harness

from nparseplus.core.death_loop import DeathLoopService
from nparseplus.core.events import OverlayEvent
from nparseplus.core.handlers.death_loop import (
    DEATH_LOOP_TEXT,
    DEATH_LOOP_TTS,
    DeathLoopHandler,
)


def test_service_threshold_and_window() -> None:
    service = DeathLoopService()
    for i in range(3):
        assert service.record_death(T0 + timedelta(seconds=10 * i)) is False
    assert service.record_death(T0 + timedelta(seconds=30)) is True


def test_service_old_deaths_roll_off() -> None:
    service = DeathLoopService()
    service.record_death(T0)
    service.record_death(T0 + timedelta(seconds=10))
    service.record_death(T0 + timedelta(seconds=20))
    # The fourth death arrives after the first has left the 120s window.
    assert service.record_death(T0 + timedelta(seconds=125)) is False
    assert service.death_count == 3


def test_service_activity_clears() -> None:
    service = DeathLoopService()
    service.record_death(T0)
    service.record_activity()
    assert service.death_count == 0


@pytest.fixture
def h(harness: Harness) -> Harness:
    harness.speaker = FakeSpeaker()
    harness.handler = DeathLoopHandler(harness.bus, harness.player, harness.speaker)
    return harness


def test_four_quick_deaths_trigger_alarm(h: Harness) -> None:
    for i in range(4):
        h.push("You have been slain by a brigand!", T0 + timedelta(seconds=15 * i))
    assert h.speaker.spoken == [DEATH_LOOP_TTS]
    assert h.collector.single(OverlayEvent).text == DEATH_LOOP_TEXT


def test_melee_activity_prevents_alarm(h: Harness) -> None:
    for i in range(3):
        h.push("You have been slain by a brigand!", T0 + timedelta(seconds=15 * i))
    h.push("You crush a skeleton for 46 points of damage.", T0 + timedelta(seconds=50))
    h.push("You have been slain by a brigand!", T0 + timedelta(seconds=60))
    assert h.speaker.spoken == []


def test_chat_activity_prevents_alarm(h: Harness) -> None:
    for i in range(3):
        h.push("You have been slain by a brigand!", T0 + timedelta(seconds=15 * i))
    h.push("You say, 'oops brb'", T0 + timedelta(seconds=50))
    h.push("You have been slain by a brigand!", T0 + timedelta(seconds=60))
    assert h.speaker.spoken == []


def test_other_deaths_do_not_count(h: Harness) -> None:
    for i in range(4):
        h.push("Marantula has been slain by an ancient wyvern!", T0 + timedelta(seconds=i))
    assert h.speaker.spoken == []


def test_slow_deaths_never_trigger(h: Harness) -> None:
    for i in range(6):
        h.push("You have been slain by a brigand!", T0 + timedelta(seconds=50 * i))
    assert h.speaker.spoken == []
