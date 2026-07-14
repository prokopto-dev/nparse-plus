"""Zone-change parser (port of EQTool YouZonedParser.cs).

Detects "You have entered <zone>." and the "/who" style
"There are X players in <zone>." / "There is 1 player in <zone>." lines.

Divergence from EQTool: the C# parser only publishes when the long name
translates to a known map short name (Zones.TranslateToMapName). Until the
zone database is wired into ParseContext, this port publishes the detected
(lowercased) long name with a best-effort short name.
"""

from __future__ import annotations

from nparseplus.core.events import YouZonedEvent
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext

_YOU_HAVE_ENTERED = "You have entered "
_THERE_ARE_NO_PLAYERS = "There are no players "
_THERE_ARE = "There are "
_THERE_IS = "There is "
_ARENA_PVP = "You have entered an Arena (PvP) area."
_IN = "in "


def _best_effort_short_name(long_name: str) -> str:
    """Placeholder long→short zone-name mapping.

    TODO: replace with a ZoneDatabase lookup backed by data/zones.json
    (aliases.zone_name_mapper / aliases.zone_who_mapper tables).
    """
    return long_name.lower().replace(" ", "")


def _zone_changed(message: str) -> str:
    """Return the lowercased long zone name, or "" when not a zone line."""
    if message in (_ARENA_PVP, _THERE_ARE_NO_PLAYERS):
        return ""
    if message.startswith(_YOU_HAVE_ENTERED):
        return message.replace(_YOU_HAVE_ENTERED, "").strip().rstrip(".").lower()
    if message.startswith(_THERE_ARE):
        rest = message.replace(_THERE_ARE, "").strip()
        in_index = rest.find(_IN)
        if in_index != -1:
            rest = rest[in_index + len(_IN) :].strip().rstrip(".").lower()
            if rest != "everquest":
                return rest
    elif message.startswith(_THERE_IS):
        rest = message.replace(_THERE_IS, "").strip()
        in_index = rest.find(_IN)
        if in_index != -1:
            rest = rest[in_index + len(_IN) :].strip().rstrip(".").lower()
            if rest != "everquest":
                return rest
    return ""


class YouZonedParser:
    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:
        long_name = _zone_changed(line.message)
        if not long_name.strip():
            return False
        ctx.bus.publish(
            YouZonedEvent(
                timestamp=line.timestamp,
                line=line.message,
                line_number=line.line_number,
                long_name=long_name,
                short_name=_best_effort_short_name(long_name),
            )
        )
        return True
