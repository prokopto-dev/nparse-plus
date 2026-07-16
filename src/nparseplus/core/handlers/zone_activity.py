"""ZoneActivityHandler — shares notable NPC sightings with PigParse.

Port of EQTool's Services/ZoneActivityTrackingService.cs: every con and
slain event posts ``api/zone/npcactivity`` (IsDeath accordingly); damage or
spells landing on Kael faction mobs post IsEngaged=true, throttled to one
send per 15 s. The PigParse server turns Kael activity into the shared
"Next Kael Faction Pull" / "Kael Faction Pull In Progress" custom timers
every client receives over the hub.

Deliberate divergence: the C# sends every name and lets
PigParseApi.SendNPCActivity drop everything not in its allow-list
("Scout Charisa", "a Kromzek Captain", Kael faction mobs); we filter here,
where the ZoneDatabase lives, and never put the discarded traffic on the
wire.
"""

from __future__ import annotations

from datetime import datetime

from nparseplus.core.bus import EventBus
from nparseplus.core.events import (
    ConEvent,
    DamageEvent,
    PlayerLocationEvent,
    SlainEvent,
    SpellCastOnOtherEvent,
)
from nparseplus.core.geometry import Loc
from nparseplus.core.handlers.base import BaseHandler
from nparseplus.core.pigparse import PigParseApi, SubmitFn
from nparseplus.core.player import ActivePlayer
from nparseplus.core.zones import ZoneDatabase

KAEL_ENGAGE_THROTTLE_SECONDS = 15.0
EXTRA_TRACKED_NPCS = frozenset({"Scout Charisa", "a Kromzek Captain"})


class ZoneActivityHandler(BaseHandler):
    def __init__(
        self,
        bus: EventBus,
        player: ActivePlayer,
        zones: ZoneDatabase,
        api: PigParseApi | None = None,
        submit: SubmitFn | None = None,
    ) -> None:
        super().__init__(bus, player)
        self.zones = zones
        self.api = api
        self.submit = submit
        self._kael_mobs = frozenset(zones.kael_faction_mobs)
        self._last_loc: Loc | None = None
        self._last_kael_send: datetime | None = None
        bus.subscribe(PlayerLocationEvent, self._on_location)
        bus.subscribe(ConEvent, self._on_con)
        bus.subscribe(SlainEvent, self._on_slain)
        bus.subscribe(DamageEvent, self._on_damage)
        bus.subscribe(SpellCastOnOtherEvent, self._on_spell_cast)

    def _on_location(self, event: PlayerLocationEvent) -> None:
        self._last_loc = event.location

    def _on_con(self, event: ConEvent) -> None:
        if self._tracked(event.name):
            self._send(event.name, is_death=False)

    def _on_slain(self, event: SlainEvent) -> None:
        if self._tracked(event.victim):
            self._send(event.victim, is_death=True)

    def _on_damage(self, event: DamageEvent) -> None:
        self._kael_engage(event.target_name, event.timestamp)

    def _on_spell_cast(self, event: SpellCastOnOtherEvent) -> None:
        self._kael_engage(event.target_name, event.timestamp)

    def _kael_engage(self, target_name: str, when: datetime) -> None:
        if target_name not in self._kael_mobs:
            return
        if (
            self._last_kael_send is not None
            and (when - self._last_kael_send).total_seconds() < KAEL_ENGAGE_THROTTLE_SECONDS
        ):
            return
        if self._send(target_name, is_death=False, is_engaged=True):
            self._last_kael_send = when

    def _tracked(self, name: str) -> bool:
        return name in EXTRA_TRACKED_NPCS or name in self._kael_mobs

    def _send(self, name: str, *, is_death: bool, is_engaged: bool = False) -> bool:
        api, submit, server = self.api, self.submit, self.player.server
        if api is None or submit is None or server is None:
            return False
        zone = self.player.zone
        # NPCData.LocX/LocY are the raw /loc print order (Point3D.X = first
        # number); our Loc normalizes, so X<-loc.y / Y<-loc.x.
        loc_x = self._last_loc.y if self._last_loc is not None else None
        loc_y = self._last_loc.x if self._last_loc is not None else None
        submit(
            lambda: api.send_npc_activity(
                name=name,
                zone=zone,
                server=int(server),
                is_death=is_death,
                is_engaged=is_engaged,
                loc_x=loc_x,
                loc_y=loc_y,
            )
        )
        return True
