"""End-to-end: log lines -> parser chain -> events -> timer rows."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from tests.core.spells.conftest import T0

from nparseplus.config.settings import SpellWindowSettings
from nparseplus.core.enums import PlayerClass
from nparseplus.core.events import LineEvent
from nparseplus.core.handlers.spell_timers import SpellTimerHandler
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext
from nparseplus.core.parsers.resist import ResistParser
from nparseplus.core.parsers.spell_cast_on_other import SpellCastOnOtherParser
from nparseplus.core.parsers.spell_worn_off import (
    SpellWornOffOtherParser,
    SpellWornOffSelfParser,
)
from nparseplus.core.parsers.you_begin_casting import YouBeginCastingParser
from nparseplus.core.parsers.you_finish_casting import YouFinishCastingParser
from nparseplus.core.spells.spells_us import SPACE_YOU
from nparseplus.core.timers import CounterRow, SpellRow, TimersService


class Harness:
    """A mini pipeline: EQTool parser chain order for the spell parsers."""

    def __init__(self, ctx: ParseContext) -> None:
        assert ctx.spells is not None
        self.ctx = ctx
        self.timers = TimersService()
        self.handler = SpellTimerHandler(ctx.bus, ctx.player, ctx.spells, self.timers)
        self.parsers = [
            YouBeginCastingParser(),
            YouFinishCastingParser(),
            SpellWornOffOtherParser(),
            SpellWornOffSelfParser(),
            ResistParser(),
            SpellCastOnOtherParser(),
        ]
        self._counter = 0

    def push(self, message: str, timestamp: datetime) -> None:
        self._counter += 1
        line = LineInfo(
            raw=message, message=message, timestamp=timestamp, line_number=self._counter
        )
        for parser in self.parsers:
            if parser.handle(line, self.ctx):
                break
        # the raw-line firehose fires regardless (LogPipeline behavior)
        self.ctx.bus.publish(
            LineEvent(timestamp=timestamp, line=message, line_number=self._counter)
        )

    def spell_rows(self) -> list[SpellRow]:
        return [r for r in self.timers.snapshot() if isinstance(r, SpellRow)]

    def counter_rows(self) -> list[CounterRow]:
        return [r for r in self.timers.snapshot() if isinstance(r, CounterRow)]


@pytest.fixture
def harness(ctx: ParseContext) -> Harness:
    return Harness(ctx)


def test_pacify_cast_on_other_duration(harness: Harness) -> None:
    # SpellMatchingTests.TestPacifyDuration
    harness.ctx.player.player_class = PlayerClass.ENCHANTER
    harness.ctx.player.level = 50
    assert harness.ctx.spells is not None
    pacify = harness.ctx.spells.spell_by_name("Pacify")
    assert pacify is not None
    harness.push("You begin casting Pacify.", T0)
    landed = T0 + timedelta(milliseconds=pacify.cast_time_ms)
    harness.push(f"Joe {pacify.cast_on_other}", landed)
    rows = harness.spell_rows()
    assert len(rows) == 1
    assert rows[0].name == "Pacify"
    assert rows[0].group == "Joe"
    assert rows[0].total_duration_s == 210


def test_bind_sight_duration(harness: Harness) -> None:
    # SpellMatchingTests.TestRangerBindSight
    harness.ctx.player.player_class = PlayerClass.RANGER
    harness.ctx.player.level = 50
    harness.push("You begin casting Bind Sight.", T0)
    harness.push("Your sight is bound.", T0 + timedelta(seconds=4))
    rows = harness.spell_rows()
    assert len(rows) == 1
    assert rows[0].total_duration_s == 660
    assert rows[0].group == SPACE_YOU


def test_harvest_cooldown_from_other_cast(harness: Harness) -> None:
    # SpellMatchingTests.TestHarvestCoolDown
    harness.ctx.player.player_class = PlayerClass.BARD
    harness.ctx.player.level = 1
    assert harness.ctx.spells is not None
    harvest = harness.ctx.spells.spell_by_name("Harvest")
    assert harvest is not None
    harness.push(f"Joe {harvest.cast_on_other}", T0)
    rows = harness.spell_rows()
    assert rows and rows[0].group == "Joe"
    assert any(r.name == "Harvest Cooldown" for r in rows)


def test_boon_of_the_garou_you_cast(harness: Harness) -> None:
    # SpellMatchingTests.TestBoonofTheGarouYouCast
    harness.ctx.player.player_class = PlayerClass.ENCHANTER
    harness.ctx.player.level = 54
    assert harness.ctx.spells is not None
    boon = harness.ctx.spells.spell_by_name("Boon of the Garou")
    assert boon is not None
    harness.push("You begin casting Boon of the Garou.", T0)
    harness.push(f"Jobob{boon.cast_on_other}", T0 + timedelta(milliseconds=boon.cast_time_ms + 200))
    names = [r.name for r in harness.spell_rows()]
    assert "Boon of the Garou Cooldown" in names
    assert "Boon of the Garou" in names


def test_mana_sieve_counter(harness: Harness) -> None:
    # SpellMatchingTests.TestManaSeive + SpellCounterTests increments
    harness.ctx.player.player_class = PlayerClass.ENCHANTER
    harness.ctx.player.level = 54
    harness.push("An ancient Frost guardian staggers in pain.", T0)
    counters = harness.counter_rows()
    assert len(counters) == 1
    assert counters[0].name == "Mana Sieve"
    assert counters[0].count == 1
    # NPC target names are grouped with a leading space
    assert counters[0].group == " An ancient Frost guardian"
    harness.push("An ancient Frost guardian staggers in pain.", T0 + timedelta(seconds=2))
    assert harness.counter_rows()[0].count == 2


def test_flux_staff_counter_and_resist_increment(harness: Harness) -> None:
    # SpellMatchingTests.TestFluxStaff + SpellCounterTests.CounterTest1 (partial:
    # resists bump an existing counter; fight-history target lookup not ported)
    harness.ctx.player.player_class = PlayerClass.WARRIOR
    harness.ctx.player.level = 52
    assert harness.ctx.spells is not None
    lower = harness.ctx.spells.spell_by_name("LowerElement")
    assert lower is not None
    harness.push(f"Jobober {lower.cast_on_other}", T0)
    counters = harness.counter_rows()
    assert len(counters) == 1 and counters[0].count == 1
    harness.push("Your target resisted the LowerElement spell.", T0 + timedelta(seconds=1))
    assert counters[0].count == 2


def test_worn_off_other_removes_row(harness: Harness) -> None:
    # SpellWornOffOtherTests.VenomOfTheSnakeViewModel
    harness.ctx.player.player_class = PlayerClass.NECROMANCER
    harness.ctx.player.level = 53
    harness.push("Someone has been poisoned.", T0)
    rows = [r for r in harness.spell_rows() if r.name == "Envenomed Bolt"]
    assert len(rows) == 1
    assert abs(rows[0].total_duration_s - 42) <= 2
    harness.push("Your Envenomed Bolt spell has worn off.", T0 + timedelta(seconds=40))
    assert not [r for r in harness.spell_rows() if r.name == "Envenomed Bolt"]


def test_worn_off_self_removes_you_row(harness: Harness) -> None:
    harness.ctx.player.player_class = PlayerClass.ENCHANTER
    harness.ctx.player.level = 54
    assert harness.ctx.spells is not None
    clarity = harness.ctx.spells.spell_by_name("Clarity")
    assert clarity is not None
    harness.push("You begin casting Clarity.", T0)
    harness.push(clarity.cast_on_you, T0 + timedelta(milliseconds=clarity.cast_time_ms))
    assert [r.group for r in harness.spell_rows()] == [SPACE_YOU]
    harness.push(clarity.spell_fades, T0 + timedelta(minutes=5))
    assert not harness.spell_rows()


def test_charm_without_completion_message(harness: Harness) -> None:
    # YouFinishCastingHandler LineEvent path: charms never print a landed line.
    harness.ctx.player.player_class = PlayerClass.ENCHANTER
    harness.ctx.player.level = 54
    assert harness.ctx.spells is not None
    charm = harness.ctx.spells.spell_by_name("Charm")
    assert charm is not None
    harness.push("You begin casting Charm.", T0)
    # any later line past casttime+1s triggers the timer-based guess
    harness.push(
        "A rat bites YOU for 1 point of damage.",
        T0 + timedelta(milliseconds=charm.cast_time_ms + 1500),
    )
    rows = harness.spell_rows()
    assert [r.name for r in rows] == ["Charm"]
    assert rows[0].group == SPACE_YOU
    # casting state was cleared afterwards
    assert harness.ctx.spells.casting.spell is None


def test_npc_detrimental_gets_extra_tick(harness: Harness) -> None:
    harness.ctx.player.player_class = PlayerClass.SHAMAN
    harness.ctx.player.level = 60
    assert harness.ctx.spells is not None
    turgurs = harness.ctx.spells.spell_by_name("Turgur's Insects")
    assert turgurs is not None
    harness.push(f"Gkrean Prophet of Tallon {turgurs.cast_on_other}", T0)
    rows = harness.spell_rows()
    assert rows[0].group == " Gkrean Prophet of Tallon"
    assert not rows[0].is_target_player
    # 60 ticks * 6s + the extra 6s grace tick for NPC detrimental timers
    assert rows[0].total_duration_s == 366


def _handler_with_settings(ctx: ParseContext, settings: SpellWindowSettings) -> SpellTimerHandler:
    assert ctx.spells is not None
    return SpellTimerHandler(
        ctx.bus, ctx.player, ctx.spells, TimersService(), spell_settings=settings
    )


def test_post_expiry_flag_set_for_allowlisted_spell(ctx: ParseContext) -> None:
    """#16: an opt-in, allow-listed buff creates a persisting row."""
    handler = _handler_with_settings(
        ctx,
        SpellWindowSettings(
            post_expiry_flash_enabled=True,
            post_expiry_flash_seconds=45,
            post_expiry_flash_spells=["clarity"],  # case-insensitive
        ),
    )
    assert ctx.spells is not None
    clarity = ctx.spells.spell_by_name("Clarity")
    assert clarity is not None
    handler.handle_spell(clarity, SPACE_YOU, 0, T0)
    row = handler.timers.rows_of(SpellRow)[0]
    assert isinstance(row, SpellRow) and row.post_expiry_persist_s == 45.0


def test_post_expiry_flag_off_by_default(ctx: ParseContext) -> None:
    handler = _handler_with_settings(ctx, SpellWindowSettings())  # defaults
    assert ctx.spells is not None
    clarity = ctx.spells.spell_by_name("Clarity")
    assert clarity is not None
    handler.handle_spell(clarity, SPACE_YOU, 0, T0)
    row = handler.timers.rows_of(SpellRow)[0]
    assert isinstance(row, SpellRow) and row.post_expiry_persist_s == 0.0


def test_post_expiry_flag_only_for_listed_spells(ctx: ParseContext) -> None:
    handler = _handler_with_settings(
        ctx,
        SpellWindowSettings(post_expiry_flash_enabled=True, post_expiry_flash_spells=["Aegolism"]),
    )
    assert ctx.spells is not None
    clarity = ctx.spells.spell_by_name("Clarity")
    assert clarity is not None
    handler.handle_spell(clarity, SPACE_YOU, 0, T0)
    row = handler.timers.rows_of(SpellRow)[0]
    assert isinstance(row, SpellRow) and row.post_expiry_persist_s == 0.0
