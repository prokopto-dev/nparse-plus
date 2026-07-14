"""Item click glow parser (port of EQTool YourItemBeginsToGlowParser.cs)."""

from __future__ import annotations

from nparseplus.core.events import YourItemBeginsToGlowEvent
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext

_YOUR = "Your "
_BEGINS_TO_GLOW = " begins to glow."
_YOUR_HAND = "Your hand begins to glow."


class YourItemBeginsToGlowParser:
    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:
        message = line.message
        if message == _YOUR_HAND:
            return False
        if not (message.startswith(_YOUR) and message.endswith(_BEGINS_TO_GLOW)):
            return False
        item_name = message[len(_YOUR) :].replace(_BEGINS_TO_GLOW, "").strip()
        if not item_name:
            return False
        ctx.bus.publish(
            YourItemBeginsToGlowEvent(
                timestamp=line.timestamp,
                line=message,
                line_number=line.line_number,
                item_name=item_name,
            )
        )
        return True
