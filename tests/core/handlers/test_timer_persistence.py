"""TimerPersistenceHandler — you_spells (EQTool YouSpells) and respawn timers
(nparse #57) survive camping/restarts via the per-character profile."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from nparseplus.config.settings import PlayerInfo, SavedTimer, Settings, YouSpell
from nparseplus.core.bus import EventBus
from nparseplus.core.enums import PlayerClass
from nparseplus.core.events import AfterPlayerChangedEvent, BeforePlayerChangedEvent
from nparseplus.core.handlers.spawn_timer import CUSTOM_TIMER_GROUP
from nparseplus.core.handlers.timer_persistence import TimerPersistenceHandler
from nparseplus.core.player import ActivePlayer, Server
from nparseplus.core.spells.spells_us import SpellBook, load_spell_book
from nparseplus.core.timers import YOU_GROUP, SpellRow, TimerRow, TimersService

SPELLS_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "spells_us.txt"

T0 = datetime(2026, 7, 8, 21, 59, 36)


@pytest.fixture(scope="module")
def spell_book() -> SpellBook:
    return load_spell_book(SPELLS_FIXTURE)


class Env:
    def __init__(self, spell_book: SpellBook) -> None:
        self.bus = EventBus()
        self.player = ActivePlayer(name="Tester", server=Server.GREEN)
        self.player.player_class = PlayerClass.ENCHANTER
        self.player.level = 60
        self.settings = Settings(
            players=[PlayerInfo(name="Tester", server="green")],
        )
        self.timers = TimersService()
        self.saves = 0
        self.now = T0

        def bump() -> None:
            self.saves += 1

        self.handler = TimerPersistenceHandler(
            self.bus,
            self.player,
            self.settings,
            self.timers,
            spell_book,
            request_save=bump,
            clock=lambda: self.now,
        )

    @property
    def profile(self) -> PlayerInfo:
        return self.settings.players[0]

    def event(self, event_type):
        return event_type(timestamp=self.now, line="", line_number=1)

    def add_respawn(self, name: str, seconds: float) -> None:
        self.timers.add_timer(
            TimerRow(
                name=name,
                group=CUSTOM_TIMER_GROUP,
                updated_at=self.now,
                ends_at=self.now + timedelta(seconds=seconds),
                total_duration_s=seconds,
            ),
            allow_duplicates=True,
        )


@pytest.fixture
def env(spell_book: SpellBook) -> Env:
    return Env(spell_book)


def buff_row(spell_book: SpellBook, now: datetime, seconds: int = 300) -> SpellRow:
    spell = spell_book.spell_by_name("Clarity")
    assert spell is not None
    return SpellRow(
        name=spell.name,
        group=YOU_GROUP,
        updated_at=now,
        spell=spell,
        ends_at=now + timedelta(seconds=seconds),
        total_duration_s=float(seconds),
    )


def test_row_changes_export_to_profile(env: Env, spell_book: SpellBook) -> None:
    env.add_respawn("--Dead-- a gnoll", 400.0)
    env.timers.add_spell(buff_row(spell_book, env.now))
    assert [t.name for t in env.profile.respawn_timers] == ["--Dead-- a gnoll"]
    assert env.profile.you_spells == [YouSpell(name="Clarity", seconds_left=300)]
    assert env.saves >= 2


def test_restore_on_player_change(env: Env) -> None:
    env.profile.you_spells = [YouSpell(name="Clarity", seconds_left=120)]
    env.profile.respawn_timers = [
        SavedTimer(
            name="--Dead-- a gnoll", ends_at=T0 + timedelta(seconds=90), total_duration_s=400.0
        )
    ]
    env.bus.publish(env.event(AfterPlayerChangedEvent))
    spell_rows = [r for r in env.timers.snapshot() if isinstance(r, SpellRow)]
    timer_rows = [r for r in env.timers.snapshot() if isinstance(r, TimerRow)]
    assert [r.name for r in spell_rows] == ["Clarity"]
    assert spell_rows[0].ends_at == T0 + timedelta(seconds=120)
    assert [r.name for r in timer_rows] == ["--Dead-- a gnoll"]
    assert timer_rows[0].ends_at == T0 + timedelta(seconds=90)
    assert timer_rows[0].group == CUSTOM_TIMER_GROUP


def test_expired_respawn_dropped_on_restore(env: Env) -> None:
    env.profile.respawn_timers = [
        SavedTimer(
            name="--Dead-- a gnoll", ends_at=T0 - timedelta(seconds=5), total_duration_s=400.0
        )
    ]
    env.bus.publish(env.event(AfterPlayerChangedEvent))
    assert env.timers.snapshot() == []
    # The re-sync export also scrubbed the dead entry from the profile.
    assert env.profile.respawn_timers == []


def test_restore_replaces_stale_rows_without_looping(env: Env, spell_book: SpellBook) -> None:
    env.timers.add_spell(buff_row(spell_book, env.now))
    env.add_respawn("--Dead-- stale", 50.0)
    env.profile.you_spells = []
    env.profile.respawn_timers = [
        SavedTimer(name="--Dead-- fresh", ends_at=T0 + timedelta(seconds=60), total_duration_s=60.0)
    ]
    env.bus.publish(env.event(AfterPlayerChangedEvent))
    names = [r.name for r in env.timers.snapshot()]
    assert names == ["--Dead-- fresh"]


def test_camp_exports_current_seconds_left(env: Env, spell_book: SpellBook) -> None:
    env.timers.add_spell(buff_row(spell_book, env.now, seconds=300))
    # Five minutes pass with no row changes; camping must re-snapshot.
    env.now = T0 + timedelta(seconds=240)
    env.bus.publish(env.event(BeforePlayerChangedEvent))
    assert env.profile.you_spells == [YouSpell(name="Clarity", seconds_left=60)]


def test_no_profile_is_a_noop(spell_book: SpellBook) -> None:
    bus = EventBus()
    player = ActivePlayer()  # unconfigured: no name/server
    settings = Settings()
    timers = TimersService()
    TimerPersistenceHandler(bus, player, settings, timers, spell_book)
    timers.add_timer(
        TimerRow(
            name="x",
            group=CUSTOM_TIMER_GROUP,
            updated_at=T0,
            ends_at=T0 + timedelta(seconds=5),
            total_duration_s=5.0,
        )
    )
    bus.publish(AfterPlayerChangedEvent(timestamp=T0, line="", line_number=1))
    assert settings.players == []
