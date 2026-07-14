"""/random roll parser (port of EQTool RandomParser.cs).

A roll spans two lines::

    **A Magic Die is rolled by Whitewitch.
    **It could have been any number from 0 to 100, but this time it turned up a 42.

The first line stashes the roller's name (and is NOT consumed, matching the
C# behavior of returning null for it); the second publishes the event if it
arrives within 2 seconds of wall clock.
"""

from __future__ import annotations

import time

from nparseplus.core.events import RandomRollEvent
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext

_ROLL_MESSAGE = "**A Magic Die is rolled by "
_ROLL_MESSAGE_2ND = "**It could have been any number from 0 to "
_ROLL_MESSAGE_3RD = "but this time it turned up a "


class RandomParser:
    def __init__(self) -> None:
        self._player_roll_name = ""
        self._roll_time: float | None = None

    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:
        message = line.message
        if message.startswith(_ROLL_MESSAGE):
            self._player_roll_name = message[len(_ROLL_MESSAGE) :].rstrip(".")
            self._roll_time = time.monotonic()
            return False

        if not message.startswith(_ROLL_MESSAGE_2ND):
            return False

        if self._roll_time is None or (time.monotonic() - self._roll_time) > 2:
            self._player_roll_name = ""
            self._roll_time = None
            return False

        max_roll = message[len(_ROLL_MESSAGE_2ND) :]
        comma_index = max_roll.find(",")
        if comma_index != -1:
            max_roll = max_roll[:comma_index]
        third_index = message.find(_ROLL_MESSAGE_3RD)
        if third_index == -1:
            return False
        roll = message[third_index + len(_ROLL_MESSAGE_3RD) :].rstrip(".")
        try:
            roll_int = int(roll)
            max_roll_int = int(max_roll)
        except ValueError:
            return False
        ctx.bus.publish(
            RandomRollEvent(
                timestamp=line.timestamp,
                line=message,
                line_number=line.line_number,
                player_name=self._player_roll_name,
                max_roll=max_roll_int,
                roll=roll_int,
            )
        )
        return True
