"""ConHandler — records the last-considered mob for the MobInfo UI.

Port of EQTool's Services/Handlers/ConHandler.cs, minus the network parts:
the C# fetches wiki loot data and PigParse item prices on every con; here we
only keep local state (name, pet flag, spawn time, notable flag) that the
MobInfo window reads via the ``on_change`` hook.

TODO(M3): wiki/PigParse enrichment of the considered mob.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from nparseplus.core.bus import EventBus
from nparseplus.core.events import ConEvent
from nparseplus.core.handlers.base import BaseHandler
from nparseplus.core.pets import PlayerPet
from nparseplus.core.player import ActivePlayer
from nparseplus.core.zones import ZoneDatabase


@dataclass
class MobInfoState:
    """The last-considered mob (MobInfoViewModel's Qt-free core)."""

    name: str = ""
    zone: str = ""
    is_pet: bool = False
    spawn_seconds: int | None = None
    is_notable: bool = False
    on_change: list[Callable[[MobInfoState], None]] = field(default_factory=list)

    def _notify(self) -> None:
        for callback in list(self.on_change):
            callback(self)


class ConHandler(BaseHandler):
    def __init__(
        self,
        bus: EventBus,
        player: ActivePlayer,
        zones: ZoneDatabase,
        player_pet: PlayerPet | None = None,
        mob_info: MobInfoState | None = None,
    ) -> None:
        super().__init__(bus, player)
        self.zones = zones
        self.player_pet = player_pet
        self.mob_info = mob_info if mob_info is not None else MobInfoState()
        bus.subscribe(ConEvent, self._on_con)

    def _on_con(self, event: ConEvent) -> None:
        info = self.mob_info
        if self.player_pet is not None and event.name == self.player_pet.pet_name:
            info.is_pet = True
            info.name = event.name
            info.zone = self.player.zone
            info.spawn_seconds = None
            info.is_notable = False
            info._notify()
            return

        if event.name == info.name and not info.is_pet:
            return  # C# skips the refetch when the same mob is conned again

        zone = self.zones.get(self.player.zone) if self.player.zone else None
        notable = zone is not None and any(
            npc.casefold() == event.name.casefold() for npc in zone.notable_npcs
        )
        info.is_pet = False
        info.name = event.name
        info.zone = self.player.zone
        info.spawn_seconds = self.zones.spawn_time(event.name, self.player.zone)
        info.is_notable = notable
        info._notify()
        # TODO(M3): wiki loot + PigParse price enrichment happens here in C#.
