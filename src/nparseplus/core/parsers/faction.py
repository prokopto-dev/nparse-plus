"""Faction standing parser (port of EQTool FactionParser.cs)."""

from __future__ import annotations

from nparseplus.core.enums import FactionStatus
from nparseplus.core.events import FactionEvent
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext

_START = "Your faction standing with"
_GOT_BETTER = "got better."
_GOT_WORSE = "got worse."
_COULD_NOT_GET_BETTER = "could not possibly get any better."
_COULD_NOT_GET_WORSE = "could not possibly get any worse."


class FactionParser:
    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:
        message = line.message
        if not message.startswith(_START):
            return False
        faction = (
            message.replace(_START, "")
            .replace(_GOT_BETTER, "")
            .replace(_GOT_WORSE, "")
            .replace(_COULD_NOT_GET_BETTER, "")
            .replace(_COULD_NOT_GET_WORSE, "")
            .strip()
        )
        status = FactionStatus.GOT_BETTER
        if _GOT_WORSE in message:
            status = FactionStatus.GOT_WORSE
        elif _COULD_NOT_GET_BETTER in message:
            status = FactionStatus.COULD_NOT_GET_BETTER
        elif _COULD_NOT_GET_WORSE in message:
            status = FactionStatus.COULD_NOT_GET_WORSE
        ctx.bus.publish(
            FactionEvent(
                timestamp=line.timestamp,
                line=message,
                line_number=line.line_number,
                faction=faction,
                status=status,
            )
        )
        return True
