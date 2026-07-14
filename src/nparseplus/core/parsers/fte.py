"""First-to-engage shout parser (port of EQTool FTEParser.cs).

Matches "<npc name> engages <playername>!" where the player name is a
single word (the last " engages " must be followed only by the name).
"""

from __future__ import annotations

from nparseplus.core.events import FTEEvent
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext

_ENGAGES = " engages "


class FTEParser:
    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:
        message = line.message
        if not message.endswith("!"):
            return False
        last_space_index = message.rfind(" ")
        if last_space_index == -1:
            return False
        engages_index = message.rfind(_ENGAGES)
        if engages_index == -1:
            return False
        if last_space_index != engages_index + len(_ENGAGES) - 1:
            return False
        player_name = message[engages_index + len(_ENGAGES) :].rstrip("!").strip()
        npc_name = message[:engages_index].strip()
        ctx.bus.publish(
            FTEEvent(
                timestamp=line.timestamp,
                line=message,
                line_number=line.line_number,
                npc_name=npc_name,
                fte_person=player_name,
            )
        )
        return True
