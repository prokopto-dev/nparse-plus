"""CorpseWaypointHandler — mark where you died (original-nparse port).

The legacy client wired the maps ``death`` signal to a corpse waypoint send;
here the core tracks the last parsed ``/loc`` and, on your own SlainEvent,
publishes a CorpseMarkerEvent. The maps window draws/persists the marker and
the SharingCoordinator forwards it over the nparse wire (see net/nparse_ws).

The last location is dropped on zone change and character switch — a corpse
marker at a stale cross-zone position is worse than none. If you die before
any ``/loc`` in the zone, no marker is made (same as the original, which
required a known position).
"""

from __future__ import annotations

from nparseplus.core.bus import EventBus
from nparseplus.core.events import (
    BeforePlayerChangedEvent,
    CorpseMarkerEvent,
    PlayerLocationEvent,
    SlainEvent,
    YouZonedEvent,
)
from nparseplus.core.geometry import Loc
from nparseplus.core.handlers.base import BaseHandler
from nparseplus.core.player import ActivePlayer


class CorpseWaypointHandler(BaseHandler):
    def __init__(self, bus: EventBus, player: ActivePlayer) -> None:
        super().__init__(bus, player)
        self._last_loc: Loc | None = None
        bus.subscribe(PlayerLocationEvent, self._on_location)
        bus.subscribe(SlainEvent, self._on_slain)
        bus.subscribe(YouZonedEvent, self._on_zoned)
        bus.subscribe(BeforePlayerChangedEvent, self._on_player_changed)

    def _on_location(self, event: PlayerLocationEvent) -> None:
        self._last_loc = event.location

    def _on_zoned(self, _event: YouZonedEvent) -> None:
        self._last_loc = None

    def _on_player_changed(self, _event: BeforePlayerChangedEvent) -> None:
        self._last_loc = None

    def _on_slain(self, event: SlainEvent) -> None:
        if event.victim != "You":
            return
        if self._last_loc is None or not self.player.zone or not self.player.name:
            return
        self.bus.publish(
            CorpseMarkerEvent(
                timestamp=event.timestamp,
                line=event.line,
                line_number=event.line_number,
                name=self.player.name,
                zone=self.player.zone,
                loc=self._last_loc,
            )
        )
