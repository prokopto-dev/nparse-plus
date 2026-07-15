"""Pet tables (PetTests.cs) and PetHandler current-pet tracking."""

from __future__ import annotations

import pytest
from tests.core.handlers.conftest import T0, Harness

from nparseplus.core.enums import PetIncident
from nparseplus.core.events import DamageEvent, PetEvent, SlainEvent, YouZonedEvent
from nparseplus.core.handlers.pet import PetHandler
from nparseplus.core.pets import PlayerPet, load_pets


def test_load_emissary_of_thule() -> None:
    pets = load_pets()
    spell = pets.pet_spells["Emissary of Thule"]
    # 6 pet ranks: 5 from min to max, plus one max+focus.
    assert len(spell.ranks) == 6
    assert spell.pet_class == "Necro"
    assert [r.pet_level for r in spell.ranks] == [43, 44, 45, 46, 47, 48]


def test_find_rank_from_max_melee() -> None:
    pets = load_pets()
    pet = PlayerPet()
    pet.set_spell(pets.pet_spells["Emissary of Thule"])
    pet.set_name("Bakalakadaka")
    # For Emissary, max damage of the 4th rank (index 3) is 59.
    pet.check_max_melee(59)
    assert pet.rank_index == 3


def test_mage_variants_share_the_base_table() -> None:
    pets = load_pets()
    air = pets.spell_for("Elementalkin: Air")
    water = pets.spell_for("Elementalkin: Water")
    assert air is not None and air is water


def test_pet_names_loaded() -> None:
    pets = load_pets()
    assert pets.is_pet_name("Gobaner")
    assert not pets.is_pet_name("Whitewitch")


@pytest.fixture
def h(harness: Harness) -> Harness:
    harness.pet = PlayerPet()
    harness.handler = PetHandler(harness.bus, harness.player, load_pets(), harness.pet)
    return harness


def test_creation_names_pet_only_when_unknown(h: Harness) -> None:
    h.push("Gobaner says 'At your service Master.'")
    assert h.pet.pet_name == "Gobaner"
    # Another creation message (someone else's pet) does not rename ours.
    h.push("Jobober says 'At your service Master.'")
    assert h.pet.pet_name == "Gobaner"


def test_leader_always_names_pet(h: Harness) -> None:
    h.push("Gobaner says 'At your service Master.'")
    h.push("Jobober says 'My leader is Tester.'")
    assert h.pet.pet_name == "Jobober"


def test_death_of_our_pet_resets(h: Harness) -> None:
    h.bus.publish(PetEvent(timestamp=T0, incident=PetIncident.LEADER, pet_name="Gobaner"))
    h.bus.publish(PetEvent(timestamp=T0, incident=PetIncident.DEATH, pet_name="Gobaner"))
    assert not h.pet.is_pet_name_known


def test_death_of_another_pet_is_ignored(h: Harness) -> None:
    h.bus.publish(PetEvent(timestamp=T0, incident=PetIncident.LEADER, pet_name="Gobaner"))
    h.bus.publish(PetEvent(timestamp=T0, incident=PetIncident.DEATH, pet_name="Jobober"))
    assert h.pet.pet_name == "Gobaner"


def test_get_lost_resets_our_pet(h: Harness) -> None:
    h.bus.publish(PetEvent(timestamp=T0, incident=PetIncident.LEADER, pet_name="Gobaner"))
    h.bus.publish(PetEvent(timestamp=T0, incident=PetIncident.GETLOST, pet_name="Gobaner"))
    assert not h.pet.is_pet_name_known


def test_no_pet_message_resets(h: Harness) -> None:
    h.bus.publish(PetEvent(timestamp=T0, incident=PetIncident.LEADER, pet_name="Gobaner"))
    h.push("You don't have a pet to command!")
    assert not h.pet.is_pet_name_known


def test_zoning_resets_pet(h: Harness) -> None:
    h.bus.publish(PetEvent(timestamp=T0, incident=PetIncident.LEADER, pet_name="Gobaner"))
    h.bus.publish(YouZonedEvent(timestamp=T0, long_name="The Overthere", short_name="overthere"))
    assert not h.pet.is_pet_name_known


def test_own_death_resets_pet(h: Harness) -> None:
    h.bus.publish(PetEvent(timestamp=T0, incident=PetIncident.LEADER, pet_name="Gobaner"))
    h.bus.publish(SlainEvent(timestamp=T0, victim="You", killer="a brigand"))
    assert not h.pet.is_pet_name_known


def test_pet_melee_damage_feeds_rank(h: Harness) -> None:
    h.pet.set_spell(load_pets().pet_spells["Emissary of Thule"])
    h.bus.publish(PetEvent(timestamp=T0, incident=PetIncident.LEADER, pet_name="Gobaner"))
    h.bus.publish(
        DamageEvent(
            timestamp=T0,
            target_name="a gnoll",
            attacker_name="Gobaner",
            damage_done=59,
            damage_type="slashes",
        )
    )
    assert h.pet.rank_index == 3
    # Kicks are excluded from the rank guess.
    h.bus.publish(
        DamageEvent(
            timestamp=T0,
            target_name="a gnoll",
            attacker_name="Gobaner",
            damage_done=62,
            damage_type="kicks",
        )
    )
    assert h.pet.rank_index == 3
