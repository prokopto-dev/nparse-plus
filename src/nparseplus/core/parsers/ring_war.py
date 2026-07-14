"""Ring war start parser (port of EQTool RingWarParser.cs)."""

from __future__ import annotations

from nparseplus.core.events import RingWarEvent
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext

_RING_WAR_LINE = "Seneschal Aldikar shouts, TROOPS, TAKE YOUR POSITIONS!"


class RingWarParser:
    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:
        if line.message != _RING_WAR_LINE:
            return False
        ctx.bus.publish(
            RingWarEvent(timestamp=line.timestamp, line=line.message, line_number=line.line_number)
        )
        return True
