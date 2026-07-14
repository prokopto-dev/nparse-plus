"""Token/compile semantics ported from EQtoolsTests/TriggerTests.cs."""

from nparseplus.core.triggers.model import Trigger, TriggerAudioType, TriggerOutput


def test_happy_path_three_named_groups() -> None:
    trigger = Trigger(
        search_text="^{count} {containers} of {beverage} on the wall",
        display_text_enabled=True,
        display_text="{count} {containers} of {beverage}",
        audio_text_enabled=True,
        audio_text="{count} {containers} of {beverage}",
        trigger_enabled=True,
    )
    assert trigger.matches("99 flagons of wine on the wall")
    assert trigger.expand(trigger.display_text) == "99 flagons of wine"
    assert trigger.expand(trigger.audio_text) == "99 flagons of wine"


def test_zero_named_groups_plain_regex() -> None:
    trigger = Trigger(search_text="^Can you hear me now?", trigger_enabled=True)
    assert trigger.matches("Can you hear me now?")
    assert not trigger.matches("static on the line")


def test_plain_substring_mode_is_case_insensitive() -> None:
    trigger = Trigger(
        search_text="You feel as if you are about to fall.",
        use_regex=False,
        trigger_enabled=True,
    )
    assert trigger.matches("YOU FEEL AS IF YOU ARE ABOUT TO FALL.")
    assert trigger.matches("prefix You feel as if you are about to fall. suffix")
    assert not trigger.matches("You feel fine.")
    # regex metacharacters in the pattern are literal in substring mode
    weird = Trigger(search_text="a+b(c)", use_regex=False, trigger_enabled=True)
    assert weird.matches("this line has a+b(c) in it")
    assert not weird.matches("this line has aab(c) in it")


def test_context_token_substitutes_into_pattern_and_output() -> None:
    trigger = Trigger(
        search_text="{c} has been slain by {s}",
        display_text_enabled=True,
        display_text="{c} died to {s}!",
        trigger_enabled=True,
    )
    trigger.player_name = "Gandalf"
    assert trigger.matches("Gandalf has been slain by a Balrog")
    assert trigger.expand(trigger.display_text) == "Gandalf died to a Balrog!"


def test_context_token_recompiles_when_context_changes() -> None:
    trigger = Trigger(search_text="{c} waves", trigger_enabled=True)
    trigger.player_name = "Gandalf"
    assert trigger.matches("Gandalf waves")
    assert not trigger.matches("Frodo waves")

    trigger.player_name = "Frodo"
    assert trigger.matches("Frodo waves")
    assert not trigger.matches("Gandalf waves")


def test_context_token_escapes_regex_metacharacters() -> None:
    trigger = Trigger(search_text="{c} waves", trigger_enabled=True)
    trigger.player_name = "a.b(c)"
    assert trigger.matches("a.b(c) waves")
    # the '.' must be literal, not a regex wildcard
    assert not trigger.matches("axb(c) waves")


def test_uppercase_context_token_also_substitutes() -> None:
    trigger = Trigger(search_text="^{C} snores", trigger_enabled=True)
    trigger.player_name = "Gandalf"
    assert trigger.matches("Gandalf snores loudly")
    trigger.player_name = "Frodo"
    assert not trigger.matches("Gandalf snores loudly")
    assert trigger.matches("Frodo snores loudly")


def test_counter_token_reflects_match_count() -> None:
    trigger = Trigger(
        search_text="You hit the target",
        display_text_enabled=True,
        display_text="Hit number {COUNTER}",
        trigger_enabled=True,
    )
    trigger.player_name = "Gandalf"
    assert trigger.matches("You hit the target")
    trigger.current_counter += 1
    assert trigger.expand(trigger.display_text) == "Hit number 1"
    trigger.current_counter += 1
    assert trigger.expand(trigger.display_text) == "Hit number 2"


def test_counter_token_matches_every_case() -> None:
    trigger = Trigger(display_text="{COUNTER} {counter} {Counter} {CoUnTeR}")
    trigger.player_name = "Gandalf"
    trigger.current_counter = 7
    assert trigger.expand(trigger.display_text) == "7 7 7 7"


def test_counter_resets_when_character_changes() -> None:
    trigger = Trigger(display_text="count={counter}")
    trigger.player_name = "Gandalf"
    trigger.current_counter += 1
    trigger.current_counter += 1
    assert trigger.expand(trigger.display_text) == "count=2"

    trigger.player_name = "Frodo"
    assert trigger.expand(trigger.display_text) == "count=0"
    trigger.current_counter += 1
    assert trigger.expand(trigger.display_text) == "count=1"

    trigger.current_counter = 0
    assert trigger.expand(trigger.display_text) == "count=0"


def test_dotnet_named_groups_are_translated() -> None:
    trigger = Trigger(
        search_text=r"^(?<name>[\w`]+) begins to cast",
        display_text="caster: {name}",
        trigger_enabled=True,
    )
    assert trigger.matches("Xanuusux begins to cast a spell.")
    assert trigger.expand(trigger.display_text) == "caster: Xanuusux"
    # lookbehind syntax must survive the translation
    lookbehind = Trigger(search_text=r"(?<=hits )you", trigger_enabled=True)
    assert lookbehind.matches("A rat hits you for 3 points of damage.")


def test_placeholder_group_matches_backticks_and_spaces() -> None:
    trigger = Trigger(
        search_text="^{npc} has become ENRAGED",
        display_text="{npc} ENRAGED",
        trigger_enabled=True,
    )
    assert trigger.matches("Vessel Drozlin`s spirit has become ENRAGED.")
    assert trigger.expand(trigger.display_text) == "Vessel Drozlin`s spirit ENRAGED"


def test_unknown_output_token_is_left_in_place() -> None:
    trigger = Trigger(
        search_text="^{count} bottles",
        display_text="{count} {typo}",
        trigger_enabled=True,
    )
    assert trigger.matches("99 bottles")
    # {typo} was never captured; the C# code leaves unknown tokens alone.
    assert trigger.expand(trigger.display_text) == "99 {typo}"


def test_zone_gating() -> None:
    trigger = Trigger(search_text="wind begins", zone="kael", trigger_enabled=True)
    assert trigger.matches_zone("kael")
    assert trigger.matches_zone("KAEL")
    assert not trigger.matches_zone("fear")
    assert not trigger.matches_zone("")
    assert not trigger.matches_zone(None)
    unzoned = Trigger(search_text="anything")
    assert unzoned.matches_zone("anywhere")
    assert unzoned.matches_zone(None)


def test_effective_basic_falls_back_to_legacy_fields() -> None:
    legacy = Trigger(
        search_text="x",
        display_text_enabled=True,
        display_text="legacy display",
        audio_text_enabled=True,
        audio_text="legacy audio",
    )
    basic = legacy.effective_basic()
    assert basic.display_text_enabled
    assert basic.display_text == "legacy display"
    assert basic.audio_type == TriggerAudioType.TEXT_TO_SPEECH
    assert basic.tts_text == "legacy audio"

    modern = Trigger(search_text="x", basic=TriggerOutput(tts_text="modern"))
    assert modern.effective_basic().tts_text == "modern"


def test_use_regex_defaults_to_true() -> None:
    assert Trigger(search_text="^abc$").effective_use_regex
    assert not Trigger(search_text="abc", use_regex=False).effective_use_regex


def test_invalid_pattern_never_matches() -> None:
    trigger = Trigger(search_text="([unclosed", trigger_enabled=True)
    assert not trigger.matches("anything ([unclosed anything")


def test_empty_line_or_pattern_never_matches() -> None:
    assert not Trigger(search_text="   ").matches("some line")
    assert not Trigger(search_text="x").matches("")
