"""RespawnExpiryNotifier — opt-in TTS when a respawn timer pops (eqtool #239)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from tests.core.handlers.conftest import FakeSpeaker

from nparseplus.config.settings import SpellWindowSettings
from nparseplus.core.handlers.respawn_expiry import RespawnExpiryNotifier
from nparseplus.core.timers import MOB_TIMER_GROUP, TimerRow, TimersService

T0 = datetime(2026, 7, 8, 21, 59, 36)


@pytest.fixture
def timers() -> TimersService:
    return TimersService()


@pytest.fixture
def speaker() -> FakeSpeaker:
    return FakeSpeaker()


def add_timer(timers: TimersService, name: str, group: str = MOB_TIMER_GROUP) -> None:
    timers.add_timer(
        TimerRow(
            name=name,
            group=group,
            updated_at=T0,
            ends_at=T0 + timedelta(seconds=60),
            total_duration_s=60.0,
        ),
        allow_duplicates=True,
    )


def test_expiry_speaks_when_enabled(timers: TimersService, speaker: FakeSpeaker) -> None:
    RespawnExpiryNotifier(timers, speaker, SpellWindowSettings(respawn_expiry_audio=True))
    add_timer(timers, "--Dead-- a frost giant scout")
    timers.tick(T0 + timedelta(seconds=61))
    assert speaker.spoken == ["a frost giant scout spawn timer expired"]


def test_duplicate_suffix_stripped(timers: TimersService, speaker: FakeSpeaker) -> None:
    RespawnExpiryNotifier(timers, speaker, SpellWindowSettings(respawn_expiry_audio=True))
    add_timer(timers, "--Dead-- a frost giant scout_3")
    timers.tick(T0 + timedelta(seconds=61))
    assert speaker.spoken == ["a frost giant scout spawn timer expired"]


def test_default_off(timers: TimersService, speaker: FakeSpeaker) -> None:
    RespawnExpiryNotifier(timers, speaker, SpellWindowSettings())
    add_timer(timers, "--Dead-- a gnoll")
    timers.tick(T0 + timedelta(seconds=61))
    assert speaker.spoken == []


def test_non_respawn_rows_silent(timers: TimersService, speaker: FakeSpeaker) -> None:
    RespawnExpiryNotifier(timers, speaker, SpellWindowSettings(respawn_expiry_audio=True))
    add_timer(timers, "--Sirran the Lunatic-- ")
    add_timer(timers, "--Dead-- a gnoll", group="Somewhere Else")
    timers.tick(T0 + timedelta(seconds=61))
    assert speaker.spoken == []
