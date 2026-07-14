"""Experience gain parser (port of EQTool ExpGainedParser.cs)."""

from __future__ import annotations

import re

from nparseplus.core.events import ExpGainedEvent
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext

_EXP_RE = re.compile(r"^You gain (party )?experience!!")


class ExpGainedParser:
    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:
        message = line.message
        if not message.startswith("You gain "):
            return False
        if not _EXP_RE.match(message):
            return False
        ctx.bus.publish(
            ExpGainedEvent(timestamp=line.timestamp, line=message, line_number=line.line_number)
        )
        return True
