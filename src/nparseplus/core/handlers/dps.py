"""DpsHandler — feeds the FightTracker from bus events.

Port of the event wiring in EQTool's UI/DPSMeter.xaml.cs (DamageEvent ->
TryAdd, SlainEvent -> TargetDied) plus ConfirmedDeathEvent from
SlainHandler for the exp/faction-confirmed kills the slain line misses.

EQTool never cleared the DPS window on zoning or camping — rows simply aged
out via ShouldRemove. nparseplus clears active fights on zone change, camp,
and the loading screen instead, folding your stats into the session totals
first so nothing is lost.
"""

from __future__ import annotations

from nparseplus.core.bus import EventBus
from nparseplus.core.dps import FightTracker
from nparseplus.core.events import (
    CampEvent,
    ConfirmedDeathEvent,
    DamageEvent,
    LoadingPleaseWaitEvent,
    SlainEvent,
    YouZonedEvent,
)
from nparseplus.core.handlers.base import BaseHandler
from nparseplus.core.player import ActivePlayer

# Pseudo-victims SlainHandler emits for exp/faction-only confirmations; they
# never name a fight target.
_PSEUDO_VICTIMS = frozenset({"exp slain", "faction slain"})


class DpsHandler(BaseHandler):
    def __init__(self, bus: EventBus, player: ActivePlayer, tracker: FightTracker) -> None:
        super().__init__(bus, player)
        self.tracker = tracker
        bus.subscribe(DamageEvent, self._on_damage)
        bus.subscribe(SlainEvent, self._on_slain)
        bus.subscribe(ConfirmedDeathEvent, self._on_confirmed_death)
        bus.subscribe(YouZonedEvent, self._on_zoned)
        bus.subscribe(LoadingPleaseWaitEvent, self._on_loading)
        bus.subscribe(CampEvent, self._on_camp)

    def _on_damage(self, event: DamageEvent) -> None:
        self.tracker.add_damage(event)

    def _on_slain(self, event: SlainEvent) -> None:
        # Your own death arrives as victim == "You": EQTool just froze the
        # fights targeting You (TargetDied), same as any other victim.
        self.tracker.end_fight(event.victim, event.timestamp)

    def _on_confirmed_death(self, event: ConfirmedDeathEvent) -> None:
        if event.victim.casefold() in _PSEUDO_VICTIMS:
            return
        self.tracker.end_fight(event.victim, event.timestamp)

    def _on_zoned(self, event: YouZonedEvent) -> None:
        self.tracker.clear(update_stats_at=event.timestamp)

    def _on_loading(self, event: LoadingPleaseWaitEvent) -> None:
        self.tracker.clear(update_stats_at=event.timestamp)

    def _on_camp(self, event: CampEvent) -> None:
        self.tracker.clear(update_stats_at=event.timestamp)
