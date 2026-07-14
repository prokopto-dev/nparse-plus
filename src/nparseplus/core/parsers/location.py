"""/loc parser (port of EQTool LocationParser.cs).

The client prints ``Your Location is <y>, <x>, <z>`` — per the ``Loc``
contract in :mod:`nparseplus.core.geometry`, the parser normalizes the
triple to (x, y, z). (EQTool keeps the raw client order.)
"""

from __future__ import annotations

from nparseplus.core.events import PlayerLocationEvent
from nparseplus.core.geometry import Loc
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext

_YOUR_LOCATION_IS = "Your Location is "


class LocationParser:
    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:
        message = line.message
        if not message.startswith(_YOUR_LOCATION_IS):
            return False
        parts = message.replace(_YOUR_LOCATION_IS, "").strip().split(",")
        y, x, z = (float(part.strip()) for part in parts[:3])
        ctx.bus.publish(
            PlayerLocationEvent(
                timestamp=line.timestamp,
                line=message,
                line_number=line.line_number,
                location=Loc(x=x, y=y, z=z),
            )
        )
        return True
