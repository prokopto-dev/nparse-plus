"""Death message parser (port of EQTool SlainParser.cs)."""

from __future__ import annotations

import re

from nparseplus.core.events import SlainEvent
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext

_SLAIN_BY_RE = re.compile(r"^(?P<victim>[\w` ]+) (has|have) been slain by (?P<killer>[\w` ]+)")
_SLAIN_RE = re.compile(r"^You have slain (?P<victim>[\w` ]+)")
_DIED_RE = re.compile(r"^(?P<victim>[\w` ]+) died\.$")
_EYE_OF = "Eye of"


class SlainParser:
    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:
        message = line.message
        if "slain" not in message and not message.endswith("died."):
            return False

        match = _SLAIN_BY_RE.match(message)
        if match:
            victim = match.group("victim")
            if victim.startswith(_EYE_OF):
                return False
            ctx.bus.publish(
                SlainEvent(
                    timestamp=line.timestamp,
                    line=message,
                    line_number=line.line_number,
                    victim=victim,
                    killer=match.group("killer"),
                )
            )
            return True

        match = _SLAIN_RE.match(message)
        if match:
            victim = match.group("victim")
            if victim.startswith(_EYE_OF):
                return False
            ctx.bus.publish(
                SlainEvent(
                    timestamp=line.timestamp,
                    line=message,
                    line_number=line.line_number,
                    victim=victim,
                    killer="You",
                )
            )
            return True

        match = _DIED_RE.match(message)
        if match:
            ctx.bus.publish(
                SlainEvent(
                    timestamp=line.timestamp,
                    line=message,
                    line_number=line.line_number,
                    victim=match.group("victim"),
                    killer="",
                )
            )
            return True

        return False
