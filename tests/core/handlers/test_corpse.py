"""CorpseWaypointHandler — mark your death at the last known /loc."""

from __future__ import annotations

import pytest
from tests.core.handlers.conftest import Harness

from nparseplus.core.events import CorpseMarkerEvent
from nparseplus.core.handlers.corpse import CorpseWaypointHandler


@pytest.fixture
def h(harness: Harness) -> Harness:
    harness.player.name = "Tester"
    harness.player.zone = "gfaydark"
    harness.handler = CorpseWaypointHandler(harness.bus, harness.player)
    return harness


def corpses(h: Harness) -> list[CorpseMarkerEvent]:
    return h.collector.of_type(CorpseMarkerEvent)


def test_death_with_known_loc_publishes_marker(h: Harness) -> None:
    h.push("Your Location is 111.00, 222.00, 3.00")
    h.push("You have been slain by a gnoll!")
    (event,) = corpses(h)
    assert event.name == "Tester"
    assert event.zone == "gfaydark"
    assert (event.loc.x, event.loc.y, event.loc.z) == (222.0, 111.0, 3.0)


def test_death_without_loc_is_silent(h: Harness) -> None:
    h.push("You have been slain by a gnoll!")
    assert corpses(h) == []


def test_zone_change_clears_stale_loc(h: Harness) -> None:
    h.push("Your Location is 111.00, 222.00, 3.00")
    h.push("You have entered Butcherblock Mountains.")
    h.push("You have been slain by a gnoll!")
    assert corpses(h) == []


def test_other_deaths_ignored(h: Harness) -> None:
    h.push("Your Location is 111.00, 222.00, 3.00")
    h.push("You have slain a gnoll!")
    h.push("Soandso has been slain by a gnoll!")
    assert corpses(h) == []
