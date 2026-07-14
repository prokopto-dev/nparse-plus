"""Earthquake (server-wide event) parser (port of EQTool QuakeParser.cs)."""

from __future__ import annotations

from nparseplus.core.events import QuakeEvent
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext

_QUAKE_LINES = frozenset(
    (
        "You feel you should get somewhere safe as soon as possible.",
        "The gods have awoken to unleash their wrath across Norrath.",
        "An unsettling silence smothers the land. Not a complete silence, but somehow "
        "quieter for it, the way a thick blanket of snow muffles the noise of the world. "
        "The chill of it pierces your bones, and you know, danger approaches.",
        "You feel the need to get somewhere safe quickly.",
    )
)


class QuakeParser:
    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:
        if line.message not in _QUAKE_LINES:
            return False
        ctx.bus.publish(
            QuakeEvent(timestamp=line.timestamp, line=line.message, line_number=line.line_number)
        )
        return True
