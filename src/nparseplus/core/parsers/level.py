"""Level-up parser (port of EQTool LevelLogParse.cs / PlayerLevelDetectionParser)."""

from __future__ import annotations

from nparseplus.core.events import PlayerLevelDetectionEvent
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext

_YOU_HAVE_GAINED_A_LEVEL = "You have gained a level! Welcome to level"


class PlayerLevelDetectionParser:
    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:
        message = line.message
        if not message.startswith(_YOU_HAVE_GAINED_A_LEVEL):
            return False
        level_string = message.replace(_YOU_HAVE_GAINED_A_LEVEL, "").strip().rstrip("!")
        try:
            level = int(level_string)
        except ValueError:
            return False
        ctx.bus.publish(
            PlayerLevelDetectionEvent(
                timestamp=line.timestamp,
                line=message,
                line_number=line.line_number,
                player_level=level,
            )
        )
        return True
