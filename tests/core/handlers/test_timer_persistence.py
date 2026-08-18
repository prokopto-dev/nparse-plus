"""TimerPersistenceHandler — you_spells (EQTool YouSpells), self cooldowns and
counters (#120) and respawn timers (nparse #57) survive camping, relogging and
restarts via the per-character profile."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from nparseplus.config.settings import (
    PlayerInfo,
    SavedCooldown,
    SavedCounter,
    SavedTimer,
    Settings,
    YouSpell,
)
from nparseplus.core.bus import EventBus
from nparseplus.core.enums import PlayerClass
from nparseplus.core.events import (
    AfterPlayerChangedEvent,
    BeforePlayerChangedEvent,
    CampEvent,
    WelcomeEvent,
)
from nparseplus.core.handlers.boat import BOATS_GROUP
from nparseplus.core.handlers.random_roll import roll_group
from nparseplus.core.handlers.timer_persistence import TimerPersistenceHandler
from nparseplus.core.player import ActivePlayer, Server
from nparseplus.core.spells.spells_us import SpellBook, load_spell_book
from nparseplus.core.timers import (
    MOB_TIMER_GROUP,
    ROLL_TIMER_GROUP,
    TRIGGER_TIMER_GROUP,
    YOU_GROUP,
    CounterRow,
    RollRow,
    SpellRow,
    TimerRow,
    TimersService,
)

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
        self.spell_book = spell_book
        # The log's own clock — what export measures seconds-left against.
        # None until a test supplies one, so the wall clock is the anchor.
        self.log_now: datetime | None = None

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
            log_clock=lambda: self.log_now,
        )

    @property
    def profile(self) -> PlayerInfo:
        return self.settings.players[0]

    def event(self, event_type):
        return event_type(timestamp=self.now, line="", line_number=1)

    def advance(self, seconds: float) -> None:
        """Time passes with the log still flowing (both clocks move)."""
        self.now += timedelta(seconds=seconds)
        if self.log_now is not None:
            self.log_now = self.now

    def add_respawn(self, name: str, seconds: float) -> None:
        self.timers.add_timer(
            TimerRow(
                name=name,
                group=MOB_TIMER_GROUP,
                updated_at=self.now,
                ends_at=self.now + timedelta(seconds=seconds),
                total_duration_s=seconds,
            ),
            allow_duplicates=True,
        )

    def add_pop_window(self, name: str, base_s: float, window_s: float) -> TimerRow:
        """A TOD-anchored respawn (#125): base countdown, then a pop window."""
        ends_at = self.now + timedelta(seconds=base_s)
        return self.timers.add_timer(
            TimerRow(
                name=name,
                group=MOB_TIMER_GROUP,
                updated_at=self.now,
                ends_at=ends_at,
                total_duration_s=base_s,
                window_ends_at=ends_at + timedelta(seconds=window_s),
            ),
            allow_duplicates=True,
        )

    def add_buff(self, seconds: int = 300) -> SpellRow:
        return self.timers.add_spell(buff_row(self.spell_book, self.now, seconds))

    def add_self_cooldown(self, name: str, seconds: float) -> TimerRow:
        return self.timers.add_timer(
            TimerRow(
                name=name,
                group=YOU_GROUP,
                updated_at=self.now,
                ends_at=self.now + timedelta(seconds=seconds),
                total_duration_s=seconds,
            )
        )

    def add_self_counter(self, name: str) -> CounterRow:
        return self.timers.add_counter(CounterRow(name=name, group=YOU_GROUP, updated_at=self.now))

    def add_world_rows(self) -> list[str]:
        """One row of every kind camping must leave alone; returns their names."""
        self.add_respawn("--Dead-- a gnoll", 400.0)
        self.timers.add_timer(
            TimerRow(
                name="Boat to Freeport",
                group=BOATS_GROUP,
                updated_at=self.now,
                ends_at=self.now + timedelta(seconds=600),
                total_duration_s=600.0,
            )
        )
        self.timers.add_timer(
            TimerRow(
                name="My custom timer",
                group=TRIGGER_TIMER_GROUP,
                updated_at=self.now,
                ends_at=self.now + timedelta(seconds=120),
                total_duration_s=120.0,
            )
        )
        self.timers.add_timer(
            TimerRow(
                name="Ring 8",
                group=ROLL_TIMER_GROUP,
                updated_at=self.now,
                ends_at=self.now + timedelta(seconds=900),
                total_duration_s=900.0,
            )
        )
        self.timers.add_roll(
            RollRow(
                name="Tester",
                group=roll_group(100),
                updated_at=self.now,
                roll=42,
                max_roll=100,
                ends_at=self.now + timedelta(seconds=180),
                total_duration_s=180.0,
            )
        )
        return ["--Dead-- a gnoll", "Boat to Freeport", "My custom timer", "Ring 8", "Tester"]


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
    assert timer_rows[0].group == MOB_TIMER_GROUP


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
    env.log_now = T0
    env.timers.add_spell(buff_row(spell_book, env.now, seconds=300))
    # Four minutes pass with no row changes; camping must re-snapshot.
    env.advance(240)
    env.bus.publish(env.event(BeforePlayerChangedEvent))
    assert env.profile.you_spells == [YouSpell(name="Clarity", seconds_left=60)]


# -- camping (#120) ------------------------------------------------------------


def test_camp_hides_only_this_characters_rows(env: Env) -> None:
    env.add_buff()
    env.add_self_cooldown("Lay on Hands", 600.0)
    env.add_self_counter("Selo's Accelerando casts")
    world = env.add_world_rows()

    env.bus.publish(env.event(CampEvent))

    assert [r.name for r in env.timers.snapshot() if r.group == YOU_GROUP] == []
    assert sorted(r.name for r in env.timers.snapshot()) == sorted(world)


def test_camp_saves_buffs_frozen_and_cooldowns_absolute(env: Env) -> None:
    env.log_now = T0
    env.add_buff(seconds=300)
    env.add_self_cooldown("Lay on Hands", 600.0)
    env.add_self_counter("Selo's Accelerando casts")
    env.advance(60)

    env.bus.publish(env.event(CampEvent))

    assert env.profile.you_spells == [YouSpell(name="Clarity", seconds_left=240)]
    assert env.profile.you_cooldowns == [
        SavedCooldown(
            name="Lay on Hands", ends_at=T0 + timedelta(seconds=600), total_duration_s=600.0
        )
    ]
    assert env.profile.you_counters == [
        SavedCounter(name="Selo's Accelerando casts", count=1, updated_at=T0)
    ]


def test_camp_leaves_the_respawn_store_alone(env: Env) -> None:
    env.add_world_rows()
    env.bus.publish(env.event(CampEvent))
    assert [t.name for t in env.profile.respawn_timers] == ["--Dead-- a gnoll"]


def test_a_change_after_camp_does_not_wipe_the_snapshot(env: Env) -> None:
    """The clobber trap: export runs on every TimersService change, so removing
    the rows on camp must not re-export an empty set over the snapshot."""
    env.add_buff()
    env.bus.publish(env.event(CampEvent))
    assert env.profile.you_spells == [YouSpell(name="Clarity", seconds_left=300)]

    # Any later row change at all — a mob respawn ticking away, a shared timer.
    env.advance(30)
    env.add_respawn("--Dead-- a bat", 200.0)
    env.timers.tick(env.now)

    assert env.profile.you_spells == [YouSpell(name="Clarity", seconds_left=300)]


def test_camp_then_quit_keeps_the_snapshot(env: Env) -> None:
    """Backend.stop calls export_now after joining the driver thread."""
    env.add_buff()
    env.add_self_cooldown("Lay on Hands", 600.0)
    env.bus.publish(env.event(CampEvent))
    env.advance(30)

    env.handler.export_now()

    assert env.profile.you_spells == [YouSpell(name="Clarity", seconds_left=300)]
    assert [c.name for c in env.profile.you_cooldowns] == ["Lay on Hands"]


def test_camp_then_player_change_keeps_the_snapshot(env: Env) -> None:
    env.add_buff()
    env.bus.publish(env.event(CampEvent))
    env.advance(30)
    env.bus.publish(env.event(BeforePlayerChangedEvent))
    assert env.profile.you_spells == [YouSpell(name="Clarity", seconds_left=300)]


# -- logging back in (#120) ----------------------------------------------------


def test_welcome_restores_buffs_at_the_same_remaining_time(env: Env) -> None:
    env.add_buff(seconds=300)
    env.bus.publish(env.event(CampEvent))
    env.advance(3600)  # an hour camped

    env.bus.publish(env.event(WelcomeEvent))

    rows = [r for r in env.timers.snapshot() if isinstance(r, SpellRow)]
    assert [r.name for r in rows] == ["Clarity"]
    assert rows[0].ends_at == env.now + timedelta(seconds=300)


def test_welcome_restores_a_cooldown_with_elapsed_time_deducted(env: Env) -> None:
    env.add_self_cooldown("Lay on Hands", 3600.0)
    env.bus.publish(env.event(CampEvent))
    env.advance(1200)

    env.bus.publish(env.event(WelcomeEvent))

    rows = [r for r in env.timers.snapshot() if isinstance(r, TimerRow)]
    assert [r.name for r in rows] == ["Lay on Hands"]
    # Absolute end, so the 20 minutes away came off it.
    assert rows[0].ends_at == T0 + timedelta(seconds=3600)
    assert rows[0].group == YOU_GROUP


def test_welcome_drops_a_cooldown_that_came_up_while_away(env: Env) -> None:
    env.add_self_cooldown("Harm Touch", 300.0)
    env.bus.publish(env.event(CampEvent))
    env.advance(600)

    env.bus.publish(env.event(WelcomeEvent))

    assert env.timers.snapshot() == []
    assert env.profile.you_cooldowns == []


def test_welcome_restores_a_spell_recast_cooldown_as_a_spell_row(env: Env) -> None:
    spell = env.spell_book.spell_by_name("Divine Aura")
    assert spell is not None
    env.timers.add_spell(
        SpellRow(
            name=f"{spell.name} Cooldown",
            group=YOU_GROUP,
            updated_at=env.now,
            spell=spell,
            ends_at=env.now + timedelta(seconds=600),
            total_duration_s=600.0,
            is_cooldown=True,
        )
    )
    env.bus.publish(env.event(CampEvent))
    assert env.timers.snapshot() == []
    # A cooldown is not a buff: it must not come back frozen through you_spells.
    assert env.profile.you_spells == []
    assert env.profile.you_cooldowns[0].spell_name == "Divine Aura"

    env.advance(300)
    env.bus.publish(env.event(WelcomeEvent))

    rows = env.timers.snapshot()
    assert len(rows) == 1
    assert isinstance(rows[0], SpellRow)
    assert rows[0].is_cooldown
    assert rows[0].ends_at == T0 + timedelta(seconds=600)


def test_welcome_restores_a_counter_and_drops_an_idle_one(env: Env) -> None:
    env.add_self_counter("Selo's Accelerando casts")
    env.bus.publish(env.event(CampEvent))

    env.advance(60)
    env.bus.publish(env.event(WelcomeEvent))
    rows = [r for r in env.timers.snapshot() if isinstance(r, CounterRow)]
    assert [(r.name, r.count) for r in rows] == [("Selo's Accelerando casts", 1)]

    # Camp again and stay away past the counter's idle expiry.
    env.bus.publish(env.event(CampEvent))
    env.advance(60 * 60)
    env.bus.publish(env.event(WelcomeEvent))
    assert env.timers.snapshot() == []


def test_welcome_replaces_the_previous_sessions_rows(env: Env) -> None:
    """A linkdead-and-relog the app ran straight through: whatever YOU_GROUP
    rows are on screen belong to a session that has ended."""
    env.log_now = T0
    env.add_buff(seconds=300)
    # The client stops writing. Rows keep draining against the wall clock, but
    # the log's clock — and so the snapshot — stops here.
    env.now += timedelta(seconds=120)
    env.timers.tick(env.now)

    env.bus.publish(env.event(WelcomeEvent))

    rows = [r for r in env.timers.snapshot() if isinstance(r, SpellRow)]
    assert len(rows) == 1
    assert rows[0].ends_at == env.now + timedelta(seconds=300)


def test_welcome_leaves_the_world_rows_alone(env: Env) -> None:
    world = env.add_world_rows()
    env.bus.publish(env.event(CampEvent))
    env.advance(60)
    env.bus.publish(env.event(WelcomeEvent))
    assert sorted(r.name for r in env.timers.snapshot()) == sorted(world)


def test_camp_as_one_character_then_login_as_another(env: Env) -> None:
    env.add_buff(seconds=300)
    env.add_self_cooldown("Lay on Hands", 3600.0)
    env.bus.publish(env.event(CampEvent))

    other = PlayerInfo(
        name="Mule",
        server="green",
        you_spells=[YouSpell(name="Strength", seconds_left=90)],
    )
    env.settings.players.append(other)
    env.advance(60)
    env.player.reset_for("Mule", Server.GREEN)
    env.bus.publish(env.event(AfterPlayerChangedEvent))

    assert [r.name for r in env.timers.snapshot()] == ["Strength"]
    # Tester's own snapshot is untouched on Tester's profile.
    assert env.profile.name == "Tester"
    assert env.profile.you_spells == [YouSpell(name="Clarity", seconds_left=300)]
    assert [c.name for c in env.profile.you_cooldowns] == ["Lay on Hands"]


# -- the log's clock is the anchor (#120 addendum: linkdead) -------------------


def test_snapshot_does_not_erode_while_the_log_is_silent(env: Env) -> None:
    env.log_now = T0
    env.add_buff(seconds=300)
    assert env.profile.you_spells == [YouSpell(name="Clarity", seconds_left=300)]

    # Linkdead: the client stops writing, so the log's clock stops. The wall
    # clock keeps going, rows keep draining, on_change keeps firing.
    for _ in range(6):
        env.now += timedelta(seconds=60)
        env.timers.tick(env.now)
        env.add_respawn(f"--Dead-- a bat {env.now:%M%S}", 400.0)

    # The buff itself has long expired against the wall clock and is gone from
    # the window, but the snapshot froze where the log left it.
    assert env.timers.find("Clarity") is None
    assert env.profile.you_spells == [YouSpell(name="Clarity", seconds_left=300)]


def test_buff_restores_at_the_value_it_had_when_the_log_stopped(env: Env) -> None:
    env.log_now = T0
    env.add_buff(seconds=1200)
    # Two minutes of play (lines still flowing), then the client dies.
    env.advance(120)
    env.add_respawn("--Dead-- a gnoll", 400.0)
    assert env.profile.you_spells == [YouSpell(name="Clarity", seconds_left=1080)]

    env.now += timedelta(hours=2)
    env.timers.tick(env.now)
    env.log_now = env.now  # relogged: the log starts writing again
    env.bus.publish(env.event(WelcomeEvent))

    rows = [r for r in env.timers.snapshot() if isinstance(r, SpellRow)]
    assert [r.name for r in rows] == ["Clarity"]
    assert rows[0].ends_at == env.now + timedelta(seconds=1080)


def test_a_silent_log_does_not_block_an_explicit_export(env: Env) -> None:
    """export_now is authoritative — the freeze is only for on_change."""
    env.log_now = T0
    env.add_buff(seconds=300)
    env.advance(0)  # same log second
    env.add_self_cooldown("Lay on Hands", 600.0)
    # The gate skipped that on_change (the log said nothing new)...
    assert env.profile.you_cooldowns == []
    # ...but camp/quit/player-change ask directly.
    env.handler.export_now()
    assert [c.name for c in env.profile.you_cooldowns] == ["Lay on Hands"]


def test_no_profile_is_a_noop(spell_book: SpellBook) -> None:
    bus = EventBus()
    player = ActivePlayer()  # unconfigured: no name/server
    settings = Settings()
    timers = TimersService()
    TimerPersistenceHandler(bus, player, settings, timers, spell_book)
    timers.add_timer(
        TimerRow(
            name="x",
            group=MOB_TIMER_GROUP,
            updated_at=T0,
            ends_at=T0 + timedelta(seconds=5),
            total_duration_s=5.0,
        )
    )
    bus.publish(AfterPlayerChangedEvent(timestamp=T0, line="", line_number=1))
    assert settings.players == []


# -- variable respawn ("pop") windows (#125) -----------------------------------


def test_a_window_row_round_trips_before_it_opens(env: Env) -> None:
    row = env.add_pop_window("--Dead-- Trakanon", 400.0, 900.0)
    saved = env.profile.respawn_timers
    assert saved == [
        SavedTimer(
            name="--Dead-- Trakanon",
            ends_at=row.ends_at,
            total_duration_s=400.0,
            window_ends_at=row.window_ends_at,
            window_opened_at=None,
        )
    ]

    env.advance(60)
    env.bus.publish(env.event(AfterPlayerChangedEvent))
    rows = [r for r in env.timers.snapshot() if isinstance(r, TimerRow)]
    assert [r.name for r in rows] == ["--Dead-- Trakanon"]
    assert rows[0].ends_at == row.ends_at
    assert rows[0].window_ends_at == row.window_ends_at
    assert rows[0].window_opened_at is None
    # The re-sync export after the restore keeps the window intact.
    assert env.profile.respawn_timers[0].window_ends_at == row.window_ends_at


def test_camping_mid_window_brings_the_row_back_still_in_its_window(env: Env) -> None:
    """The old ends_at filter dropped the row at exactly the moment it matters
    most: its base end is in the past for the whole window."""
    row = env.add_pop_window("--Dead-- Trakanon", 400.0, 900.0)
    env.advance(460)
    env.timers.tick(env.now)  # the crossover: the window opened 60 s ago
    opened_at = row.window_opened_at
    assert opened_at == row.ends_at + timedelta(seconds=60)

    env.bus.publish(env.event(CampEvent))
    assert env.profile.respawn_timers[0].window_opened_at == opened_at
    # Mob respawns are world state — camping never takes them off screen.
    assert [r.name for r in env.timers.snapshot()] == ["--Dead-- Trakanon"]

    env.advance(120)
    env.bus.publish(env.event(AfterPlayerChangedEvent))
    rows = [r for r in env.timers.snapshot() if isinstance(r, TimerRow)]
    assert [r.name for r in rows] == ["--Dead-- Trakanon"]
    assert rows[0].window_ends_at == row.window_ends_at
    # Preserved, not re-stamped.
    assert rows[0].window_opened_at == opened_at


def test_a_character_swap_does_not_re_announce_an_open_window(env: Env) -> None:
    opened: list[str] = []
    env.timers.on_window_open.append(lambda rows: opened.extend(r.name for r in rows))
    env.add_pop_window("--Dead-- Trakanon", 400.0, 900.0)
    env.advance(400)
    env.timers.tick(env.now)
    assert opened == ["--Dead-- Trakanon"]

    for _ in range(3):
        env.advance(30)
        env.bus.publish(env.event(AfterPlayerChangedEvent))
        env.timers.tick(env.now)
    assert opened == ["--Dead-- Trakanon"]


def test_a_window_that_closed_while_away_is_dropped(env: Env) -> None:
    env.profile.respawn_timers = [
        SavedTimer(
            name="--Dead-- Trakanon",
            ends_at=T0 + timedelta(seconds=400),
            total_duration_s=400.0,
            window_ends_at=T0 + timedelta(seconds=1300),
            window_opened_at=T0 + timedelta(seconds=400),
        )
    ]
    env.now = T0 + timedelta(seconds=1301)
    env.bus.publish(env.event(AfterPlayerChangedEvent))
    assert env.timers.snapshot() == []
    assert env.profile.respawn_timers == []


def test_an_old_store_without_the_window_fields_still_loads(env: Env) -> None:
    """The fields are optional precisely so no migration is needed."""
    env.profile.respawn_timers = [
        SavedTimer(
            name="--Dead-- a gnoll", ends_at=T0 + timedelta(seconds=90), total_duration_s=400.0
        )
    ]
    env.bus.publish(env.event(AfterPlayerChangedEvent))
    rows = [r for r in env.timers.snapshot() if isinstance(r, TimerRow)]
    assert [r.name for r in rows] == ["--Dead-- a gnoll"]
    assert rows[0].window_ends_at is None


def test_a_candidate_window_series_survives_a_camp(env: Env) -> None:
    """All three of Lodizal's chances come back, still knowing they are one
    mob and which chance each was."""
    series = "lodizal"
    for index, hours in enumerate((12, 20, 30), start=1):
        ends_at = env.now + timedelta(hours=hours)
        env.timers.add_timer(
            TimerRow(
                name="--Dead-- Lodizal",
                group=MOB_TIMER_GROUP,
                updated_at=env.now,
                ends_at=ends_at,
                total_duration_s=hours * 3600.0,
                window_ends_at=ends_at + timedelta(hours=4),
                window_series=series,
                window_index=index,
                window_count=3,
            ),
            allow_duplicates=True,
        )
    saved = env.profile.respawn_timers
    assert [(t.window_index, t.window_count) for t in saved] == [(1, 3), (2, 3), (3, 3)]
    assert {t.window_series for t in saved} == {series}

    env.advance(60)
    env.bus.publish(env.event(AfterPlayerChangedEvent))
    rows = [r for r in env.timers.snapshot() if isinstance(r, TimerRow)]
    assert [(r.window_index, r.window_count) for r in rows] == [(1, 3), (2, 3), (3, 3)]
    assert {r.window_series for r in rows} == {series}
    # And the set can still be cleared as one.
    assert env.timers.remove_series(series) == 3
