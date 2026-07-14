"""Spells landing on other targets — port of SpellCastOnOtherParser.cs.

Matching strategy, in order:
1. "You lose control of yourself!" -> DragonRoarEvent (Dragon Roar).
2. Possessive messages: match from the first apostrophe ("Joe's skin ..."),
   which also covers "Someone's ..." lines.
3. The spell the user is mid-casting (cast_on_you self-landing or
   cast_on_other suffix match).
4. Progressive target extraction: peel 1-5 words off the front and look the
   remainder up in the cast_on_other table (with EQTool's Tsunami /
   Waves of the Deep Sea / Malisement disambiguation).

EQTool's zone-AOE ("dragon roar") candidate promotion needs fight history and
is not ported; candidates flow through SpellCastOnOtherEvent as-is.
"""

from __future__ import annotations

from nparseplus.core.enums import PlayerClass, SpellType
from nparseplus.core.events import (
    DragonRoarEvent,
    SpellCastOnOtherEvent,
    YouFinishCastingEvent,
)
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext
from nparseplus.core.spells.matching import iter_target_splits, possessive_message
from nparseplus.core.spells.models import Spell
from nparseplus.core.spells.spells_us import SPACE_YOU

_IGNORE_SPELLS_FOR_GUESSES = frozenset({"Tigir's Insects"})
_LOSE_CONTROL = "You lose control of yourself!"


class SpellCastOnOtherParser:
    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:
        if ctx.spells is None:
            return False
        message = line.message
        if message.startswith("Your "):
            return False

        if message == _LOSE_CONTROL:
            dragon_roar = ctx.spells.spell_by_name("Dragon Roar")
            if dragon_roar is not None:
                ctx.bus.publish(
                    DragonRoarEvent(
                        timestamp=line.timestamp,
                        line=message,
                        line_number=line.line_number,
                        spell=dragon_roar,
                    )
                )
                return True

        possessive = possessive_message(message)
        if possessive is not None:
            found = ctx.spells.cast_on_other(possessive)
            if found:
                target = message.replace(found[0].cast_on_other, "").strip()
                ctx.bus.publish(
                    SpellCastOnOtherEvent(
                        timestamp=line.timestamp,
                        line=message,
                        line_number=line.line_number,
                        spells=tuple(found),
                        target_name=target,
                    )
                )
                return True

        casting = ctx.spells.casting
        if casting.spell is not None and casting.started_at is not None:
            spell = casting.spell
            if message == spell.cast_on_you:
                ctx.bus.publish(
                    YouFinishCastingEvent(
                        timestamp=line.timestamp,
                        line=message,
                        line_number=line.line_number,
                        spell=spell,
                        target_name=SPACE_YOU,
                    )
                )
                return True
            if spell.cast_on_other and message.endswith(spell.cast_on_other):
                ctx.bus.publish(
                    SpellCastOnOtherEvent(
                        timestamp=line.timestamp,
                        line=message,
                        line_number=line.line_number,
                        spells=(spell,),
                        target_name=message.replace(spell.cast_on_other, "").strip(),
                    )
                )
                return True

        for target, spell_message in iter_target_splits(message):
            if self._match(target, spell_message, line, ctx):
                return True
        return False

    def _match(
        self, target_name: str, spell_message: str, line: LineInfo, ctx: ParseContext
    ) -> bool:
        assert ctx.spells is not None
        found = ctx.spells.cast_on_other(spell_message)
        if not found:
            return False
        candidates: list[Spell] = [
            s
            for s in found
            if (
                s.name not in _IGNORE_SPELLS_FOR_GUESSES
                and s.spell_type is not SpellType.TARGETED_AOE
            )
            or s.name.casefold() == "wake of tranquility"
        ]
        if not candidates:
            return False

        if ctx.spells.is_npc(target_name):
            candidates = [s for s in candidates if s.name != "Tsunami"]
            target_name = " " + target_name
        else:
            candidates = [s for s in candidates if s.name != "Waves of the Deep Sea"]
        if ctx.player.player_class is PlayerClass.WIZARD:
            # A wizard is probably seeing the flux staff, not Malisement.
            candidates = [s for s in candidates if s.name != "Malisement"]
        if not candidates:
            return False

        ctx.bus.publish(
            SpellCastOnOtherEvent(
                timestamp=line.timestamp,
                line=line.message,
                line_number=line.line_number,
                spells=tuple(candidates),
                target_name=target_name,
            )
        )
        return True
