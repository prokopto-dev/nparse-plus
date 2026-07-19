"""Boat departure-announcement parser (port of EQTool BoatParser.cs)."""

from __future__ import annotations

from nparseplus.core.events import BoatEvent
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext


class BoatParser:
    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:
        # The start-announcement table lives in data/zones.json (the "boats"
        # section, BoatInfo entries with a non-empty StartAnnoucement from
        # EQToolShared/Zones.cs) and is read via the shared ZoneDatabase rather
        # than duplicated here.
        if ctx.zones is None:
            return False
        message = line.message
        for boat in ctx.zones.boats:
            announcement = boat.start_announcement
            if announcement and message.startswith(announcement):
                ctx.bus.publish(
                    BoatEvent(
                        timestamp=line.timestamp,
                        line=message,
                        line_number=line.line_number,
                        boat=boat.boat,
                        start_point=boat.start_point,
                    )
                )
                return True
        return False
