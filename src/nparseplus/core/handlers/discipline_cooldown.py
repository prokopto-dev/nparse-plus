"""DisciplineCooldownHandler — melee discipline cooldown timers.

Port of EQTool's Services/Handlers/DisciplineCooldownHandler.cs: each
DisciplineCooldownEvent (already carrying its level-scaled cooldown seconds
from the parser) becomes a countdown TimerRow. The C# files these under the
shared custom-timer group; here they live in YOU_GROUP since a discipline
cooldown is always the player's own.
"""

from __future__ import annotations

from datetime import timedelta

from nparseplus.core.bus import EventBus
from nparseplus.core.events import DisciplineCooldownEvent
from nparseplus.core.handlers.base import BaseHandler
from nparseplus.core.player import ActivePlayer
from nparseplus.core.timers import YOU_GROUP, TimerRow, TimersService


class DisciplineCooldownHandler(BaseHandler):
    def __init__(self, bus: EventBus, player: ActivePlayer, timers: TimersService) -> None:
        super().__init__(bus, player)
        self.timers = timers
        bus.subscribe(DisciplineCooldownEvent, self._on_cooldown)

    def _on_cooldown(self, event: DisciplineCooldownEvent) -> None:
        seconds = event.total_timer_seconds
        self.timers.add_timer(
            TimerRow(
                name=event.discipline_name,
                group=YOU_GROUP,
                updated_at=event.timestamp,
                ends_at=event.timestamp + timedelta(seconds=seconds),
                total_duration_s=float(seconds),
            )
        )
