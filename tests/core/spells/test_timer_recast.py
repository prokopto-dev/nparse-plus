"""TimerRecast policy (PlayerInfo.TimerRecastSetting port + eqtool #213).

RestartCurrentTimer (default) refreshes the running row; StartNewTimer stacks
a new row per cast of a detrimental spell on an NPC — except root spells,
which always refresh (nparseplus divergence, see ROOT_SPELLS).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from tests.core.spells.conftest import T0

from nparseplus.core.handlers.spell_timers import SpellTimerHandler
from nparseplus.core.parsers.base import ParseContext
from nparseplus.core.spells.models import Spell
from nparseplus.core.timers import SpellRow, TimersService


class Recast:
    def __init__(self, value: str = "RestartCurrentTimer") -> None:
        self.value = value

    def __call__(self) -> str:
        return self.value


@pytest.fixture
def recast() -> Recast:
    return Recast()


@pytest.fixture
def handler(ctx: ParseContext, recast: Recast) -> SpellTimerHandler:
    assert ctx.spells is not None
    return SpellTimerHandler(ctx.bus, ctx.player, ctx.spells, TimersService(), timer_recast=recast)


def spell(ctx: ParseContext, name: str) -> Spell:
    assert ctx.spells is not None
    found = ctx.spells.spell_by_name(name)
    assert found is not None, name
    return found


def rows(handler: SpellTimerHandler) -> list[SpellRow]:
    return [r for r in handler.timers.snapshot() if isinstance(r, SpellRow)]


# "a frost giant scout" is in the master NPC list (is_npc -> True).
NPC = "a frost giant scout"


def test_restart_current_timer_refreshes(handler: SpellTimerHandler, ctx: ParseContext) -> None:
    dot = spell(ctx, "Curse of the Spirits")
    handler.handle_spell(dot, NPC, 0, T0)
    handler.handle_spell(dot, NPC, 0, T0 + timedelta(seconds=10))
    got = rows(handler)
    assert len(got) == 1
    assert got[0].updated_at == T0 + timedelta(seconds=10)


def test_start_new_timer_stacks_npc_detrimental(
    handler: SpellTimerHandler, ctx: ParseContext, recast: Recast
) -> None:
    recast.value = "StartNewTimer"
    dot = spell(ctx, "Curse of the Spirits")
    handler.handle_spell(dot, NPC, 0, T0)
    handler.handle_spell(dot, NPC, 0, T0 + timedelta(seconds=10))
    got = rows(handler)
    assert len(got) == 2
    assert {r.name for r in got} == {"Curse of the Spirits"}
    assert all(r.group == f" {NPC}" for r in got)


def test_start_new_timer_root_still_refreshes(
    handler: SpellTimerHandler, ctx: ParseContext, recast: Recast
) -> None:
    recast.value = "StartNewTimer"
    root = spell(ctx, "Ensnaring Roots")
    handler.handle_spell(root, NPC, 0, T0)
    handler.handle_spell(root, NPC, 0, T0 + timedelta(seconds=10))
    assert len(rows(handler)) == 1


def test_start_new_timer_beneficial_on_player_still_refreshes(
    handler: SpellTimerHandler, ctx: ParseContext, recast: Recast
) -> None:
    recast.value = "StartNewTimer"
    buff = spell(ctx, "Clarity")
    handler.handle_spell(buff, "Joe", 0, T0)
    handler.handle_spell(buff, "Joe", 0, T0 + timedelta(seconds=10))
    assert len(rows(handler)) == 1
