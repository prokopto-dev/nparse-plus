"""SpawnTimerHandler — port of EQtoolsTests/SlainHandlerTests.cs plus the
respawn-timer assertions that the C# spread across ZoneSpawnTimeTests."""

from __future__ import annotations

from datetime import timedelta

import pytest
from tests.core.handlers.conftest import T0, Harness

from nparseplus.core.events import ConfirmedDeathEvent
from nparseplus.core.handlers.spawn_timer import (
    CUSTOM_TIMER_GROUP,
    SIRRAN_TIMER_NAME,
    SpawnTimerHandler,
)
from nparseplus.core.spells.models import Spell
from nparseplus.core.timers import SpellRow, TimerRow


@pytest.fixture
def h(harness: Harness, zones, npcs) -> Harness:
    harness.handler = SpawnTimerHandler(harness.bus, harness.player, harness.timers, zones, npcs)
    return harness


def deaths(h: Harness) -> list[ConfirmedDeathEvent]:
    return h.collector.of_type(ConfirmedDeathEvent)


def timer_rows(h: Harness) -> list[TimerRow]:
    return [r for r in h.timers.snapshot() if isinstance(r, TimerRow)]


FACTION_BURST = (
    "Your faction standing with ClawsofVeeshan got better.",
    "Your faction standing with Coldain got better.",
    "Your faction standing with Kromrif got worse.",
    "Your faction standing with Kromzek got worse.",
)


def test_happy_path_all_three_messages(h: Harness) -> None:
    h.push("You have slain a frost giant scout!")
    for line in FACTION_BURST:
        h.push(line)
    h.push("You gain experience!!")
    events = deaths(h)
    assert len(events) == 1
    assert events[0].victim == "a frost giant scout"
    assert events[0].killer == "You"


def test_slain_timer_number_increment(h: Harness) -> None:
    for _ in range(3):
        h.push("You have slain a frost giant scout!")
    names = [r.name for r in timer_rows(h)]
    assert names == [
        "--Dead-- a frost giant scout",
        "--Dead-- a frost giant scout_2",
        "--Dead-- a frost giant scout_3",
    ]
    assert all(r.group == CUSTOM_TIMER_GROUP for r in timer_rows(h))


def test_you_slain_does_not_count(h: Harness) -> None:
    h.push("You have been slain by Ajorek the Crimson Fang!")
    h.push("Your faction standing with ClawsofVeeshan got better.")
    assert deaths(h) == []


def test_slain_in_middle(h: Harness) -> None:
    for line in FACTION_BURST:
        h.push(line)
    h.push("You have slain a frost giant scout!")
    h.push("You gain experience!!")
    events = deaths(h)
    assert len(events) == 1
    assert events[0].victim == "a frost giant scout"


def test_slain_at_end(h: Harness) -> None:
    for line in FACTION_BURST:
        h.push(line)
    h.push("You gain experience!!")
    h.push("You have slain a frost giant scout!")
    events = deaths(h)
    assert len(events) == 1
    assert events[0].victim == "a frost giant scout"


def test_faction_burst_alone_is_a_guess(h: Harness) -> None:
    for line in FACTION_BURST:
        h.push(line)
    h.push("Lilrez begins to cast a spell.")
    events = deaths(h)
    assert len(events) == 1
    assert events[0].victim == "Faction Slain"
    assert events[0].killer == "You"
    assert timer_rows(h)[0].name == "--Dead-- Faction Slain"


def test_slain_after_your_melee(h: Harness) -> None:
    h.push("You crush a skeleton for 46 points of damage.")
    h.push("You have slain a skeleton!")
    h.push("Your Location is -0.26, 1844.07, -14.98")
    events = deaths(h)
    assert len(events) == 1
    assert (events[0].victim, events[0].killer) == ("a skeleton", "You")


def test_other_player_slain_by_npc(h: Harness) -> None:
    h.push("Sontalak claws Robobard for 425 points of damage.")
    h.push("Robobard has been slain by Sontalak!")
    h.push(
        "Sontalak says 'Ack!  I must be careful not to step on that body, it "
        "tastes much better when it is still crunchy, not pulped!'"
    )
    events = deaths(h)
    assert len(events) == 1
    assert (events[0].victim, events[0].killer) == ("Robobard", "Sontalak")
    # Robobard is a player, not in the NPC list: no respawn timer.
    assert timer_rows(h) == []


def test_two_players_slain(h: Harness) -> None:
    h.push("An ancient wyvern hits Marantula for 196 points of damage.")
    h.push("Marantula has been slain by an ancient wyvern!")
    h.push("Gluwen has been slain by an ancient wyvern!")
    assert [e.victim for e in deaths(h)] == ["Marantula", "Gluwen"]


def test_faction_guess_alongside_real_kill(h: Harness) -> None:
    h.push("Jaerlin pierces a Drakkel Dire Wolf for 42 points of damage.")
    for line in FACTION_BURST:
        h.push(line)
    h.push("Lilrez begins to cast a spell.")
    h.push("An ancient wyvern hits Marantula for 196 points of damage.")
    h.push("Marantula has been slain by an ancient wyvern!")
    assert [e.victim for e in deaths(h)] == ["Faction Slain", "Marantula"]


def test_repeated_faction_bursts(h: Harness) -> None:
    h.push("Jaerlin pierces a Drakkel Dire Wolf for 42 points of damage.")
    for _ in range(3):
        for line in FACTION_BURST:
            h.push(line)
    h.push("Jaerlin pierces a Drakkel Dire Wolf for 42 points of damage.")
    assert [e.victim for e in deaths(h)] == ["Faction Slain"] * 3


def test_faction_bursts_split_by_other_slain(h: Harness) -> None:
    h.push("Jaerlin pierces a Drakkel Dire Wolf for 42 points of damage.")
    for _ in range(2):
        for line in FACTION_BURST:
            h.push(line)
    h.push("Marantula has been slain by an ancient wyvern!")
    h.push("Gluwen has been slain by an ancient wyvern!")
    for line in FACTION_BURST:
        h.push(line)
    h.push("Jaerlin pierces a Drakkel Dire Wolf for 42 points of damage.")
    assert len(deaths(h)) == 5


def test_multiple_exp_messages(h: Harness) -> None:
    h.push("You have slain a frost giant scout!")
    h.push("You gain experience!!")
    h.push("You gain experience!!")
    h.push("You gain experience!!")
    h.push("Jaerlin pierces a Drakkel Dire Wolf for 42 points of damage.")
    victims = [e.victim for e in deaths(h)]
    assert victims.count("a frost giant scout") == 1
    assert victims.count("Exp Slain") == 2


def test_multiple_slain_messages(h: Harness) -> None:
    for _ in range(3):
        h.push("You have slain a frost giant scout!")
    assert len(deaths(h)) == 3


def test_seventh_faction_message_splits_burst(h: Harness) -> None:
    h.push("Jaerlin pierces a Drakkel Dire Wolf for 42 points of damage.")
    for _ in range(2):
        for line in FACTION_BURST:
            h.push(line)
    h.push("Your faction standing with Knight got better.")
    h.push("Your faction standing with Doesntmatter got better.")
    h.push("Your faction standing with Kromrif got worse.")
    h.push("Your faction standing with Kromzek got worse.")
    h.push("Jaerlin pierces a Drakkel Dire Wolf for 42 points of damage.")
    assert len(deaths(h)) == 3


def test_slain_suppresses_following_faction_guess(h: Harness) -> None:
    h.push("You have slain a zol ghoul knight!")
    h.push("Your faction standing with FrogloksofGuk got better.")
    h.push("Your faction standing with UndeadFrogloksofGuk could not possibly get any worse.")
    h.push(
        "Varer judges you amiably -- You would probably win this fight..it's not certain though."
    )
    h.push("Varer slices a wan ghoul knight for 24 points of damage.")
    h.push("You have slain a wan ghoul knight!")
    h.push("Varer slices a wan ghoul knight for 24 points of damage.")
    h.push("You have slain a zol ghoul knight!")
    h.push("Your faction standing with FrogloksofGuk got better.")
    h.push("Your faction standing with UndeadFrogloksofGuk could not possibly get any worse.")
    h.push(
        "Varer judges you amiably -- You would probably win this fight..it's not certain though."
    )
    assert len(deaths(h)) == 3


def test_slain_neriak(h: Harness) -> None:
    h.push("You crush Uglan for 19 points of damage.")
    h.push("You have slain Uglan!")
    h.push("Your faction standing with EldritchCollective got better.")
    h.push("Your faction standing with KeepersoftheArt got better.")
    h.push("Your faction standing with KingAythoxThex got better.")
    h.push("Your faction standing with PrimordialMalice got better.")
    h.push("Your faction standing with QueenCristanosThex got worse.")
    h.push("Your faction standing with TheDead could not possibly get any worse.")
    h.push("You gain party experience!!")
    h.push("You fail to locate any food nearby.")
    assert len(deaths(h)) == 1


def test_respawn_timer_uses_zone_spawn_time(h: Harness, zones) -> None:
    h.player.zone = "kael"
    h.push("You have slain Derakor the Vindicator!")
    row = h.timers.find("--Dead-- Derakor the Vindicator", CUSTOM_TIMER_GROUP)
    assert row is not None
    assert row.total_duration_s == float(zones.spawn_time("Derakor the Vindicator", "kael"))
    assert row.total_duration_s == 25200.0


def test_respawn_timer_zone_default(h: Harness, zones) -> None:
    h.player.zone = "unrest"
    h.push("You have slain a skeleton!")
    row = h.timers.find("--Dead-- a skeleton", CUSTOM_TIMER_GROUP)
    assert row is not None
    assert row.total_duration_s == float(zones.get("unrest").respawn_seconds)


def test_pos_boss_starts_sirran_timer(h: Harness) -> None:
    h.push("You have slain Keeper of Souls!")
    row = h.timers.find(SIRRAN_TIMER_NAME, CUSTOM_TIMER_GROUP)
    assert row is not None
    assert row.total_duration_s == 15 * 60.0


def test_known_player_victim_gets_no_timer(h: Harness) -> None:
    h.player.known_players.add("Uglan")
    h.push("You have slain Uglan!")
    assert len(deaths(h)) == 1
    assert timer_rows(h) == []


def _victim_spell_row(h: Harness, victim: str) -> SpellRow:
    spell = Spell(id=1, name="Tainted Breath", benefit_detriment="detrimental")
    return SpellRow(
        name=spell.name,
        group=f" {victim}",
        updated_at=T0,
        is_target_player=False,
        spell=spell,
        ends_at=T0 + timedelta(seconds=60),
        total_duration_s=60.0,
        detrimental=True,
    )


def test_slain_clears_victim_rows_under_restart_current_timer(h: Harness) -> None:
    h.timers.add_spell(_victim_spell_row(h, "a frost giant scout"))
    h.push("You have slain a frost giant scout!")
    assert h.timers.find("Tainted Breath", " a frost giant scout") is None


def test_slain_keeps_victim_rows_under_start_new_timer(h: Harness) -> None:
    # SlainHandler.cs: stacked rows may belong to another same-named mob, so
    # StartNewTimer leaves them to expire naturally.
    h.handler.timer_recast = lambda: "StartNewTimer"
    h.timers.add_spell(_victim_spell_row(h, "a frost giant scout"))
    h.push("You have slain a frost giant scout!")
    assert h.timers.find("Tainted Breath", " a frost giant scout") is not None
    assert h.timers.find("--Dead-- a frost giant scout", CUSTOM_TIMER_GROUP) is not None
