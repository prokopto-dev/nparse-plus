"""YouZonedHandler — keeps ActivePlayer.zone current (YouZonedHandler.cs).

The C# also persists the zone into the saved per-character settings on every
change; our PlayerInfo profiles are written by the UI save path instead, so
this port only updates the live session state.
"""

from __future__ import annotations

from nparseplus.core.bus import EventBus
from nparseplus.core.events import YouZonedEvent
from nparseplus.core.player import ActivePlayer


class YouZonedHandler:
    def __init__(self, bus: EventBus, player: ActivePlayer) -> None:
        self.player = player
        bus.subscribe(YouZonedEvent, self._on_zoned)

    def _on_zoned(self, event: YouZonedEvent) -> None:
        if self.player.zone != event.short_name:
            self.player.zone = event.short_name
