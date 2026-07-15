"""MendWoundsHandler — the monk Mend cooldown.

Port of EQTool's Services/Handlers/MendWoundsHandler.cs: a MendWoundsEvent
starts the fixed 6-minute "Mend" reuse timer in the player's own group (the
C# renders it as a SpellViewModel with a heal-spell icon; the icon choice is
a UI concern here).
"""

from __future__ import annotations

from datetime import timedelta

from nparseplus.core.bus import EventBus
from nparseplus.core.events import MendWoundsEvent
from nparseplus.core.handlers.base import BaseHandler
from nparseplus.core.player import ActivePlayer
from nparseplus.core.timers import YOU_GROUP, TimerRow, TimersService

MEND_TIMER_NAME = "Mend"
MEND_COOLDOWN_SECONDS = 6 * 60


class MendWoundsHandler(BaseHandler):
    def __init__(self, bus: EventBus, player: ActivePlayer, timers: TimersService) -> None:
        super().__init__(bus, player)
        self.timers = timers
        bus.subscribe(MendWoundsEvent, self._on_mend)

    def _on_mend(self, event: MendWoundsEvent) -> None:
        self.timers.add_timer(
            TimerRow(
                name=MEND_TIMER_NAME,
                group=YOU_GROUP,
                updated_at=event.timestamp,
                ends_at=event.timestamp + timedelta(seconds=MEND_COOLDOWN_SECONDS),
                total_duration_s=float(MEND_COOLDOWN_SECONDS),
            )
        )
