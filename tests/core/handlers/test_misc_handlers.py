"""ConHandler, GroupLeaderHandler, DisciplineCooldownHandler, and
MendWoundsHandler."""

from __future__ import annotations

import pytest
from tests.core.handlers.conftest import Harness

from nparseplus.core.handlers.con import ConHandler
from nparseplus.core.handlers.discipline_cooldown import DisciplineCooldownHandler
from nparseplus.core.handlers.group_leader import NO_LEADER, GroupLeaderHandler
from nparseplus.core.handlers.mend_wounds import (
    MEND_COOLDOWN_SECONDS,
    MEND_TIMER_NAME,
    MendWoundsHandler,
)
from nparseplus.core.pets import PlayerPet
from nparseplus.core.timers import YOU_GROUP


@pytest.fixture
def h(harness: Harness, zones) -> Harness:
    harness.speaker = None
    harness.pet = PlayerPet()
    harness.con = ConHandler(harness.bus, harness.player, zones, harness.pet)
    harness.leader = GroupLeaderHandler(harness.bus, harness.player)
    harness.disc = DisciplineCooldownHandler(harness.bus, harness.player, harness.timers)
    harness.mend = MendWoundsHandler(harness.bus, harness.player, harness.timers)
    return harness


# -- ConHandler ---------------------------------------------------------------


def test_con_records_mob_with_spawn_time(h: Harness, zones) -> None:
    h.player.zone = "kael"
    h.push(
        "Derakor the Vindicator scowls at you, ready to attack -- what would "
        "you like your tombstone to say?"
    )
    info = h.con.mob_info
    assert info.name == "Derakor the Vindicator"
    assert info.zone == "kael"
    assert info.spawn_seconds == 25200
    assert info.is_notable is True
    assert info.is_pet is False


def test_con_unremarkable_mob(h: Harness) -> None:
    h.player.zone = "unrest"
    h.push("a gnoll scowls at you, ready to attack -- what would you like your tombstone to say?")
    info = h.con.mob_info
    assert info.name == "a gnoll"
    assert info.spawn_seconds == 1320  # unrest zone default
    assert info.is_notable is False


def test_con_own_pet(h: Harness) -> None:
    h.pet.set_name("Gobaner")
    h.push("Gobaner regards you indifferently -- what would you like your tombstone to say?")
    assert h.con.mob_info.is_pet is True
    assert h.con.mob_info.name == "Gobaner"


def test_con_change_hook_fires(h: Harness) -> None:
    seen: list[str] = []
    h.con.mob_info.on_change.append(lambda info: seen.append(info.name))
    h.push("a gnoll scowls at you, ready to attack -- what would you like your tombstone to say?")
    assert seen == ["a gnoll"]


# -- GroupLeaderHandler ---------------------------------------------------------


def test_group_leader_tracked_and_cleared_on_login(h: Harness) -> None:
    assert h.leader.group_leader_name == NO_LEADER
    h.push("Wreckeder is now the leader of your group.")
    assert h.leader.group_leader_name == "Wreckeder"
    h.push("Your group has been disbanded.")
    assert h.leader.group_leader_name == NO_LEADER
    h.push("You invite Wreckeder to join your group.")
    assert h.leader.group_leader_name == "You"
    h.push("Welcome to EverQuest!")
    assert h.leader.group_leader_name == NO_LEADER


# -- DisciplineCooldownHandler ----------------------------------------------------


def test_discipline_cooldown_timer(h: Harness) -> None:
    h.push("You can use the ability Puretone Discipline again in 48 minute(s) 45 seconds.")
    row = h.timers.find("Puretone Discipline", YOU_GROUP)
    assert row is not None
    assert row.total_duration_s == float(48 * 60 + 45)


# -- MendWoundsHandler -------------------------------------------------------------


def test_mend_wounds_cooldown_timer(h: Harness) -> None:
    h.push("You have failed to mend your wounds.")
    row = h.timers.find(MEND_TIMER_NAME, YOU_GROUP)
    assert row is not None
    assert row.total_duration_s == float(MEND_COOLDOWN_SECONDS)
