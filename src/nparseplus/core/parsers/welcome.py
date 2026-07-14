"""Login welcome-banner parser (port of EQTool WelcomeParser.cs)."""

from __future__ import annotations

from nparseplus.core.events import WelcomeEvent
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext

_WELCOME_LINE = "Welcome to EverQuest!"


class WelcomeParser:
    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:
        if line.message != _WELCOME_LINE:
            return False
        ctx.bus.publish(
            WelcomeEvent(timestamp=line.timestamp, line=line.message, line_number=line.line_number)
        )
        return True
