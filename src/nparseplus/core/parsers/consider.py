"""/consider parser (port of EQTool ConLogParse.cs)."""

from __future__ import annotations

from nparseplus.core.events import ConEvent
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext

_CON_MESSAGES = (
    "regards you as an ally",
    "looks upon you warmly",
    "kindly considers you",
    "judges you amiably",
    "regards you indifferently",
    "looks your way apprehensively",
    "glowers at you dubiously",
    "glares at you threateningly",
    "scowls at you, ready to attack",
)


class ConLogParse:
    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:
        message = line.message
        for con_message in _CON_MESSAGES:
            index = message.find(con_message)
            if index == -1:
                continue
            name = message[:index].strip()
            if not name:
                return False
            ctx.bus.publish(
                ConEvent(
                    timestamp=line.timestamp,
                    line=message,
                    line_number=line.line_number,
                    name=name,
                )
            )
            return True
        return False
