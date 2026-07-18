"""ApiTimersService — the 5-minute shared boat/roll-timer refresh.

Port of EQTool's UIRunner + SpellWindowViewModel.UpdateAPITimers: every
five minutes (and once at startup) fetch the server's shared boat
sightings and roll timers from PigParse and rebuild the local rows, so
everyone benefits from anyone's boat sighting and the post-quake ring-roll
state survives restarts.

Runs on the driver tick; fetches go through the net worker and the results
are applied back on the driver thread (coordinator inbox), like every
other network consumer.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from nparseplus.core.enums import Boat, RollTimerType
from nparseplus.core.handlers.boat import BOATS_GROUP, boat_dock_countdowns
from nparseplus.core.pigparse import PigParseApi, SubmitFn
from nparseplus.core.timers import ROLL_TIMER_GROUP, TimerRow, TimersService

if TYPE_CHECKING:
    from nparseplus.core.player import ActivePlayer
    from nparseplus.core.zones import ZoneDatabase

REFRESH_INTERVAL_SECONDS = 5 * 60.0
RING_ROLL_NAME = "Ring 8 Roll Timer"
SCOUT_TIMER_NAME = "Scout Charisa Timer"
SCOUT_TOTAL = timedelta(hours=10)
QUAKE_TOTAL = timedelta(hours=24)
RING_ROLL_LEAD = timedelta(minutes=30)  # ring roll is 30 minutes before the quake


def _latest(timers: list[Any], *, guess: bool) -> Any | None:
    candidates = [t for t in timers if t.guess is guess]
    return max(candidates, key=lambda t: t.date_time, default=None)


def scout_row(timers: list[Any], now: datetime) -> TimerRow | None:
    """Port of the RollTimerType.Scout branch of UpdateAPITimers."""
    scouts = [t for t in timers if t.roll_timer_type == int(RollTimerType.SCOUT)]
    if not scouts:
        return None
    timer = _latest(scouts, guess=False) or _latest(scouts, guess=True)
    if timer is None:
        return None
    name = SCOUT_TIMER_NAME
    if timer.date_time > now:
        remaining = timer.date_time - now
    else:
        remaining = SCOUT_TOTAL
        name = f"{SCOUT_TIMER_NAME} (UNKNOWN)"
    return TimerRow(
        name=name,
        group=ROLL_TIMER_GROUP,
        updated_at=now,
        ends_at=now + remaining,
        total_duration_s=SCOUT_TOTAL.total_seconds(),
    )


def quake_row(timers: list[Any], now: datetime) -> TimerRow | None:
    """Port of the RollTimerType.Quake branch of UpdateAPITimers.

    The stored quake time is rolled forward in 24h steps until it is in the
    future, then the ring roll lands 30 minutes before it (with the C#'s
    odd wrap when less than 30 minutes remain — kept as-is).
    """
    quakes = [t for t in timers if t.roll_timer_type == int(RollTimerType.QUAKE)]
    timer = _latest(quakes, guess=False)
    if timer is None:
        return None
    when = timer.date_time
    remaining: timedelta | None = None
    while when < now:
        when = when + QUAKE_TOTAL
        if when > now:
            remaining = when - now
            if remaining >= RING_ROLL_LEAD:
                remaining -= RING_ROLL_LEAD
            else:
                # C#: 24h - (30 - remaining.Minutes) minutes, using the
                # minutes *component* of the remaining timespan.
                minutes_component = int(remaining.total_seconds() // 60) % 60
                remaining = QUAKE_TOTAL - timedelta(minutes=30 - minutes_component)
    if remaining is None:
        # Stored time was already in the future (server clock skew): treat
        # it like the in-future case directly.
        remaining = when - now
        if remaining >= RING_ROLL_LEAD:
            remaining -= RING_ROLL_LEAD
    return TimerRow(
        name=RING_ROLL_NAME,
        group=ROLL_TIMER_GROUP,
        updated_at=now,
        ends_at=now + remaining,
        total_duration_s=QUAKE_TOTAL.total_seconds(),
    )


class ApiTimersService:
    def __init__(
        self,
        timers: TimersService,
        zones: ZoneDatabase,
        player: ActivePlayer,
        api: PigParseApi | None = None,
        submit: SubmitFn | None = None,
    ) -> None:
        self.timers = timers
        self.zones = zones
        self.player = player
        self.api = api
        self.submit = submit
        self._last_refresh: datetime | None = None

    def tick(self, now: datetime) -> None:
        api, submit, server = self.api, self.submit, self.player.server
        if api is None or submit is None or server is None:
            return
        if (
            self._last_refresh is not None
            and (now - self._last_refresh).total_seconds() < REFRESH_INTERVAL_SECONDS
        ):
            return
        self._last_refresh = now
        server_int = int(server)

        def fetch() -> tuple[list[Any], list[Any]]:
            return list(api.boat_activity(server_int)), list(api.roll_timers(server_int))

        def apply(result: tuple[list[Any], list[Any]]) -> None:
            boats, rolls = result
            self._apply_boats(boats, datetime.now())
            self._apply_rolls(rolls, datetime.now())

        submit(fetch, apply)

    def _apply_boats(self, activities: list[Any], now: datetime) -> None:
        """Replay everyone's sightings through the local projection math."""
        for activity in activities:
            boat = Boat(activity.boat).name if activity.boat in set(Boat) else None
            if boat is None:
                continue
            for leg, seconds in boat_dock_countdowns(
                self.zones, boat, activity.start_point, activity.last_seen, now
            ):
                ends_at = now + timedelta(seconds=seconds)
                existing = self.timers.find(leg.pretty_name, BOATS_GROUP)
                if isinstance(existing, TimerRow):
                    existing.ends_at = ends_at
                    existing.updated_at = now
                else:
                    self.timers.add_timer(
                        TimerRow(
                            name=leg.pretty_name,
                            group=BOATS_GROUP,
                            updated_at=now,
                            ends_at=ends_at,
                            total_duration_s=float(leg.trip_time_in_seconds),
                        )
                    )

    def _apply_rolls(self, rolls: list[Any], now: datetime) -> None:
        for row in self.timers.rows_of(TimerRow):
            if row.group == ROLL_TIMER_GROUP and (
                row.name.startswith(RING_ROLL_NAME) or row.name.startswith(SCOUT_TIMER_NAME)
            ):
                self.timers.remove_row(row)
        for new_row in (scout_row(rolls, now), quake_row(rolls, now)):
            if new_row is not None:
                self.timers.add_timer(new_row)
