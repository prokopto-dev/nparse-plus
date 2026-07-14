"""'Your spell is interrupted.' — port of YourSpellInterruptedParser.cs."""

from __future__ import annotations

from nparseplus.core.events import YourSpellInterruptedEvent
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext

YOUR_SPELL_IS_INTERRUPTED = "Your spell is interrupted."


class YourSpellInterruptedParser:
    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:
        if line.message != YOUR_SPELL_IS_INTERRUPTED:
            return False
        ctx.bus.publish(
            YourSpellInterruptedEvent(
                timestamp=line.timestamp,
                line=line.message,
                line_number=line.line_number,
            )
        )
        return True
