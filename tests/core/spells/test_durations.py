"""Duration/level tests ported from SpellMatchingTests.cs."""

from __future__ import annotations

from nparseplus.core.enums import PlayerClass
from nparseplus.core.spells.durations import get_duration_seconds, match_closest_level
from nparseplus.core.spells.spells_us import SpellBook


def test_match_closest_level_sk30_grim_aura(spell_book: SpellBook) -> None:
    spell = spell_book.spell_by_name("Grim Aura")
    assert spell is not None
    assert match_closest_level(spell, PlayerClass.SHADOW_KNIGHT, 30) == 30


def test_match_closest_level_sk60_grim_aura(spell_book: SpellBook) -> None:
    spell = spell_book.spell_by_name("Grim Aura")
    assert spell is not None
    assert match_closest_level(spell, PlayerClass.SHADOW_KNIGHT, 60) == 60


def test_match_closest_level_necro1_grim_aura(spell_book: SpellBook) -> None:
    spell = spell_book.spell_by_name("Grim Aura")
    assert spell is not None
    assert match_closest_level(spell, PlayerClass.NECROMANCER, 1) == 4


def test_match_closest_level_necro60_grim_aura(spell_book: SpellBook) -> None:
    spell = spell_book.spell_by_name("Grim Aura")
    assert spell is not None
    assert match_closest_level(spell, PlayerClass.NECROMANCER, 60) == 60


def test_match_closest_level_no_class(spell_book: SpellBook) -> None:
    spell = spell_book.spell_by_name("Grim Aura")
    assert spell is not None
    assert match_closest_level(spell, None, 60) == 60


def test_match_closest_level_journeyman_boots(spell_book: SpellBook) -> None:
    spell = spell_book.spell_by_name("JourneymanBoots")
    assert spell is not None
    assert match_closest_level(spell, PlayerClass.SHADOW_KNIGHT, 35) == 35


def test_pacify_duration(spell_book: SpellBook) -> None:
    spell = spell_book.spell_by_name("Pacify")
    assert spell is not None
    assert get_duration_seconds(spell, PlayerClass.ENCHANTER, 50) == 210


def test_bind_sight_duration(spell_book: SpellBook) -> None:
    spell = spell_book.spell_by_name("Bind Sight")
    assert spell is not None
    assert get_duration_seconds(spell, PlayerClass.RANGER, 50) == 660


def test_clarity2_duration_cleric54(spell_book: SpellBook) -> None:
    # TestClairityDurationGuess_part1
    spell = spell_book.spell_by_name("Clarity II")
    assert spell is not None
    assert match_closest_level(spell, PlayerClass.CLERIC, 54) == 54
    assert get_duration_seconds(spell, PlayerClass.CLERIC, 54) == 2100


def test_alliance_duration_zero(spell_book: SpellBook) -> None:
    spell = spell_book.spell_by_name("Alliance")
    assert spell is not None
    assert get_duration_seconds(spell, PlayerClass.SHADOW_KNIGHT, 35) == 0


def test_turgurs_duration_necro60(spell_book: SpellBook) -> None:
    spell = spell_book.spell_by_name("Turgur's Insects")
    assert spell is not None
    assert abs(get_duration_seconds(spell, PlayerClass.NECROMANCER, 60) / 60.0 - 6) < 0.2


def test_manicial_strength_duration(spell_book: SpellBook) -> None:
    spell = spell_book.spell_by_name("Manicial Strength")
    assert spell is not None
    assert get_duration_seconds(spell, PlayerClass.SHAMAN, 60) / 60.0 == 144
