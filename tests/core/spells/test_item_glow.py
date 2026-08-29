"""Clicky items whose spell shares a cast message with a memorized one.

Ports EQtoolsTests/ItemBeginsToGlowTests.cs. The parser for the glow line was
already here and registered; nothing subscribed to the event it published, so
"Peggy Levitate" was reachable by no route at all — except by accident, as the
first entry in the "Your feet leave the ground." candidate list, which is the
#177 bug that made every real Levitate read as the two-minute item version.
Ordering the loader correctly fixed the cast; this makes the CLICK right too.
"""

from __future__ import annotations

from datetime import timedelta

from tests.core.spells.conftest import T0, make_line

from nparseplus.config.settings import Settings
from nparseplus.core.enums import PlayerClass
from nparseplus.core.handlers.item_glow import ITEM_SPELLS, ItemGlowHandler
from nparseplus.core.handlers.spell_timers import SpellTimerHandler
from nparseplus.core.parsers.registry import build_parser_chain
from nparseplus.core.spells.durations import get_duration_seconds
from nparseplus.core.timers import YOU_GROUP, SpellRow, TimersService

CLOAK = "Your Pegasus Feather Cloak begins to glow."
FEET = "Your feet leave the ground."


def _rig(ctx) -> TimersService:
    ctx.settings = Settings()
    ctx.player.player_class = PlayerClass.SHAMAN
    ctx.player.level = 40
    timers = TimersService()
    SpellTimerHandler(
        ctx.bus, ctx.player, ctx.spells, timers, spell_settings=ctx.settings.spellwindow
    )
    ItemGlowHandler(ctx.bus, ctx.player, ctx.spells)
    return timers


def _push(ctx, message: str, at) -> None:
    for parser in build_parser_chain():
        if parser.handle(make_line(message, at), ctx):
            return


def _spell_rows(timers: TimersService) -> list[SpellRow]:
    return [r for r in timers.snapshot() if isinstance(r, SpellRow) and not r.is_cooldown]


def test_the_peggy_cloak_click_gets_the_items_duration(ctx) -> None:
    """EQtoolsTests TestPeggyCloak, through the real parser chain.

    The click line follows the begin-casting line and REPLACES what is being
    cast, so the landing message resolves against a spell already known rather
    than being guessed from the shared cast-message table.
    """
    timers = _rig(ctx)
    _push(ctx, "You begin casting Levitate.", T0)
    _push(ctx, CLOAK, T0)
    _push(ctx, FEET, T0 + timedelta(seconds=6))

    (row,) = _spell_rows(timers)
    assert row.name == "Peggy Levitate"
    assert row.group == YOU_GROUP
    peggy = ctx.spells.spell_by_name("Peggy Levitate")
    assert row.total_duration_s == float(get_duration_seconds(peggy, PlayerClass.SHAMAN, 40))


def test_a_cast_levitate_is_unaffected_by_the_handler(ctx) -> None:
    """The other half of #177: with no click, the real spell still wins."""
    timers = _rig(ctx)
    _push(ctx, "You begin casting Levitate.", T0)
    _push(ctx, FEET, T0 + timedelta(seconds=6))

    (row,) = _spell_rows(timers)
    assert row.name == "Levitate"
    levitate = ctx.spells.spell_by_name("Levitate")
    assert row.total_duration_s == float(get_duration_seconds(levitate, PlayerClass.SHAMAN, 40))


def test_the_two_durations_actually_differ(ctx) -> None:
    """Otherwise the two tests above would pass for the wrong reason."""
    peggy = ctx.spells.spell_by_name("Peggy Levitate")
    levitate = ctx.spells.spell_by_name("Levitate")
    assert get_duration_seconds(peggy, PlayerClass.SHAMAN, 40) != get_duration_seconds(
        levitate, PlayerClass.SHAMAN, 40
    )


def test_an_unmapped_item_does_not_touch_the_casting_state(ctx) -> None:
    """Most clickies share no message with a memorized spell and are left alone."""
    _rig(ctx)
    levitate = ctx.spells.spell_by_name("Levitate")
    ctx.spells.casting.begin(levitate, T0)
    _push(ctx, "Your Shissar Seance Staff begins to glow.", T0)
    assert ctx.spells.casting.spell is levitate


def test_every_mapped_spell_exists_in_the_database(spell_book) -> None:
    """A table entry naming a spell the loader does not produce is dead."""
    for item, spell_name in ITEM_SPELLS.items():
        assert spell_book.spell_by_name(spell_name) is not None, f"{item} -> {spell_name}"
