"""Loader tests — ported from SpellMatchingTests.cs plus fixup spot checks."""

from __future__ import annotations

from nparseplus.core.enums import PlayerClass, ResistType, SpellType
from nparseplus.core.spells.spells_us import SpellBook, parse_spell_line

GRIM_AURA_LINE = (
    "8639^Grim Aura^PLAYER_1^^^^A dull aura covers your hand.^"
    "'s hand is covered with a dull aura.^The grim aura fades.^0^0^0^0^3000^2250^2250"
    "^3^270^0^25^3^0^0^0^0^0^0^0^0^0^0^0^0^0^0^0^0^0^0^0^0^0^0^0^10^0^0^0^0^0^0^0^0^0^0^0"
    "^2503^2108^-1^-1^-1^-1^1^1^1^1^-1^-1^-1^-1^102^100^100^100^100^100^100^100^100^100^100"
    "^100^0^1^0^0^2^254^254^254^254^254^254^254^254^254^254^254^6^25^5^-1^0^0^255^255^255"
    "^255^22^255^255^255^255^255^4^255^255^255^255^255^43^0^0^8^0^0^0^0^0^0^0^0^0^0^0^0^0"
    "^0^0^0^0^0^100^0^37^94^0^0^0^0^0^0^0^0^0^0^0^7^0^0^0^0^0^0^0^0^0^0^0^0^0^0^0^0^0^0^0"
    "^5^101^12^92^3^270^0^0^0^0^3^105^0^0^0^0^0^0^0^0^0^0^1^1^0^0^0^0^0^-1^0^0^0^1^0^0^1^1^^0"
)


def test_book_loads(spell_book: SpellBook) -> None:
    assert len(spell_book.spells) > 4000
    assert spell_book.spell_by_name("Clarity") is not None
    # lookups are case-insensitive, as in EQTool
    assert spell_book.spell_by_name("clarity") is not None


def test_parse_p99_grim_aura_line() -> None:
    raw = parse_spell_line(GRIM_AURA_LINE)
    assert raw is not None
    assert raw.id == 8639
    assert raw.name == "Grim Aura"
    assert raw.cast_on_you == "A dull aura covers your hand."
    assert raw.cast_on_other == "'s hand is covered with a dull aura."
    assert raw.spell_fades == "The grim aura fades."
    assert raw.casttime == 3000
    assert raw.recast_time == 2250
    assert raw.buffdurationformula == 3
    assert raw.buffduration == 270
    assert raw.classes == {PlayerClass.SHADOW_KNIGHT: 22, PlayerClass.NECROMANCER: 4}


def test_grim_aura_in_book(spell_book: SpellBook) -> None:
    spell = spell_book.spell_by_name("Grim Aura")
    assert spell is not None
    assert spell.resist_type is ResistType.NONE
    assert spell.class_levels[PlayerClass.NECROMANCER] == 4


def test_ignore_lists(spell_book: SpellBook) -> None:
    # IgnoreSpells / name-substring skips
    assert spell_book.spell_by_name("Complete Heal") is None
    assert spell_book.spell_by_name("Translocate") is None
    assert spell_book.spell_by_name("Markar's Translocation") is None
    # Roman numeral variants skipped unless allowlisted
    assert spell_book.spell_by_name("Clarity II") is not None  # Clarity allowlisted
    assert spell_book.spell_by_name("Burnout III") is not None  # Burnout allowlisted


def test_fixups(spell_book: SpellBook) -> None:
    pacify = spell_book.spell_by_name("Pacify")
    assert pacify is not None and pacify.buff_duration_ticks == 35
    bind_sight = spell_book.spell_by_name("Bind Sight")
    assert bind_sight is not None and bind_sight.buff_duration_ticks == 999
    loh = spell_book.spell_by_name("Lay on Hands")
    assert loh is not None and loh.recast_time_ms == 4_320_000
    # Harm Touch is the same 72-minute reuse; the file claims 30 s for both.
    harm_touch = spell_book.spell_by_name("Harm Touch")
    assert harm_touch is not None and harm_touch.recast_time_ms == 4_320_000
    # Maniacal Strength renamed to match the (misspelled) log message
    assert spell_book.spell_by_name("Manicial Strength") is not None
    assert spell_book.spell_by_name("Maniacal Strength") is None
    # Peggy Levitate synthesized from Levitate
    peggy = spell_book.spell_by_name("Peggy Levitate")
    assert peggy is not None
    assert peggy.buff_duration_ticks == 120
    assert peggy.buff_duration_formula == 12
    assert peggy.cast_time_ms == 6000
    # LowerElement given a wizard entry (flux staff)
    lower = spell_book.spell_by_name("LowerElement")
    assert lower is not None and lower.class_levels.get(PlayerClass.WIZARD) == 51


def test_epic_cast_times(spell_book: SpellBook) -> None:
    shissar = spell_book.spell_by_name("Speed of the Shissar")
    assert shissar is not None
    assert shissar.cast_time_ms == 6000
    assert shissar.class_levels.get(PlayerClass.ENCHANTER) == 46
    curse = spell_book.spell_by_name("Curse of the Spirits")
    assert curse is not None
    assert curse.cast_time_ms == 9000
    assert curse.class_levels.get(PlayerClass.SHAMAN) == 46


def test_castable_by_everyone(spell_book: SpellBook) -> None:
    petals = spell_book.spell_by_name("Aura of Black Petals")
    assert petals is not None
    assert petals.class_levels.get(PlayerClass.OTHER) == 46


def test_lookup_dicts(spell_book: SpellBook) -> None:
    # TestSpellMatchCorrectlyManaSieve: unique cast_on_other message
    sieve = spell_book.spell_by_name("Mana Sieve")
    assert sieve is not None
    assert [s.name for s in spell_book.cast_on_other(sieve.cast_on_other)] == ["Mana Sieve"]
    # Clarity line maps to multiple candidates
    assert len(spell_book.cast_on_other("looks very tranquil.")) > 1
    # worn-off table
    fades = spell_book.worn_off("The grim aura fades.")
    assert any(s.name == "Grim Aura" for s in fades)
    # you-cast table only contains spells some class can cast
    assert spell_book.you_cast("Clarity")
    assert all(
        any(level > 0 for level in s.class_levels.values()) for s in spell_book.you_cast("Clarity")
    )


def test_target_types_map_to_enum(spell_book: SpellBook) -> None:
    spell = spell_book.spell_by_name("Gift of Pure Thought")
    assert spell is not None
    assert spell.spell_type is SpellType.GROUP_V2
    assert all(isinstance(s.spell_type, SpellType) for s in spell_book.spells)


def test_discipline_double_period_messages_are_normalized(spell_book: SpellBook) -> None:
    """The file's trailing ".." makes a discipline permanently unmatchable: the
    parser strips a trailing ".." off the LOG line, so a ".." in the DATA can
    never be the lookup key either. Puretone (bard 60) was the live casualty."""
    for name in ("Defensive Discipline", "Puretone Discipline"):
        spell = spell_book.spell_by_name(name)
        assert spell is not None
        assert not spell.cast_on_you.endswith("..")
        assert [s.name for s in spell_book.cast_on_you(spell.cast_on_you)]

    # Scoped to disciplines on purpose — the artifact is all over the file and
    # normalizing the rest would move existing best-guess matches.
    assert any(
        s.cast_on_you.endswith("..") for s in spell_book.spells if not s.name.endswith("Discipline")
    )


def test_classless_duplicate_does_not_shadow_a_trainable_self_buff(
    spell_book: SpellBook,
) -> None:
    """A later classless "Whirlwind" copy claimed the message key first, and the
    C# guard then dropped Whirlwind Discipline (monk 53) entirely."""
    disc = spell_book.spell_by_name("Whirlwind Discipline")
    assert disc is not None
    candidates = spell_book.cast_on_you(disc.cast_on_you)
    assert candidates, "Whirlwind Discipline is missing from the cast-on-you table"
    assert candidates[0].name == "Whirlwind Discipline"
    # The duplicate is demoted, not discarded.
    assert [s.name for s in candidates[1:]] == ["Whirlwind"]


def test_the_peggy_clicky_does_not_outrank_the_real_levitate(
    spell_book: SpellBook,
) -> None:
    """#177: "Peggy Levitate" is a synthetic copy of the Levitate line with the
    Peggy Cloak's duration, so it carries an identical class table and nothing
    in the data can separate the two. EQTool reaches it by name from
    YourItemBeginsToGlowHandler, never from a cast message — registering it
    before the genuine spell made it win every "Your feet leave the ground."
    on candidate order alone, which is what the reporter saw.
    """
    levitate = spell_book.spell_by_name("Levitate")
    peggy = spell_book.spell_by_name("Peggy Levitate")
    assert levitate is not None and peggy is not None
    assert levitate.class_levels == peggy.class_levels  # the reason order decides
    for candidates in (
        spell_book.cast_on_you(levitate.cast_on_you),
        spell_book.cast_on_other(levitate.cast_on_other),
    ):
        assert [s.name for s in candidates][:2] == ["Levitate", "Peggy Levitate"]
