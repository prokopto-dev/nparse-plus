"""Boat departure-announcement parser (port of EQTool BoatParser.cs)."""

from __future__ import annotations

from nparseplus.core.events import BoatEvent
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext

# (start announcement, boat key, start point) — from EQToolShared/Zones.cs
# BoatInfo entries with a non-empty StartAnnoucement.
# TODO: load from data/zones.json "boats" table (start_announcement / boat /
# start_point fields) once a shared zone database is wired into ParseContext.
_BOAT_ANNOUNCEMENTS: tuple[tuple[str, str, str], ...] = (
    (
        "Rack Stonebelly shouts, 'Da Barrel Barge will be here soon soon!'",
        "BarrelBarge",
        "oasis",
    ),
    (
        "Rack Stonebelly shouts, 'Da Bloated Belly be leaving da Overdere now!'",
        "BloatedBelly",
        "overthere",
    ),
    (
        "Glisse Bluesea shouts 'The Maiden's Voyage is now ready to be boarded. "
        "Please form an orderly line to the shuttles, and remember, no pushing!",
        "MaidensVoyage",
        "butcher",
    ),
    (
        "Glisse Bluesea shouts 'The Maiden's Voyage has departed the outpost at "
        "Firiona Vie. Please be ready to board the shuttles shortly, if you desire "
        "to make the journey to Kunark.",
        "MaidensVoyage",
        "firiona",
    ),
    (
        "Frankel the Pirate says 'Thar she be mates. All aboard thats goin aboard!'",
        "NroIcecladBoat",
        "nro",
    ),
)


class BoatParser:
    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:
        message = line.message
        for announcement, boat, start_point in _BOAT_ANNOUNCEMENTS:
            if message.startswith(announcement):
                ctx.bus.publish(
                    BoatEvent(
                        timestamp=line.timestamp,
                        line=message,
                        line_number=line.line_number,
                        boat=boat,
                        start_point=start_point,
                    )
                )
                return True
        return False
