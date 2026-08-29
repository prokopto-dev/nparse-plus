"""The player's class disambiguates same-message spells (#177).

Every case here drives off the pinned spells_us.txt fixture, not a hand-built
Spell: the bug was that real candidate lists contain a spell from another class
whose level happens to sit nearer the player's, and a fixture built to show the
rule would not have caught it.

``test_matching.py`` holds the four EQtoolsTests-derived cases and is
deliberately untouched — they all go through the bystander path, which is
EQTool's rule verbatim.
"""

from __future__ import annotations

import logging

import pytest

from nparseplus.core.enums import PlayerClass
from nparseplus.core.spells.matching import (
    SpellMatchMode,
    log_candidates,
    match_closest_level_to_spell,
    other_matches,
)
from nparseplus.core.spells.spells_us import SpellBook

SPIRIT_OF_WOLF = "You feel the spirit of wolf enter you."
EYES_TINGLE = "Your eyes tingle."
LEVITATE = "Your feet leave the ground."
LUPINE_AURA = "is surrounded by a brief lupine aura."


def _guess(candidates, player_class, level, mode=SpellMatchMode.PARTICIPANT):
    found = match_closest_level_to_spell(candidates, player_class, level, mode=mode)
    assert found is not None
    return found.name


# -- rule 1/2: the player is the caster or the target --------------------------


@pytest.mark.parametrize(
    ("player_class", "level", "expected"),
    [
        # The reported bug: Pack Spirit is DRUID 39, so it beat a shaman's own
        # Spirit of Wolf (SHAMAN 9) on raw level distance for anyone near 40.
        (PlayerClass.SHAMAN, 40, "Spirit of Wolf"),
        (PlayerClass.RANGER, 45, "Spirit of Wolf"),
        (PlayerClass.SHAMAN, 9, "Spirit of Wolf"),
        (PlayerClass.RANGER, 1, "Spirit of Wolf"),  # not grown into it yet (rule 2)
        # A druid really can cast both, and Pack Spirit is the higher-level one
        # they know — so for a druid it is the better answer, not a mis-guess.
        (PlayerClass.DRUID, 40, "Pack Spirit"),
        (PlayerClass.DRUID, 39, "Pack Spirit"),
        (PlayerClass.DRUID, 20, "Spirit of Wolf"),  # too low for Pack Spirit
    ],
)
def test_spirit_of_wolf_resolves_by_the_players_class(
    spell_book: SpellBook, player_class: PlayerClass, level: int, expected: str
) -> None:
    assert _guess(spell_book.cast_on_you(SPIRIT_OF_WOLF), player_class, level) == expected


@pytest.mark.parametrize(
    ("player_class", "level", "expected"),
    [
        # Ten spells share "Your eyes tingle.". These levels are ones the
        # unfixed matcher got WRONG — checked against EQTool's own function, so
        # each is a real regression guard rather than a case that passed anyway.
        # (The two levels #177 quoted, shaman 30 and magician 16, happen to be
        # among the ones it already got right; they are kept below as coverage,
        # not as the guard.)
        (PlayerClass.SHAMAN, 45, "Ultravision"),  # was See Invisible (no shaman entry)
        (PlayerClass.SHAMAN, 50, "Ultravision"),  # was Acumen (shaman 56, not yet)
        (PlayerClass.SHAMAN, 25, "Spirit Sight"),  # was Ultravision (shaman 29, not yet)
        (PlayerClass.MAGICIAN, 30, "See Invisible"),  # was Ultravision — no mage entry
        (PlayerClass.MAGICIAN, 55, "See Invisible"),  # was Acumen — likewise
        (PlayerClass.WIZARD, 25, "See Invisible"),  # was Ultravision
        (PlayerClass.ENCHANTER, 40, "Ultravision"),  # was See Invisible (enc 8 vs 29)
        # Already correct before the fix; kept so a future change cannot quietly
        # break what did work.
        (PlayerClass.SHAMAN, 30, "Ultravision"),
        (PlayerClass.MAGICIAN, 16, "See Invisible"),
        (PlayerClass.ENCHANTER, 29, "Ultravision"),
        (PlayerClass.WIZARD, 10, "See Invisible"),
        (PlayerClass.SHAMAN, 9, "Spirit Sight"),
    ],
)
def test_see_invisible_and_ultravision_do_not_swap(
    spell_book: SpellBook, player_class: PlayerClass, level: int, expected: str
) -> None:
    assert _guess(spell_book.cast_on_you(EYES_TINGLE), player_class, level) == expected


def test_the_eyes_tingle_guard_actually_discriminates(spell_book: SpellBook) -> None:
    """The cases above are only guards if the OLD behaviour failed them.

    EQTool's rule transcribed from SpellDurations.cs:71 at d8e8084f — the code
    this repo shipped before #177 — so the test proves the fix changed
    something rather than asserting what was already true.
    """

    def eqtool_match(spells, level: int):
        smallest, closest = level, None
        for spell in spells:
            for class_level in spell.class_levels.values():
                if abs(class_level - level) < smallest:
                    closest, smallest = spell, abs(class_level - level)
        return closest

    candidates = spell_book.cast_on_you(EYES_TINGLE)
    for player_class, level, expected in (
        (PlayerClass.SHAMAN, 45, "Ultravision"),
        (PlayerClass.MAGICIAN, 30, "See Invisible"),
        (PlayerClass.ENCHANTER, 40, "Ultravision"),
    ):
        before = eqtool_match(candidates, level)
        assert before is not None and before.name != expected, (
            f"{player_class.name} {level} was already {expected} before the fix"
        )
        assert _guess(candidates, player_class, level) == expected


def test_rule_one_prefers_the_best_version_you_know(spell_book: SpellBook) -> None:
    """Rule 1 is 'highest requirement at or below my level', not 'closest'.

    A shaman 30 is one level past Ultravision (29) and twenty-one past Spirit
    Sight (9); both are equally castable, and the newer one is the answer.
    """
    candidates = spell_book.cast_on_you(EYES_TINGLE)
    assert _guess(candidates, PlayerClass.SHAMAN, 30) == "Ultravision"
    assert _guess(candidates, PlayerClass.SHAMAN, 28) == "Spirit Sight"


def test_rule_two_falls_back_to_closest_when_nothing_is_castable_yet(
    spell_book: SpellBook,
) -> None:
    """A class that has the spell but not the level still gets a class answer."""
    candidates = spell_book.cast_on_you(EYES_TINGLE)
    # A level-1 magician cannot cast See Invisible (16) but it is the only
    # candidate on their list, so it beats every other class's nearer number.
    assert _guess(candidates, PlayerClass.MAGICIAN, 1) == "See Invisible"


# -- classed beats classless ---------------------------------------------------


@pytest.mark.parametrize(
    ("player_class", "level"),
    [
        (PlayerClass.SHAMAN, 40),  # a class that can cast it
        (PlayerClass.WARRIOR, 40),  # and one that cannot
        (PlayerClass.WIZARD, 30),
    ],
)
def test_levitate_beats_its_classless_namesakes(
    spell_book: SpellBook, player_class: PlayerClass, level: int
) -> None:
    candidates = spell_book.cast_on_you(LEVITATE)
    classless = {s.name for s in candidates if not s.class_levels}
    assert {"Levitation", "Levity", "Flight"} <= classless  # the fixture still has them
    assert _guess(candidates, player_class, level) == "Levitate"
    assert _guess(candidates, player_class, level, mode=SpellMatchMode.BYSTANDER) == "Levitate"


# -- rule 3: the bystander path is EQTool's, unchanged -------------------------


def test_bystander_mode_never_reads_the_observers_class(spell_book: SpellBook) -> None:
    """A third party's cast says nothing about the watcher's class, so the
    answer must not move with it — only with their level, as the C# does."""
    candidates = spell_book.cast_on_other(LUPINE_AURA)
    answers = {
        _guess(candidates, player_class, 40, mode=SpellMatchMode.BYSTANDER)
        for player_class in (
            PlayerClass.WARRIOR,
            PlayerClass.CLERIC,
            PlayerClass.DRUID,
            PlayerClass.SHAMAN,
        )
    }
    assert len(answers) == 1


def test_participant_mode_is_opt_in(spell_book: SpellBook) -> None:
    """The default is bystander, which is what keeps the EQtoolsTests cases in
    ``test_matching.py`` passing unedited — the class rules are a layer above
    the C# rule, reached only when the player is part of the cast."""
    candidates = spell_book.cast_on_you(SPIRIT_OF_WOLF)
    assert match_closest_level_to_spell(candidates, PlayerClass.SHAMAN, 40).name == "Pack Spirit"
    assert (
        match_closest_level_to_spell(
            candidates, PlayerClass.SHAMAN, 40, mode=SpellMatchMode.PARTICIPANT
        ).name
        == "Spirit of Wolf"
    )


def test_no_class_falls_through_to_the_c_sharp_tail(spell_book: SpellBook) -> None:
    """Unknown class: the first candidate with a sane class level, as in C#."""
    candidates = spell_book.cast_on_you(SPIRIT_OF_WOLF)
    found = match_closest_level_to_spell(candidates, None, None, mode=SpellMatchMode.PARTICIPANT)
    assert found is not None and found.name == "Pack Spirit"


def test_empty_candidate_list_is_no_match() -> None:
    assert match_closest_level_to_spell([], PlayerClass.SHAMAN, 40) is None


# -- the rejected candidates ---------------------------------------------------


def test_other_matches_lists_what_the_guess_passed_over(spell_book: SpellBook) -> None:
    candidates = spell_book.cast_on_you(SPIRIT_OF_WOLF)
    chosen = match_closest_level_to_spell(
        candidates, PlayerClass.SHAMAN, 40, mode=SpellMatchMode.PARTICIPANT
    )
    rest = other_matches(candidates, chosen)
    assert [s.name for s in rest] == ["Pack Spirit"]
    assert chosen not in rest


def test_other_matches_deduplicates_by_name(spell_book: SpellBook) -> None:
    levitate = spell_book.spell_by_name("Levitate")
    assert levitate is not None
    rest = other_matches([levitate, levitate], None)
    assert [s.name for s in rest] == ["Levitate"]


def test_other_matches_of_a_single_candidate_is_empty(spell_book: SpellBook) -> None:
    cleanse = spell_book.cast_on_you("You feel cleansed.")
    assert len(cleanse) == 1
    assert other_matches(cleanse, cleanse[0]) == []


# -- diagnostics ---------------------------------------------------------------


def test_log_candidates_names_the_whole_list(spell_book: SpellBook, caplog) -> None:
    """The user's spells_us.txt need not be ours, so a mis-guess is only
    reportable if the log carries the candidates it was chosen from."""
    candidates = spell_book.cast_on_you(EYES_TINGLE)
    chosen = match_closest_level_to_spell(
        candidates, PlayerClass.SHAMAN, 30, mode=SpellMatchMode.PARTICIPANT
    )
    with caplog.at_level(logging.DEBUG, logger="nparseplus.core.spells.matching"):
        log_candidates("cast on you", EYES_TINGLE, candidates, chosen)
    text = caplog.text
    assert "Ultravision" in text and "See Invisible" in text
    assert "SHAMAN:29" in text  # the levels are the evidence, not just the names
    assert "classless" in text  # and the ones with no class table are named too


def test_log_candidates_stays_quiet_for_an_unambiguous_line(spell_book: SpellBook, caplog) -> None:
    cleanse = spell_book.cast_on_you("You feel cleansed.")
    with caplog.at_level(logging.DEBUG, logger="nparseplus.core.spells.matching"):
        log_candidates("cast on you", "You feel cleansed.", cleanse, cleanse[0])
    assert caplog.text == ""
