"""/camp parser (port of EQTool CampParser.cs).

Like EQTool, the CampEvent fires ~6 seconds after the "5 more seconds"
notice unless camping was abandoned in the meantime; ``camp_delay_seconds``
is overridable for tests.

**The delay is resolved on the driver tick, not on a timer thread.** EQTool
uses a Task and this parser used a ``threading.Timer`` daemon thread, which
meant every ``CampEvent`` subscriber ran off the driver thread — already a
latent data race for ``FightTracker.clear`` and the sharing keepalive state,
and a hard blocker for the timer-persistence subscriber, since
``TimersService`` is not thread-safe and only the driver tick may touch it.
So the start line records a deadline, the abandon line clears it, and
``tick`` (registered on ``LogDriver.on_tick``) publishes the event once the
deadline passes. Same 6 s wall-clock delay, same abandon semantics, one
thread.

The deadline is wall-clock (``clock``, injectable) rather than log time: the
delay models the client finishing its camp-out in the real world, and a
catch-up read of a backlog must not fire it instantly.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from nparseplus.core.bus import EventBus
from nparseplus.core.events import CampEvent
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext

_CAMP_START = "It will take about 5 more seconds to prepare your camp."
_CAMP_ABANDON = "You abandon your preparations to camp."


class CampParser:
    camp_delay_seconds: float = 6.0

    def __init__(self, clock: Callable[[], datetime] = datetime.now) -> None:
        self._clock = clock
        self._deadline: datetime | None = None
        self._pending: LineInfo | None = None
        self._bus: EventBus | None = None

    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:
        if line.message == _CAMP_START:
            self._pending = line
            self._bus = ctx.bus
            self._deadline = self._clock() + timedelta(seconds=self.camp_delay_seconds)
            return True
        if line.message == _CAMP_ABANDON:
            self._cancel()
            return True
        return False

    def tick(self, now: datetime) -> None:
        """Publish the pending CampEvent once its delay has elapsed.

        Runs on the driver thread (``LogDriver.on_tick``), which is what makes
        it safe for a subscriber to touch ``TimersService``.
        """
        if self._deadline is None or now < self._deadline:
            return
        line = self._pending
        bus = self._bus
        self._cancel()
        if line is None or bus is None:  # pragma: no cover - defensive
            return
        bus.publish(
            CampEvent(timestamp=line.timestamp, line=line.message, line_number=line.line_number)
        )

    def _cancel(self) -> None:
        self._deadline = None
        self._pending = None
        self._bus = None
