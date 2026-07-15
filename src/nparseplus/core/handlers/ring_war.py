"""RingWarHandler — the Coldain Ring War wave schedule.

Port of EQTool's Services/Handlers/RingWarHandler.cs: when Seneschal Aldikar
calls troops to positions, lay down the full wave schedule — three waves of
seven rounds 210 seconds apart, with a 300-second break after each wave
(plus the C#'s extra 4 seconds after wave 3). Durations are cumulative from
the start announcement.
"""

from __future__ import annotations

from datetime import timedelta

from nparseplus.core.bus import EventBus
from nparseplus.core.events import RingWarEvent
from nparseplus.core.handlers.base import BaseHandler
from nparseplus.core.player import ActivePlayer
from nparseplus.core.timers import TimerRow, TimersService

ROUND_SECONDS = 210
BREAK_SECONDS = 300
WAVES = 3
ROUNDS_PER_WAVE = 7
FINAL_WAVE_EXTRA_SECONDS = 4


def wave_group(wave: int) -> str:
    return f" Wave {wave} Ring War"


class RingWarHandler(BaseHandler):
    def __init__(self, bus: EventBus, player: ActivePlayer, timers: TimersService) -> None:
        super().__init__(bus, player)
        self.timers = timers
        bus.subscribe(RingWarEvent, self._on_ring_war)

    def _on_ring_war(self, event: RingWarEvent) -> None:
        elapsed = 0
        for wave in range(1, WAVES + 1):
            for round_number in range(1, ROUNDS_PER_WAVE + 1):
                elapsed += ROUND_SECONDS
                self._add_timer(event, wave, f"Round {round_number}", elapsed)
            elapsed += BREAK_SECONDS
            if wave == WAVES:
                elapsed += FINAL_WAVE_EXTRA_SECONDS
            self._add_timer(event, wave, "-- Break --", elapsed)

    def _add_timer(self, event: RingWarEvent, wave: int, name: str, seconds: int) -> None:
        self.timers.add_timer(
            TimerRow(
                name=name,
                group=wave_group(wave),
                updated_at=event.timestamp,
                ends_at=event.timestamp + timedelta(seconds=seconds),
                total_duration_s=float(seconds),
            )
        )
