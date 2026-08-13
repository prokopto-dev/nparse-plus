"""Pet attribution — issue #81.

A pet's damage was counted but never connected to its owner: the row was
styled as a stranger's and ``_update_session_stats`` merged only the entity
named You, so pet damage reached the session Best/Now/Last footer nowhere at
all. For a magician or a necromancer that is most of their output.
"""

from datetime import datetime, timedelta

from nparseplus.core.bus import EventBus
from nparseplus.core.dps import YOU, FightTracker
from nparseplus.core.enums import PetIncident
from nparseplus.core.events import DamageEvent, PetEvent
from nparseplus.core.handlers.dps import DpsHandler
from nparseplus.core.pets import PlayerPet
from nparseplus.core.player import ActivePlayer


def _swing(attacker: str, target: str, amount: int, when: datetime) -> DamageEvent:
    return DamageEvent(
        timestamp=when,
        target_name=target,
        attacker_name=attacker,
        damage_done=amount,
        damage_type="hits",
        level_guess=None,
    )


def _rows(tracker: FightTracker, now: datetime) -> dict[str, object]:
    return {row.attacker_name: row for row in tracker.snapshot(now)}


# -- the flag -----------------------------------------------------------------


def test_no_pet_flags_nothing(tracker: FightTracker, t0: datetime) -> None:
    tracker.add_damage(_swing("Vexer", "a gnoll", 26, t0))
    assert tracker.snapshot(t0)[0].is_your_pet is False


def test_the_current_pet_is_flagged(tracker: FightTracker, t0: datetime) -> None:
    tracker.set_pet_name("Vexer")
    tracker.add_damage(_swing("Vexer", "a gnoll", 26, t0))
    tracker.add_damage(_swing("Soandso", "a gnoll", 40, t0))
    rows = _rows(tracker, t0)
    assert rows["Vexer"].is_your_pet is True
    assert rows["Vexer"].is_your_damage is False  # it is the pet's row, not yours
    assert rows["Soandso"].is_your_pet is False


def test_the_flag_is_casefolded(tracker: FightTracker, t0: datetime) -> None:
    tracker.set_pet_name("Vexer")
    tracker.add_damage(_swing("vexer", "a gnoll", 26, t0))
    assert tracker.snapshot(t0)[0].is_your_pet is True


def test_a_charm_named_like_its_target_is_not_flagged(tracker: FightTracker, t0: datetime) -> None:
    """The attacker == target guard must not come back through the flag.

    A charmed pet can carry an NPC's name, which is why ``add_damage``
    refuses an event whose attacker equals its target. That guard compares
    the raw names, so a case difference slips a row through; the flag
    refuses it on its own terms rather than painting a mob as yours.
    """
    tracker.set_pet_name("a large rat")
    tracker.add_damage(_swing("a large rat", "a large rat", 26, t0))
    assert tracker.fights == []  # dropped at intake

    tracker.add_damage(_swing("A LARGE RAT", "a large rat", 26, t0))
    assert tracker.snapshot(t0)[0].is_your_pet is False


def test_losing_the_pet_clears_the_flag(tracker: FightTracker, t0: datetime) -> None:
    tracker.set_pet_name("Vexer")
    tracker.add_damage(_swing("Vexer", "a gnoll", 26, t0))
    assert tracker.snapshot(t0)[0].is_your_pet is True
    tracker.set_pet_name("")  # died / reclaimed / got lost
    assert tracker.snapshot(t0)[0].is_your_pet is False


def test_a_resummoned_pet_tracks_the_new_name(tracker: FightTracker, t0: datetime) -> None:
    tracker.set_pet_name("Vexer")
    tracker.add_damage(_swing("Vexer", "a gnoll", 26, t0))
    tracker.set_pet_name("Gorkus")
    tracker.add_damage(_swing("Gorkus", "a gnoll", 30, t0 + timedelta(seconds=1)))
    rows = _rows(tracker, t0)
    assert rows["Gorkus"].is_your_pet is True
    assert rows["Vexer"].is_your_pet is False  # the corpse is somebody else now


def test_set_pet_name_notifies_only_on_a_change(tracker: FightTracker) -> None:
    # PlayerPet also fires on every rank guess, which is every pet hit.
    calls: list[int] = []
    tracker.on_change.append(lambda: calls.append(1))
    tracker.set_pet_name("Vexer")
    tracker.set_pet_name("Vexer")
    assert calls == [1]


# -- the session footer -------------------------------------------------------


def _long_fight(tracker: FightTracker, t0: datetime, *, you: int, pet: int) -> datetime:
    """A 30 s fight past the session minimum, both of you swinging."""
    for offset in range(0, 31):
        when = t0 + timedelta(seconds=offset)
        if you:
            tracker.add_damage(_swing(YOU, "Gorenaire", you, when))
        if pet:
            tracker.add_damage(_swing("Vexer", "Gorenaire", pet, when))
    now = t0 + timedelta(seconds=30)
    tracker.tick(now)
    return now


def test_pet_damage_reaches_the_session_footer(t0: datetime) -> None:
    tracker = FightTracker()
    tracker.set_pet_name("Vexer")
    _long_fight(tracker, t0, you=100, pet=60)
    combined = tracker.session_summary().best

    solo = FightTracker()
    _long_fight(solo, t0, you=100, pet=0)
    yours_alone = solo.session_summary().best

    assert combined.highest_dps > yours_alone.highest_dps
    assert combined.total_damage == yours_alone.total_damage + (60 * 31)


def test_the_footer_is_the_sum_not_the_larger_of_the_two(t0: datetime) -> None:
    tracker = FightTracker()
    tracker.set_pet_name("Vexer")
    _long_fight(tracker, t0, you=100, pet=100)
    pet_only = FightTracker()
    pet_only.set_pet_name("Vexer")
    _long_fight(pet_only, t0, you=0, pet=100)
    assert (
        tracker.session_summary().best.highest_dps
        == pet_only.session_summary().best.highest_dps * 2
    )


def test_the_toggle_off_leaves_the_footer_as_it_was(t0: datetime) -> None:
    tracker = FightTracker(count_pet_damage=False)
    tracker.set_pet_name("Vexer")
    _long_fight(tracker, t0, you=100, pet=60)

    unchanged = FightTracker()
    _long_fight(unchanged, t0, you=100, pet=0)
    assert (
        tracker.session_summary().best.highest_dps == unchanged.session_summary().best.highest_dps
    )


def test_a_pet_only_fight_still_feeds_the_footer(t0: datetime) -> None:
    # A necromancer who let the pet tank still has a number.
    tracker = FightTracker()
    tracker.set_pet_name("Vexer")
    _long_fight(tracker, t0, you=0, pet=60)
    assert tracker.session_summary().best.highest_dps > 0


def test_the_highest_hit_stays_yours(t0: datetime) -> None:
    # It reads as your own crit; the pet's biggest swing is not that.
    tracker = FightTracker()
    tracker.set_pet_name("Vexer")
    _long_fight(tracker, t0, you=100, pet=900)
    assert tracker.session_summary().best.highest_hit == 100


def test_another_players_pet_never_reaches_your_footer(t0: datetime) -> None:
    tracker = FightTracker()
    tracker.set_pet_name("Vexer")
    for offset in range(0, 31):
        tracker.add_damage(_swing("Gorkus", "Gorenaire", 900, t0 + timedelta(seconds=offset)))
    tracker.tick(t0 + timedelta(seconds=30))
    assert tracker.session_summary().best.highest_dps == 0


def test_the_pet_carries_the_pair_past_the_fight_minimum(t0: datetime) -> None:
    """The gate takes the longer of the two, not yours.

    The pet opened 25 s before you landed a swing; the pair has been on the
    target long enough even though your own entity has not.
    """
    tracker = FightTracker()
    tracker.set_pet_name("Vexer")
    for offset in range(0, 30):
        tracker.add_damage(_swing("Vexer", "Gorenaire", 60, t0 + timedelta(seconds=offset)))
    tracker.add_damage(_swing(YOU, "Gorenaire", 100, t0 + timedelta(seconds=29)))
    tracker.tick(t0 + timedelta(seconds=30))
    assert tracker.session_summary().best.highest_dps > 0


def test_the_toggle_applies_live(t0: datetime) -> None:
    tracker = FightTracker()
    tracker.set_pet_name("Vexer")
    _long_fight(tracker, t0, you=100, pet=60)
    # It is a measurement rule, so it resets the aggregates...
    tracker.configure(count_pet_damage=False)
    assert tracker.session_summary().best.highest_dps == 0
    # ...and the next reading is yours alone.
    tracker.tick(t0 + timedelta(seconds=31))
    with_pet = FightTracker()
    with_pet.set_pet_name("Vexer")
    _long_fight(with_pet, t0, you=100, pet=60)
    assert tracker.session_summary().best.highest_dps < with_pet.session_summary().best.highest_dps


# -- the handler follows PlayerPet --------------------------------------------


def test_the_handler_follows_the_pet_through_summon_and_death(
    bus: EventBus, tracker: FightTracker, t0: datetime
) -> None:
    """Attribution rides on the state PetHandler already maintains.

    DpsHandler must not re-implement the CREATION/LEADER/DEATH rules — this
    walks PlayerPet directly, which is what the pet handler writes.
    """
    pet = PlayerPet()
    DpsHandler(bus, ActivePlayer(name="Genartik"), tracker, player_pet=pet)
    assert tracker.pet_name == ""

    pet.set_name("Vexer")  # PetHandler: CREATION / LEADER / PETATTACK
    bus.publish(_swing("Vexer", "a gnoll", 26, t0))
    assert tracker.snapshot(t0)[0].is_your_pet is True

    pet.reset()  # PetHandler: DEATH / RECLAIMED / GETLOST / zone / charm break
    assert tracker.pet_name == ""
    assert tracker.snapshot(t0)[0].is_your_pet is False


def test_the_pet_handler_and_the_dps_handler_agree(
    bus: EventBus, tracker: FightTracker, t0: datetime
) -> None:
    """End to end over the bus: a PetEvent names the pet, the row is flagged."""
    from nparseplus.core.handlers.pet import PetHandler
    from nparseplus.core.pets import load_pets

    pet = PlayerPet()
    player = ActivePlayer(name="Genartik")
    PetHandler(bus, player, load_pets(), player_pet=pet)
    DpsHandler(bus, player, tracker, player_pet=pet)

    bus.publish(
        PetEvent(timestamp=t0, pet_name="Vexer", incident=PetIncident.LEADER, line="Vexer says")
    )
    bus.publish(_swing("Vexer", "a gnoll", 26, t0))
    assert tracker.snapshot(t0)[0].is_your_pet is True


# -- ownership outlives the pet -----------------------------------------------


def test_a_pet_that_dies_mid_fight_keeps_its_damage_in_the_footer(t0: datetime) -> None:
    """The footer asks the past tense, not the present.

    PetHandler clears the pet name on death, reclaim, charm break and zone.
    Selecting the pet entity by the CURRENT name meant a pet that fought for
    25 s and then died was dropped from the combined reading for the rest of
    a fight that was still running — the damage was recorded and then
    silently uncounted.
    """
    tracker = FightTracker()
    tracker.set_pet_name("Vexer")
    for offset in range(0, 15):
        tracker.add_damage(_swing("Vexer", "Gorenaire", 60, t0 + timedelta(seconds=offset)))
    tracker.set_pet_name("")  # the pet dies, well before the 20 s gate
    for offset in range(0, 31):
        tracker.add_damage(_swing(YOU, "Gorenaire", 100, t0 + timedelta(seconds=offset)))
    tracker.tick(t0 + timedelta(seconds=30))

    solo = FightTracker()
    _long_fight(solo, t0, you=100, pet=0)
    assert (
        tracker.session_summary().best.total_damage
        == solo.session_summary().best.total_damage + (60 * 15)
    )


def test_the_row_flag_still_drops_while_the_footer_keeps_the_damage(t0: datetime) -> None:
    """The two are deliberately different claims.

    "That row is my pet" is present tense and goes false when the pet dies
    (issue #81's first acceptance criterion); "that damage was mine" is not.
    """
    tracker = FightTracker()
    tracker.set_pet_name("Vexer")
    for offset in range(0, 31):
        tracker.add_damage(_swing("Vexer", "Gorenaire", 60, t0 + timedelta(seconds=offset)))
    tracker.set_pet_name("")
    now = t0 + timedelta(seconds=30)
    tracker.tick(now)

    assert _rows(tracker, now)["Vexer"].is_your_pet is False
    assert tracker.session_summary().best.highest_dps > 0


def test_a_pet_replaced_mid_fight_counts_both(t0: datetime) -> None:
    tracker = FightTracker()
    tracker.set_pet_name("Vexer")
    for offset in range(0, 15):
        tracker.add_damage(_swing("Vexer", "Gorenaire", 60, t0 + timedelta(seconds=offset)))
    tracker.set_pet_name("Gorkus")  # died and resummoned
    for offset in range(15, 31):
        tracker.add_damage(_swing("Gorkus", "Gorenaire", 60, t0 + timedelta(seconds=offset)))
    tracker.tick(t0 + timedelta(seconds=30))
    # Both pets were yours; the whole 31 hits' worth is in the reading.
    assert tracker.session_summary().best.total_damage == 60 * 31


def test_ownership_is_never_stamped_on_another_players_pet(t0: datetime) -> None:
    tracker = FightTracker()
    tracker.set_pet_name("Vexer")
    for offset in range(0, 31):
        tracker.add_damage(_swing("Gorkus", "Gorenaire", 900, t0 + timedelta(seconds=offset)))
    # Naming it later must not retroactively claim what it already did.
    tracker.set_pet_name("Gorkus")
    tracker.tick(t0 + timedelta(seconds=30))
    assert tracker.session_summary().best.highest_dps == 0


def test_the_stamp_is_scoped_to_the_fight_it_was_made_in(t0: datetime) -> None:
    # A charm that breaks and is fought as a mob later starts a fresh entity.
    tracker = FightTracker()
    tracker.set_pet_name("Vexer")
    tracker.add_damage(_swing("Vexer", "Gorenaire", 60, t0))
    assert tracker.fights[0].entities["vexer"].was_your_pet is True
    tracker.set_pet_name("")
    tracker.clear()
    for offset in range(0, 31):
        tracker.add_damage(_swing("Vexer", "a gnoll", 900, t0 + timedelta(seconds=offset)))
    tracker.tick(t0 + timedelta(seconds=30))
    assert tracker.fights[0].entities["vexer"].was_your_pet is False
    assert tracker.session_summary().best.highest_dps == 0
