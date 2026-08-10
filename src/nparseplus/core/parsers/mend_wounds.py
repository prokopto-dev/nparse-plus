"""Monk mend parser (port of EQTool MendWoundsParser.cs).

Bug-compatible with the C# original: the event is published but the line is
never consumed (Handle always returns false).

Deliberate divergence: EQTool only matches the plain success and plain
failure lines, so a monk who crit-mended or worsened their wounds got no
reuse timer at all. Mend has FOUR outcomes and the 6-minute reuse starts on
every one of them, so all four are matched here.
"""

from __future__ import annotations

from nparseplus.core.events import MendWoundsEvent
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext

_MEND_LINES = (
    # Success / critical success.
    "You mend your wounds and heal some damage.",
    "You magically mend your wounds and heal considerable damage.",
    # Failure / critical failure (below 100 skill a failure can wound you).
    "You have failed to mend your wounds.",
    "You have worsened your wounds!",
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
