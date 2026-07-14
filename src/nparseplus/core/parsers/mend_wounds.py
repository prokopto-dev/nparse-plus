"""Monk mend parser (port of EQTool MendWoundsParser.cs).

Bug-compatible with the C# original: the event is published but the line is
never consumed (Handle always returns false).
"""

from __future__ import annotations

from nparseplus.core.events import MendWoundsEvent
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext

_MEND_LINES = (
    "You mend your wounds and heal some damage.",
    "You have failed to mend your wounds.",
)


class MendWoundsParser:
    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:
        if line.message in _MEND_LINES:
            ctx.bus.publish(
                MendWoundsEvent(
                    timestamp=line.timestamp, line=line.message, line_number=line.line_number
                )
            )
        return False
