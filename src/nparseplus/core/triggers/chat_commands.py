"""Chat-driven custom timers — port of EQTool's CustomTimerHandler.

Watches every :class:`CommsEvent` (any channel that produces one — say, tell,
group, guild, auction, ooc, shout — from any sender, including yourself) for a
message *starting with*::

    PigTimer-<duration>[-<label>]
    StartTimer-<duration>[-<label>]

where ``<duration>`` is ``ss``, ``mm:ss`` or ``hh:mm:ss`` (no spaces anywhere).
When there is no label the timer is named after the matched command text
itself (e.g. ``PigTimer-02:03``). Examples::

    StartTimer-30                 30 second timer, named "StartTimer-30"
    PigTimer-10:00                10 minute timer
    PigTimer-120-description      120 second timer named "description"
    PigTimer-1:02:00-LongTimer    1 hour 2 minute timer named "LongTimer"
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from nparseplus.core.bus import EventBus, Unsubscribe
from nparseplus.core.events import CommsEvent
from nparseplus.core.triggers.engine import TimerSink
from nparseplus.core.triggers.model import TimerRestartBehavior

# EQTool uses Brushes.DarkSeaGreen and the Feign Death spell icon for these.
CUSTOM_TIMER_COLOR = "DarkSeaGreen"
CUSTOM_TIMER_ICON = "Feign Death"

_DURATION_AND_LABEL = (
    r"(?:(?:(?P<hh>[0-9]+):)?(?:(?P<mm>[0-9]+):))?(?P<ss>[0-9]+)(?:-(?P<label>.+))*"
)
_PIG_TIMER_RE = re.compile(r"^PigTimer-" + _DURATION_AND_LABEL)
_START_TIMER_RE = re.compile(r"^StartTimer-" + _DURATION_AND_LABEL)


@dataclass(frozen=True)
class ParsedCustomTimer:
    name: str
    seconds: int


def parse_custom_timer(content: str) -> ParsedCustomTimer | None:
    """Parse a chat message into a custom timer, or None when it isn't one."""
    if not content:
        return None
    match = _PIG_TIMER_RE.match(content) or _START_TIMER_RE.match(content)
    if match is None:
        return None
    seconds = int(match.group("ss"))
    if match.group("mm"):
        seconds += 60 * int(match.group("mm"))
    if match.group("hh"):
        seconds += 3600 * int(match.group("hh"))
    label = match.group("label")
    return ParsedCustomTimer(name=label if label else match.group(0), seconds=seconds)


class CustomTimerChatCommands:
    """CommsEvent subscriber that starts custom timers from chat commands."""

    def __init__(self, bus: EventBus, timers: TimerSink) -> None:
        self.timers = timers
        self._unsubscribe: Unsubscribe | None = bus.subscribe(CommsEvent, self._on_comms)

    def close(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    def _on_comms(self, event: CommsEvent) -> None:
        parsed = parse_custom_timer(event.content)
        if parsed is None:
            return
        self.timers.add_timer(
            parsed.name,
            parsed.seconds,
            CUSTOM_TIMER_COLOR,
            CUSTOM_TIMER_ICON,
            str(TimerRestartBehavior.START_NEW_TIMER),
        )
