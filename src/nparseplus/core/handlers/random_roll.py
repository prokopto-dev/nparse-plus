"""RandomRollHandler — /random results into RollRows.

Port of EQTool's Services/Handlers/RandomRollHandler.cs: every
RandomRollEvent becomes a RollRow named after the roller, grouped per
max-roll (`` Random -- <max>``, RollViewModel.MaxRoll setter) with a
3-minute window. ``TimersService.add_roll`` resets the window of every roll
already in the group, matching the C# UI behaviour.
"""

from __future__ import annotations

from datetime import timedelta

from nparseplus.core.bus import EventBus
from nparseplus.core.events import RandomRollEvent
from nparseplus.core.handlers.base import BaseHandler
from nparseplus.core.player import ActivePlayer
from nparseplus.core.timers import RollRow, TimersService

ROLL_WINDOW_SECONDS = 3 * 60


def roll_group(max_roll: int) -> str:
    return f" Random -- {max_roll}"


class RandomRollHandler(BaseHandler):
    def __init__(self, bus: EventBus, player: ActivePlayer, timers: TimersService) -> None:
        super().__init__(bus, player)
        self.timers = timers
        bus.subscribe(RandomRollEvent, self._on_roll)

    def _on_roll(self, event: RandomRollEvent) -> None:
        self.timers.add_roll(
            RollRow(
                name=event.player_name,
                group=roll_group(event.max_roll),
                updated_at=event.timestamp,
                roll=event.roll,
                max_roll=event.max_roll,
                ends_at=event.timestamp + timedelta(seconds=ROLL_WINDOW_SECONDS),
                total_duration_s=float(ROLL_WINDOW_SECONDS),
            )
        )
