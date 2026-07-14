"""Candidate matching tests (SpellDurations.MatchClosestLevelToSpell list form)."""

from __future__ import annotations

from nparseplus.core.enums import PlayerClass
from nparseplus.core.spells.matching import (
    iter_target_splits,
    match_closest_level_to_spell,
    possessive_message,
)
from nparseplus.core.spells.spells_us import SpellBook


def test_clarity_guess_cleric54(spell_book: SpellBook) -> None:
    candidates = spell_book.cast_on_other("looks very tranquil.")
    found = match_closest_level_to_spell(candidates, PlayerClass.CLERIC, 54)
    assert found is not None and found.name == "Clarity II"


def test_aegolism_guess(spell_book: SpellBook) -> None:
    aego = spell_book.spell_by_name("Aegolism")
    assert aego is not None
    candidates = spell_book.cast_on_other(aego.cast_on_other)
    found = match_closest_level_to_spell(candidates, PlayerClass.CLERIC, 54)
    assert found is not None and found.name == "Aegolism"


def test_burnout_guess_mag14(spell_book: SpellBook) -> None:
    burnout = spell_book.spell_by_name("Burnout")
    assert burnout is not None
    candidates = spell_book.cast_on_other(burnout.cast_on_other)
    found = match_closest_level_to_spell(candidates, PlayerClass.MAGICIAN, 14)
    assert found is not None and found.name == "Burnout"


def test_naltron_guess_sk60(spell_book: SpellBook) -> None:
    naltron = spell_book.spell_by_name("Naltron's Mark")
    assert naltron is not None
    candidates = [s for s in spell_book.spells if s.cast_on_other == naltron.cast_on_other]
    found = match_closest_level_to_spell(candidates, PlayerClass.SHADOW_KNIGHT, 60)
    assert found is not None and found.name == "Symbol of Naltron"


def test_possessive_message() -> None:
    assert (
        possessive_message("Joe's hand is covered with a dull aura.")
        == "'s hand is covered with a dull aura."
    )
    assert possessive_message("no apostrophe here") is None


def test_iter_target_splits() -> None:
    splits = list(iter_target_splits("an Jobober rager yawns."))
    assert splits[0] == ("an", "Jobober rager yawns.")
    assert splits[1] == ("an Jobober", "rager yawns.")
    assert splits[2] == ("an Jobober rager", "yawns.")
    assert len(splits) == 3


def test_iter_target_splits_stops_at_five_words() -> None:
    message = "one two three four five six seven eight"
    assert len(list(iter_target_splits(message))) == 5
