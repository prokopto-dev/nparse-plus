"""Boat schedule math and BoatHandler — port of BoatScheduleTests.cs with
fixed clocks (the C# used DateTimeOffset.Now with a +/-1s tolerance; exact
timestamps make the expectations deterministic here)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from tests.core.handlers.conftest import T0, Harness

from nparseplus.core.events import BoatEvent
from nparseplus.core.handlers.boat import BOATS_GROUP, BoatHandler, boat_dock_countdowns
from nparseplus.core.zones import ZoneDatabase


def seconds_by_leg(zones: ZoneDatabase, boat: str, start: str, ago: float) -> dict[str, float]:
    result = boat_dock_countdowns(zones, boat, start, T0 - timedelta(seconds=ago), T0)
    return {leg.pretty_name: seconds for leg, seconds in result}


def test_oasis_barge_time_not_passed(zones: ZoneDatabase) -> None:
    legs = seconds_by_leg(zones, "BarrelBarge", "oasis", ago=10)
    assert legs["Oasis arrival"] == 109.0
    assert legs["TD from Oasis arrival"] == 500.0


def test_oasis_barge_time_passed(zones: ZoneDatabase) -> None:
    legs = seconds_by_leg(zones, "BarrelBarge", "oasis", ago=200)
    assert legs["Oasis arrival"] == 698.0  # 779.75 - 200 + 119, truncated
    assert legs["TD from Oasis arrival"] == 310.0


def test_oasis_barge_many_trips_ago(zones: ZoneDatabase) -> None:
    trip = next(b for b in zones.boats if b.boat == "BarrelBarge").trip_time_in_seconds
    legs = seconds_by_leg(zones, "BarrelBarge", "oasis", ago=trip * 5 + 10)
    # elapsed folds to 9s (int truncation of the modulo), C# asserted 109 +/- 1.
    assert legs["Oasis arrival"] == 110.0
    assert legs["TD from Oasis arrival"] == 501.0


def test_nro_boat(zones: ZoneDatabase) -> None:
    legs = seconds_by_leg(zones, "NroIcecladBoat", "nro", ago=10)
    # NRo's dock offset is 0, so a 10s-old sighting rolls to the next trip.
    assert legs["NRo arrival"] == 509.0  # C# asserted 508 +/- 1
    assert legs["Iceclad from NRo arrival"] == 297.0


def test_overthere_boat(zones: ZoneDatabase) -> None:
    legs = seconds_by_leg(zones, "BloatedBelly", "overthere", ago=10)
    assert legs["Overthere arrival"] == 1965.0
    assert len(legs) == 1  # no return leg in the schedule


def test_overthere_boat_over_time(zones: ZoneDatabase) -> None:
    trip = next(b for b in zones.boats if b.boat == "BloatedBelly").trip_time_in_seconds
    legs = seconds_by_leg(zones, "BloatedBelly", "overthere", ago=trip + 10)
    assert legs["Overthere arrival"] == 1965.0


def test_unknown_boat_yields_nothing(zones: ZoneDatabase) -> None:
    assert boat_dock_countdowns(zones, "NotABoat", "oasis", T0, T0) == []


@pytest.fixture
def h(harness: Harness, zones: ZoneDatabase) -> Harness:
    harness.handler = BoatHandler(harness.bus, harness.player, harness.timers, zones)
    return harness


def test_maidens_voyage_announcement_creates_boat_timers(h: Harness) -> None:
    h.push(
        "Glisse Bluesea shouts 'The Maiden's Voyage has departed the outpost at "
        "Firiona Vie. Please be ready to board the shuttles shortly, if you desire "
        "to make the journey to Kunark."
    )
    rows = {r.name: r for r in h.timers.snapshot()}
    assert set(rows) == {"FV Transfer to BB arrival", "BB to FV Transfer arrival"}
    assert all(r.group == BOATS_GROUP for r in rows.values())
    assert rows["FV Transfer to BB arrival"].ends_at == T0 + timedelta(seconds=771)
    # The far dock's offset is 0, so it rolls to the next full trip.
    assert rows["BB to FV Transfer arrival"].ends_at == T0 + timedelta(seconds=1230)


def test_boat_event_creates_barge_timers(h: Harness) -> None:
    h.bus.publish(BoatEvent(timestamp=T0, boat="BarrelBarge", start_point="oasis"))
    rows = {r.name: r for r in h.timers.snapshot()}
    assert rows["Oasis arrival"].ends_at == T0 + timedelta(seconds=119)
    assert rows["TD from Oasis arrival"].ends_at == T0 + timedelta(seconds=510)

    # A fresh sighting replaces the rows instead of duplicating them.
    h.bus.publish(
        BoatEvent(timestamp=T0 + timedelta(seconds=30), boat="BarrelBarge", start_point="oasis")
    )
    assert len(h.timers.snapshot()) == 2
