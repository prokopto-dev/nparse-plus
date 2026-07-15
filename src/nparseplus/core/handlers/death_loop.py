"""DeathLoopHandler — screams when you are being death-looped.

Port of EQTool's Services/Handlers/DeathLoopHandler.cs over the
``DeathLoopService`` state in ``nparseplus.core.death_loop``: four of your
own deaths within 120 seconds with no intervening You-activity (your melee
DamageEvents, YouBeginCastingEvents, or CommsEvents you sent) trigger the
alarm — TTS plus a "DEATH LOOP" OverlayEvent.
"""

from __future__ import annotations

from nparseplus.core.bus import EventBus
from nparseplus.core.death_loop import DeathLoopService
from nparseplus.core.enums import CommsChannel
from nparseplus.core.events import (
    CommsEvent,
    DamageEvent,
    OverlayEvent,
    SlainEvent,
    YouBeginCastingEvent,
)
from nparseplus.core.handlers.base import BaseHandler
from nparseplus.core.player import ActivePlayer
from nparseplus.core.triggers.engine import Speaker

DEATH_LOOP_TEXT = "DEATH LOOP"
DEATH_LOOP_TTS = "death loop death loop death loop. death loop!"


class DeathLoopHandler(BaseHandler):
    def __init__(
        self,
        bus: EventBus,
        player: ActivePlayer,
        speaker: Speaker | None = None,
        service: DeathLoopService | None = None,
    ) -> None:
        super().__init__(bus, player)
        self.speaker = speaker
        self.service = service if service is not None else DeathLoopService()
        bus.subscribe(SlainEvent, self._on_slain)
        bus.subscribe(DamageEvent, self._on_damage)
        bus.subscribe(YouBeginCastingEvent, self._on_begin_casting)
        bus.subscribe(CommsEvent, self._on_comms)

    def _on_slain(self, event: SlainEvent) -> None:
        self.service.prune(event.timestamp)
        if event.victim == "You" and self.service.record_death(event.timestamp):
            if self.speaker is not None:
                self.speaker.speak(DEATH_LOOP_TTS)
            self.bus.publish(OverlayEvent(text=DEATH_LOOP_TEXT, foreground="Red"))

    def _on_damage(self, event: DamageEvent) -> None:
        self.service.prune(event.timestamp)
        if event.attacker_name == "You":
            self.service.record_activity()

    def _on_begin_casting(self, event: YouBeginCastingEvent) -> None:
        self.service.record_activity()

    def _on_comms(self, event: CommsEvent) -> None:
        self.service.prune(event.timestamp)
        if event.channel != CommsChannel.NONE and event.sender == "You":
            self.service.record_activity()
