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


# -- variable respawn ("pop") windows (#125) -----------------------------------


def add_window_timer(
    timers: TimersService, name: str, base_s: float = 60.0, window_s: float = 120.0
) -> None:
    ends_at = T0 + timedelta(seconds=base_s)
    timers.add_timer(
        TimerRow(
            name=name,
            group=MOB_TIMER_GROUP,
            updated_at=T0,
            ends_at=ends_at,
            total_duration_s=base_s,
            window_ends_at=ends_at + timedelta(seconds=window_s),
        ),
        allow_duplicates=True,
    )


def test_window_open_speaks(timers: TimersService, speaker: FakeSpeaker) -> None:
    """Without this the only announcement lands at window *close* — the least
    useful moment, hours after the mob became poppable."""
    RespawnExpiryNotifier(timers, speaker, SpellWindowSettings(respawn_expiry_audio=True))
    add_window_timer(timers, "--Dead-- Trakanon")
    timers.tick(T0 + timedelta(seconds=61))
    assert speaker.spoken == ["Trakanon spawn window open"]


def test_window_close_still_speaks_the_expiry(timers: TimersService, speaker: FakeSpeaker) -> None:
    RespawnExpiryNotifier(timers, speaker, SpellWindowSettings(respawn_expiry_audio=True))
    add_window_timer(timers, "--Dead-- Trakanon")
    timers.tick(T0 + timedelta(seconds=61))
    timers.tick(T0 + timedelta(seconds=181))
    assert speaker.spoken == [
        "Trakanon spawn window open",
        "Trakanon spawn timer expired",
    ]


def test_window_open_respects_the_same_setting(timers: TimersService, speaker: FakeSpeaker) -> None:
    RespawnExpiryNotifier(timers, speaker, SpellWindowSettings())
    add_window_timer(timers, "--Dead-- Trakanon")
    timers.tick(T0 + timedelta(seconds=61))
    assert speaker.spoken == []


def test_window_open_respects_the_same_name_gate(
    timers: TimersService, speaker: FakeSpeaker
) -> None:
    RespawnExpiryNotifier(timers, speaker, SpellWindowSettings(respawn_expiry_audio=True))
    add_window_timer(timers, "A plugin's own window timer")
    timers.tick(T0 + timedelta(seconds=61))
    assert speaker.spoken == []


def add_candidate_window(
    timers: TimersService, name: str, index: int, count: int, base_s: float = 60.0
) -> None:
    ends_at = T0 + timedelta(seconds=base_s)
    timers.add_timer(
        TimerRow(
            name=name,
            group=MOB_TIMER_GROUP,
            updated_at=T0,
            ends_at=ends_at,
            total_duration_s=base_s,
            window_ends_at=ends_at + timedelta(seconds=120),
            window_series="lodizal-series",
            window_index=index,
            window_count=count,
        ),
        allow_duplicates=True,
    )


def test_a_candidate_window_says_which_one_it_is(
    timers: TimersService, speaker: FakeSpeaker
) -> None:
    """A bare "spawn window open" cannot say which chance came up, nor how
    many are left after it."""
    RespawnExpiryNotifier(timers, speaker, SpellWindowSettings(respawn_expiry_audio=True))
    add_candidate_window(timers, "--Dead-- Lodizal", 2, 3)
    timers.tick(T0 + timedelta(seconds=61))
    assert speaker.spoken == ["Lodizal spawn window 2 of 3 open"]
    # ...and the close says which chance was spent, too.
    timers.tick(T0 + timedelta(seconds=181))
    assert speaker.spoken[-1] == "Lodizal spawn timer 2 of 3 expired"


def test_a_lone_window_keeps_the_plain_wording(timers: TimersService, speaker: FakeSpeaker) -> None:
    RespawnExpiryNotifier(timers, speaker, SpellWindowSettings(respawn_expiry_audio=True))
    add_window_timer(timers, "--Dead-- Trakanon")
    timers.tick(T0 + timedelta(seconds=61))
    assert speaker.spoken == ["Trakanon spawn window open"]
