"""Discipline cooldown parser (port of EQTool DisciplineCooldownParser.cs).

Matches "You can use the ability <disc> again in N minute(s) M seconds."
"""

from __future__ import annotations

import re

from nparseplus.core.events import DisciplineCooldownEvent
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext

_COOLDOWN_PREFIX = "You can use the ability "
_COOLDOWN_RE = re.compile(
    r"^You can use the ability (?P<discname>[\w` ]+) again in (?P<mm>[0-9]+)"
    r" minute\(s\) (?P<ss>[0-9]+) seconds."
)


class DisciplineCooldownParser:
    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:
        message = line.message
        if not message.startswith(_COOLDOWN_PREFIX):
            return False
        match = _COOLDOWN_RE.match(message)
        if not match:
            return False
        total_seconds = int(match.group("ss")) + 60 * int(match.group("mm"))
        ctx.bus.publish(
            DisciplineCooldownEvent(
                timestamp=line.timestamp,
                line=message,
                line_number=line.line_number,
                discipline_name=match.group("discname"),
                total_timer_seconds=total_seconds,
            )
        )
        return True
