"""ApiTimersService — 5-minute shared boat/roll-timer refresh."""

from datetime import datetime, timedelta

from nparseplus.core.enums import Server
from nparseplus.core.handlers.api_timers import (
    RING_ROLL_NAME,
    SCOUT_TIMER_NAME,
    ApiTimersService,
    quake_row,
    scout_row,
)
from nparseplus.core.handlers.boat import BOATS_GROUP
from nparseplus.core.handlers.spawn_timer import CUSTOM_TIMER_GROUP
from nparseplus.core.player import ActivePlayer
from nparseplus.core.timers import TimerRow, TimersService
from nparseplus.core.zones import load_zone_database
from nparseplus.net.pigparse_models import BoatActivity, RollTimer
from nparseplus.net.worker import ImmediateWorker

T0 = datetime(2026, 7, 8, 12, 0, 0)
ZONES = load_zone_database()


def _roll(kind: int, when: datetime, guess: bool = False) -> RollTimer:
    return RollTimer(roll_timer_type=kind, guess=guess, name="x", date_time=when)


def test_scout_row_prefers_non_guess_and_counts_down() -> None:
    rolls = [
        _roll(1, T0 + timedelta(hours=2), guess=True),
        _roll(1, T0 + timedelta(hours=1), guess=False),
    ]
    row = scout_row(rolls, T0)
    assert row is not None
    assert row.name == SCOUT_TIMER_NAME
    assert row.ends_at == T0 + timedelta(hours=1)


def test_scout_row_past_time_is_unknown() -> None:
    row = scout_row([_roll(1, T0 - timedelta(hours=1))], T0)
    assert row is not None
    assert "(UNKNOWN)" in row.name
    assert row.ends_at == T0 + timedelta(hours=10)


def test_quake_row_rolls_forward_and_subtracts_ring_lead() -> None:
    # Quake recorded 30h ago -> next quake at stored+48h = T0+18h; ring roll
    # 30 minutes before that.
    row = quake_row([_roll(2, T0 - timedelta(hours=30))], T0)
    assert row is not None
    assert row.name == RING_ROLL_NAME
    assert row.ends_at == T0 + timedelta(hours=18) - timedelta(minutes=30)


def test_quake_row_none_without_non_guess_entries() -> None:
    assert quake_row([_roll(2, T0, guess=True)], T0) is None
    assert quake_row([], T0) is None


def test_service_refreshes_every_5_minutes_and_rebuilds_rows() -> None:
    timers = TimersService()
    player = ActivePlayer()
    player.reset_for("Xantik", Server.GREEN)

    barrel = next(b for b in ZONES.boats if b.boat == "BarrelBarge")
    fetches = {"n": 0}

    class Api:
        def boat_activity(self, server: int) -> list[BoatActivity]:
            fetches["n"] += 1
            return [
                BoatActivity(
                    start_point=barrel.start_point,
                    boat=0,
                    last_seen=datetime.now() - timedelta(seconds=30),
                )
            ]

        def roll_timers(self, server: int) -> list[RollTimer]:
            return [_roll(2, datetime.now() + timedelta(hours=3))]

    service = ApiTimersService(timers, ZONES, player, api=Api(), submit=ImmediateWorker().submit)
    service.tick(T0)
    assert fetches["n"] == 1
    boat_rows = [r for r in timers.rows_of(TimerRow) if r.group == BOATS_GROUP]
    assert boat_rows, "boat rows rebuilt from the shared feed"
    ring_rows = [
        r
        for r in timers.rows_of(TimerRow)
        if r.group == CUSTOM_TIMER_GROUP and r.name.startswith(RING_ROLL_NAME)
    ]
    assert len(ring_rows) == 1

    service.tick(T0 + timedelta(minutes=2))
    assert fetches["n"] == 1  # inside the 5-minute window
    service.tick(T0 + timedelta(minutes=6))
    assert fetches["n"] == 2
    ring_rows = [
        r
        for r in timers.rows_of(TimerRow)
        if r.group == CUSTOM_TIMER_GROUP and r.name.startswith(RING_ROLL_NAME)
    ]
    assert len(ring_rows) == 1  # replaced, not duplicated


def test_service_idle_without_api_or_server() -> None:
    timers = TimersService()
    player = ActivePlayer()
    ApiTimersService(timers, ZONES, player).tick(T0)  # no api, no server: no-op
