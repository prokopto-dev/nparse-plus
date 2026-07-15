"""BoatHandler — boat arrival countdowns from departure announcements.

Ports EQTool's Services/BoatScheduleService.cs math locally: a boat sighting
(the departure shout parsed into a BoatEvent) pins the boat's position in
its loop, from which the time to the sighted dock and to the far dock are
projected. Rows live in the "Boats" group, one per schedule leg, named by
the leg's pretty name.

TODO(M3): EQTool's Services/Handlers/BoatHandler.cs only *sends* the
sighting to PigParse; the countdowns are rebuilt from the server's shared
BoatActivityResponce feed so everyone benefits from any sighting. The local
projection below applies the same UpdateBoatInformation math to our own
sightings until the sharing layer lands.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from nparseplus.core.bus import EventBus
from nparseplus.core.events import BoatEvent
from nparseplus.core.handlers.base import BaseHandler
from nparseplus.core.player import ActivePlayer
from nparseplus.core.timers import TimerRow, TimersService
from nparseplus.core.zones import BoatInfo, ZoneDatabase

BOATS_GROUP = "Boats"

# BoatScheduleService.SupportdBoats — legs outside this set are ignored.
SUPPORTED_BOATS = frozenset({"BarrelBarge", "NroIcecladBoat", "BloatedBelly", "MaidensVoyage"})


def boat_dock_countdowns(
    zones: ZoneDatabase,
    boat: str,
    start_point: str,
    last_seen: datetime,
    now: datetime,
) -> list[tuple[BoatInfo, float]]:
    """Project (leg, seconds-until-dock) pairs from one sighting.

    Port of BoatScheduleService.UpdateBoatInformation: the elapsed time since
    the sighting is folded modulo the trip time, then compared against each
    leg's announcement-to-dock offset; a missed dock rolls to the next trip.
    """
    start_leg = next(
        (b for b in zones.boats if b.boat == boat and b.start_point == start_point), None
    )
    if start_leg is None or boat not in SUPPORTED_BOATS:
        return []
    end_leg = next(
        (b for b in zones.boats if b.boat == boat and b.start_point == start_leg.end_point), None
    )

    elapsed = int(abs((now - last_seen).total_seconds()))
    if elapsed > start_leg.trip_time_in_seconds:
        trips = int(elapsed / start_leg.trip_time_in_seconds)
        elapsed = int(elapsed - trips * start_leg.trip_time_in_seconds)

    result: list[tuple[BoatInfo, float]] = []
    for leg in (start_leg, end_leg):
        if leg is None:
            continue
        to_dock = leg.announcement_to_dock_in_seconds - elapsed
        if to_dock <= 0:
            to_dock = int(leg.trip_time_in_seconds - elapsed + leg.announcement_to_dock_in_seconds)
        result.append((leg, float(to_dock)))
    return result


class BoatHandler(BaseHandler):
    def __init__(
        self,
        bus: EventBus,
        player: ActivePlayer,
        timers: TimersService,
        zones: ZoneDatabase,
    ) -> None:
        super().__init__(bus, player)
        self.timers = timers
        self.zones = zones
        bus.subscribe(BoatEvent, self._on_boat)

    def _on_boat(self, event: BoatEvent) -> None:
        # TODO(M3): pigParseApi.SendBoatData equivalent (share the sighting).
        for leg, seconds in boat_dock_countdowns(
            self.zones, event.boat, event.start_point, event.timestamp, event.timestamp
        ):
            self.timers.add_timer(
                TimerRow(
                    name=leg.pretty_name,
                    group=BOATS_GROUP,
                    updated_at=event.timestamp,
                    ends_at=event.timestamp + timedelta(seconds=seconds),
                    total_duration_s=float(leg.trip_time_in_seconds),
                )
            )
