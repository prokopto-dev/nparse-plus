"""Pet message parser (port of EQTool PetParser.cs)."""

from __future__ import annotations

import re

from nparseplus.core.enums import PetIncident
from nparseplus.core.events import PetEvent
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext

_NO_PET_LINE = "You don't have a pet to command!"

# Checked in C# enum declaration order (NONE and ANY excluded). Patterns are
# byte-for-byte the C# ones (unescaped '.' included) to preserve semantics.
_INCIDENT_PATTERNS: tuple[tuple[PetIncident, re.Pattern[str]], ...] = (
    (
        PetIncident.LEADER,
        re.compile(r"^(?P<pet_name>[\w` ]+) says 'My leader is (?P<leader_name>[\w` ]+).'"),
    ),
    (PetIncident.RECLAIMED, re.compile(r"^(?P<pet_name>[\w` ]+) disperses.")),
    (
        PetIncident.DEATH,
        re.compile(r"^(?P<pet_name>[\w` ]+) says 'Sorry to have failed you, oh Great One.'"),
    ),
    (
        PetIncident.CREATION,
        re.compile(r"^(?P<pet_name>[\w` ]+) says 'At your service Master.'"),
    ),
    (
        PetIncident.GETLOST,
        re.compile(r"^(?P<pet_name>[\w` ]+) says 'As you wish, oh great one.'"),
    ),
    (
        PetIncident.PETATTACK,
        re.compile(r"^(?P<pet_name>[\w` ]+) tells you, 'Attacking (.*?) Master.'"),
    ),
    (PetIncident.PETLIFETAP, re.compile(r"^(?P<pet_name>[\w` ]+) beams a smile at [\w` ]+")),
    (
        PetIncident.PETFOLLOWME,
        re.compile(r"^(?P<pet_name>[\w` ]+) says 'Following you, Master.'"),
    ),
    (
        PetIncident.SITSTAND,
        re.compile(r"^(?P<pet_name>[\w` ]+) says 'Changing position, Master.'"),
    ),
    (
        PetIncident.GUARD,
        re.compile(r"^(?P<pet_name>[\w` ]+) says 'Guarding with my life..oh splendid one.'"),
    ),
)

# Cheap substrings that every pet pattern requires.
_FAST_PATH = ("says '", "disperses", "beams a smile at", "tells you, 'Attacking")


class PetParser:
    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:
        message = line.message
        if message == _NO_PET_LINE:
            ctx.bus.publish(
                PetEvent(
                    timestamp=line.timestamp,
                    line=message,
                    line_number=line.line_number,
                    pet_name="None",
                    incident=PetIncident.NONE,
                )
            )
            return True

        if not any(token in message for token in _FAST_PATH):
            return False

        for incident, pattern in _INCIDENT_PATTERNS:
            match = pattern.match(message)
            if match:
                ctx.bus.publish(
                    PetEvent(
                        timestamp=line.timestamp,
                        line=message,
                        line_number=line.line_number,
                        pet_name=match.group("pet_name"),
                        incident=incident,
                    )
                )
                return True
        return False
