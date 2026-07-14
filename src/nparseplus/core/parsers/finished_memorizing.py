"""'You have finished memorizing <spell>.' — port of
YouHaveFinishedMemorizingParser.cs."""

from __future__ import annotations

from nparseplus.core.events import (
    ClassDetectedEvent,
    PlayerLevelDetectionEvent,
    YouHaveFinishedMemorizingEvent,
)
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext

YOU_HAVE_FINISHED_MEMORIZING = "You have finished memorizing "


class YouHaveFinishedMemorizingParser:
    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:
        if not line.message.startswith(YOU_HAVE_FINISHED_MEMORIZING):
            return False
        spell_name = line.message.replace(YOU_HAVE_FINISHED_MEMORIZING, "").strip(" .")
        ctx.bus.publish(
            YouHaveFinishedMemorizingEvent(
                timestamp=line.timestamp,
                line=line.message,
                line_number=line.line_number,
                spell_name=spell_name,
            )
        )
        if ctx.spells is not None:
            spell = ctx.spells.spell_by_name(spell_name)
            if spell is not None and len(spell.class_levels) == 1:
                (found_class, found_level), *_ = spell.class_levels.items()
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
        return True
