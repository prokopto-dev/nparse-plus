"""Built-in library loading and SyncBuiltInTriggers semantics."""

from nparseplus.core.triggers.builtin import (
    EXPECTED_BUILTIN_COUNT,
    effective_triggers,
    load_builtin_triggers,
    sync_builtin_triggers,
)
from nparseplus.core.triggers.model import Trigger, TriggerAudioType, TriggerOutput


def test_load_builtin_triggers_count() -> None:
    triggers = load_builtin_triggers()
    assert len(triggers) == EXPECTED_BUILTIN_COUNT == 67


def test_builtins_are_marked_and_foldered() -> None:
    triggers = load_builtin_triggers()
    assert all(t.is_built_in for t in triggers)
    assert all(t.built_in_id for t in triggers)
    loose = [t.trigger_name for t in triggers if not t.built_in_folder.strip()]
    assert loose == []
    utility = {t.trigger_name for t in triggers if t.built_in_folder == "Utility"}
    assert {"Levitate Fading", "Tells You", "Sense Heading", "Exp Timer"} <= utility


def test_nparseplus_utility_builtins_route_to_utility_section() -> None:
    """#14: the nparseplus-only utility built-ins load disabled, sit in the
    Utility folder, and target the overlay's utility section."""
    triggers = load_builtin_triggers()
    by_id = {t.built_in_id: t for t in triggers}
    for built_in_id, name in (
        ("builtin:np-rebuff-request", "Rebuff Request"),
        ("builtin:np-out-of-mana", "Out of Mana"),
    ):
        trigger = by_id[built_in_id]
        assert trigger.trigger_name == name
        assert trigger.built_in_folder == "Utility"
        assert trigger.trigger_enabled is False
        assert trigger.basic is not None
        assert trigger.basic.overlay_section == "utility"


def test_rebuff_request_matches_buff_tell() -> None:
    trigger = next(
        t for t in load_builtin_triggers() if t.built_in_id == "builtin:np-rebuff-request"
    )
    trigger.trigger_enabled = True
    assert trigger.matches("Soandso tells you, 'can I get buffs please'")
    assert trigger.matches("Soandso tells you, 'rebuff?'")
    assert not trigger.matches("Soandso tells you, 'where is the group'")
    assert trigger.expand(trigger.effective_basic().display_text) == "Rebuff: Soandso"


def test_tells_you_ignores_npc_senders_with_spaces() -> None:
    trigger = next(t for t in load_builtin_triggers() if t.built_in_id == "builtin:tells-you")
    trigger.trigger_enabled = True
    trigger.player_name = "Gandalf"

    assert trigger.matches("Thalistair tells you, 'omw'")
    assert trigger.matches("Thalistair -> Gandalf: omw")
    assert trigger.expand(trigger.effective_basic().display_text) == "Thalistair sent a tell"

    merchant = (
        "Peron ThreadSpinner tells you, 'That'll be 3 gold 2 copper for the "
        "Earring of the Frozen Skull.'"
    )
    assert not trigger.matches(merchant)
    assert not trigger.matches(
        "Cleonae Kalen tells you, 'I'll give you 9 gold 8 silver 8 copper per Globe of Fear'"
    )
    assert not trigger.matches("a spectre tells you, 'Attacking a spectre Master.'")


def test_fresh_seed_enables_only_top_level_encounters() -> None:
    rebuilt, changed = sync_builtin_triggers([])
    assert changed
    assert len(rebuilt) == EXPECTED_BUILTIN_COUNT
    assert all(t.built_in_id for t in rebuilt)
    for trigger in rebuilt:
        assert trigger.trigger_enabled == (trigger.built_in_folder == "Encounters"), (
            trigger.trigger_name
        )
    assert not any(t.customized for t in rebuilt)


def test_sync_refreshes_non_customized_builtin_keeping_enabled_state_and_id() -> None:
    seeded, _ = sync_builtin_triggers([])
    target = next(t for t in seeded if t.built_in_id == "builtin:cant-see-target")
    target.trigger_enabled = True
    target.search_text = "stale local edit that is NOT marked customized"
    original_id = target.trigger_id

    rebuilt, _ = sync_builtin_triggers(seeded)

    refreshed = next(t for t in rebuilt if t.built_in_id == "builtin:cant-see-target")
    assert refreshed.search_text == "^You can't see your target"
    assert refreshed.trigger_enabled
    assert refreshed.trigger_id == original_id


def test_sync_keeps_customized_builtin_edits() -> None:
    seeded, _ = sync_builtin_triggers([])
    target = next(t for t in seeded if t.built_in_id == "builtin:cant-see-target")
    target.customized = True
    target.search_text = "edited by user"

    rebuilt, _ = sync_builtin_triggers(seeded)

    kept = next(t for t in rebuilt if t.built_in_id == "builtin:cant-see-target")
    assert kept.search_text == "edited by user"
    assert kept.is_built_in
    assert kept.built_in_folder == "Combat"


def test_orphaned_duplicate_merges_into_builtin_by_name() -> None:
    seeded, _ = sync_builtin_triggers([])
    tracked = next(t for t in seeded if t.built_in_id == "builtin:cant-see-target")
    tracked.trigger_enabled = False
    orphan = Trigger(
        trigger_name="Can't See Target",
        search_text="old user pattern",
        trigger_enabled=True,
        display_text_enabled=True,
        display_text="user display text",
    )
    orphan_id = orphan.trigger_id
    seeded.append(orphan)

    rebuilt, changed = sync_builtin_triggers(seeded)

    assert changed
    matches = [t for t in rebuilt if t.trigger_name == "Can't See Target"]
    assert len(matches) == 1
    merged = matches[0]
    assert merged.built_in_id == "builtin:cant-see-target"
    assert merged.is_built_in
    assert merged.trigger_id == orphan_id
    assert merged.search_text == "^You can't see your target"
    assert merged.display_text == "user display text"
    assert merged.trigger_enabled
    assert merged.customized


def test_orphaned_duplicate_merges_by_search_text() -> None:
    seeded, _ = sync_builtin_triggers([])
    seeded.append(
        Trigger(
            trigger_name="my fizzle alert",
            search_text="^Your spell fizzles!",
            trigger_enabled=True,
            basic=TriggerOutput(
                display_text_enabled=True,
                display_text="user fizzle text",
                audio_type=TriggerAudioType.TEXT_TO_SPEECH,
                tts_text="user fizzle tts",
            ),
        )
    )

    rebuilt, _ = sync_builtin_triggers(seeded)

    assert not any(t.trigger_name == "my fizzle alert" for t in rebuilt)
    merged = next(t for t in rebuilt if t.built_in_id == "builtin:spell-fizzle")
    assert merged.trigger_name == "Spell Fizzle"
    assert merged.basic is not None and merged.basic.display_text == "user fizzle text"
    assert merged.customized


def test_ambiguous_search_text_duplicate_is_left_alone() -> None:
    seeded, _ = sync_builtin_triggers([])
    before = len(seeded)
    shared_pattern = (
        r"(You feel your skin freeze\.|skin freezes\.|You resist the Silver Breath spell!)"
    )
    seeded.append(
        Trigger(
            trigger_name="my silver breath",
            search_text=shared_pattern,
            trigger_enabled=True,
        )
    )

    rebuilt, _ = sync_builtin_triggers(seeded)

    user = [t for t in rebuilt if t.trigger_name == "my silver breath"]
    assert len(user) == 1
    assert user[0].built_in_id is None
    assert len(rebuilt) == before + 1


def test_duplicate_of_customized_builtin_is_left_alone() -> None:
    seeded, _ = sync_builtin_triggers([])
    tracked = next(t for t in seeded if t.built_in_id == "builtin:cant-see-target")
    tracked.customized = True
    tracked.search_text = "edited by user"
    seeded.append(
        Trigger(
            trigger_name="Can't See Target",
            search_text="old user pattern",
            trigger_enabled=True,
        )
    )

    rebuilt, _ = sync_builtin_triggers(seeded)

    assert sum(1 for t in rebuilt if t.trigger_name == "Can't See Target") == 2
    kept = next(t for t in rebuilt if t.built_in_id == "builtin:cant-see-target")
    assert kept.search_text == "edited by user"


def test_builtin_copy_in_a_folder_is_not_adopted() -> None:
    seeded, _ = sync_builtin_triggers([])
    folder_id = "11111111-2222-3333-4444-555555555555"
    seeded.append(
        Trigger(
            trigger_name="Can't See Target",
            search_text="^You can't see your target",
            folder_id=folder_id,
            trigger_enabled=True,
        )
    )

    rebuilt, _ = sync_builtin_triggers(seeded)

    copies = [t for t in rebuilt if t.trigger_name == "Can't See Target" and not t.built_in_id]
    assert len(copies) == 1
    assert copies[0].folder_id == folder_id


def test_user_trigger_and_disable_state_survive_sync() -> None:
    seeded, _ = sync_builtin_triggers([])
    enraged = next(t for t in seeded if t.built_in_id == "builtin:enraged")
    assert enraged.trigger_enabled  # top-level Encounters seeds enabled
    enraged.trigger_enabled = False
    mine = Trigger(trigger_name="My Trigger", search_text="mine", trigger_enabled=True)
    seeded.append(mine)

    rebuilt, _ = sync_builtin_triggers(seeded)

    assert not next(t for t in rebuilt if t.built_in_id == "builtin:enraged").trigger_enabled
    assert any(t.trigger_name == "My Trigger" and t.trigger_enabled for t in rebuilt)


def test_effective_triggers_from_stored_dicts() -> None:
    stored = [
        {
            "built_in_id": "builtin:enraged",
            "trigger_enabled": False,
            "trigger_name": "Enraged",
            "search_text": "stale",
        },
        {"trigger_name": "Mine", "search_text": "hello", "trigger_enabled": True},
    ]
    rebuilt = effective_triggers(stored)
    assert len(rebuilt) == EXPECTED_BUILTIN_COUNT + 1
    enraged = next(t for t in rebuilt if t.built_in_id == "builtin:enraged")
    assert not enraged.trigger_enabled  # user disable respected
    assert enraged.search_text == "{npc} has become ENRAGED."  # definition refreshed
