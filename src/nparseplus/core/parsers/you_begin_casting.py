"""'You begin casting <spell>.' — port of YouBeginCastingParser.cs."""

from __future__ import annotations

from nparseplus.core.enums import PlayerClass
from nparseplus.core.events import (
    ClassDetectedEvent,
    PlayerLevelDetectionEvent,
    YouBeginCastingEvent,
)
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext
from nparseplus.core.spells.matching import match_closest_level_to_spell
from nparseplus.core.spells.spells_us import DESCR_ILLUSION_OTHER, DESCR_ILLUSION_PLAYER

YOU_BEGIN_CASTING = "You begin casting "


class YouBeginCastingParser:
    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:
        if ctx.spells is None or not line.message.startswith(YOU_BEGIN_CASTING):
            return False
        # C# substrings at Length-1 to keep double-spaced names intact.
        spell_name = line.message[len(YOU_BEGIN_CASTING) - 1 :].strip().rstrip(".")
        candidates = ctx.spells.you_cast(spell_name)
        if not candidates:
            return False
        spell = match_closest_level_to_spell(candidates, ctx.player.player_class, ctx.player.level)
        if spell is None:
            return False

        if len(spell.class_levels) == 1:
            (found_class, found_level), *_ = spell.class_levels.items()
            if (
                ctx.player.player_class is PlayerClass.ENCHANTER
                and PlayerClass.ENCHANTER in spell.class_levels
            ):
                ctx.bus.publish(
                    PlayerLevelDetectionEvent(
                        timestamp=line.timestamp,
                        line=line.message,
                        line_number=line.line_number,
                        player_level=found_level,
                    )
                )
            else:
                if spell.descr_number not in (DESCR_ILLUSION_PLAYER, DESCR_ILLUSION_OTHER):
                    ctx.bus.publish(
                        ClassDetectedEvent(
                            timestamp=line.timestamp,
                            line=line.message,
                            line_number=line.line_number,
                            player_class=found_class,
                        )
                    )
                ctx.bus.publish(
                    PlayerLevelDetectionEvent(
                        timestamp=line.timestamp,
                        line=line.message,
                        line_number=line.line_number,
                        player_level=found_level,
                    )
                )

        ctx.bus.publish(
            YouBeginCastingEvent(
                timestamp=line.timestamp,
                line=line.message,
                line_number=line.line_number,
                spell=spell,
            )
        )
        return True
