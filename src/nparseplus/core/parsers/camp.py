"""/camp parser (port of EQTool CampParser.cs).

Like EQTool, the CampEvent fires ~6 seconds after the "5 more seconds"
notice unless camping was abandoned in the meantime. The delay is published
from a background timer thread (EQTool uses a Task the same way);
``camp_delay_seconds`` is overridable for tests.
"""

from __future__ import annotations

import threading

from nparseplus.core.events import CampEvent
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext

_CAMP_START = "It will take about 5 more seconds to prepare your camp."
_CAMP_ABANDON = "You abandon your preparations to camp."


class CampParser:
    camp_delay_seconds: float = 6.0

    def __init__(self) -> None:
        self._still_camping = False
        self._timer: threading.Timer | None = None

    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:
        if line.message == _CAMP_START:
            self._still_camping = True
            timer = threading.Timer(self.camp_delay_seconds, self._fire, args=(line, ctx))
            timer.daemon = True
            self._timer = timer
            timer.start()
            return True
        if line.message == _CAMP_ABANDON:
            self._still_camping = False
            return True
        return False

    def _fire(self, line: LineInfo, ctx: ParseContext) -> None:
        if self._still_camping:
            self._still_camping = False
            ctx.bus.publish(
                CampEvent(timestamp=line.timestamp, line=line.message, line_number=line.line_number)
            )
