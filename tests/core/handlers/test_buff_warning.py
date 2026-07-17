"""BuffFadeWarner — pre-warning before self-buffs fade (GINA parity)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from tests.core.handlers.conftest import FakeSpeaker

from nparseplus.config.settings import SpellWindowSettings
from nparseplus.core.bus import EventBus
from nparseplus.core.events import OverlayEvent
from nparseplus.core.handlers.buff_warning import BuffFadeWarner
from nparseplus.core.spells.models import Spell
from nparseplus.core.timers import YOU_GROUP, SpellRow, TimersService

T0 = datetime(2026, 7, 8, 21, 59, 36)


class Env:
    def __init__(self, threshold: int = 30, audio: bool = True) -> None:
        self.bus = EventBus()
        self.timers = TimersService()
        self.speaker = FakeSpeaker()
        self.settings = SpellWindowSettings(
            buff_fade_warning_seconds=threshold, buff_fade_warning_audio=audio
        )
        self.warner = BuffFadeWarner(self.bus, self.timers, self.speaker, self.settings)
        self.overlays: list[OverlayEvent] = []
        self.bus.subscribe(OverlayEvent, self.overlays.append)

    def add_buff(self, name: str = "Clarity", seconds: float = 300.0, **kwargs) -> SpellRow:
        return self.timers.add_spell(
            SpellRow(
                name=name,
                group=kwargs.pop("group", YOU_GROUP),
                updated_at=T0,
                spell=Spell(id=1, name=name),
                ends_at=T0 + timedelta(seconds=seconds),
                total_duration_s=seconds,
                **kwargs,
            )
        )


@pytest.fixture
def env() -> Env:
    return Env()


def test_fires_once_inside_threshold(env: Env) -> None:
    env.add_buff(seconds=300)
    env.warner.tick(T0 + timedelta(seconds=100))
    assert env.speaker.spoken == []
    env.warner.tick(T0 + timedelta(seconds=275))
    env.warner.tick(T0 + timedelta(seconds=280))
    assert env.speaker.spoken == ["Clarity is fading"]
    assert [e.text for e in env.overlays] == ["Clarity is fading"]


def test_recast_rearms(env: Env) -> None:
    env.add_buff(seconds=300)
    env.warner.tick(T0 + timedelta(seconds=275))
    # Recast: add_spell overwrites the row with a fresh ends_at.
    env.add_buff(seconds=300 + 280)
    env.warner.tick(T0 + timedelta(seconds=290))  # plenty left again
    env.warner.tick(T0 + timedelta(seconds=560))  # back inside the threshold
    assert env.speaker.spoken == ["Clarity is fading", "Clarity is fading"]


def test_zero_threshold_disables() -> None:
    env = Env(threshold=0)
    env.add_buff(seconds=10)
    env.warner.tick(T0 + timedelta(seconds=5))
    assert env.speaker.spoken == []
    assert env.overlays == []


def test_audio_off_still_publishes_overlay() -> None:
    env = Env(audio=False)
    env.add_buff(seconds=10)
    env.warner.tick(T0 + timedelta(seconds=5))
    assert env.speaker.spoken == []
    assert [e.text for e in env.overlays] == ["Clarity is fading"]


def test_ignores_others_cooldowns_detrimentals_and_expired(env: Env) -> None:
    env.add_buff(name="Haste", seconds=10, group="Joe")
    env.add_buff(name="Harvest Cooldown", seconds=10, is_cooldown=True)
    env.add_buff(name="Tainted Breath", seconds=10, detrimental=True)
    env.add_buff(name="Clarity", seconds=10)
    env.warner.tick(T0 + timedelta(seconds=20))  # Clarity already expired
    assert env.speaker.spoken == []
