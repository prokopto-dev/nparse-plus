"""Clicky (item-cast) durations scale with the CLICKER's own level.

This is the corrected mechanic, and it is the reverse of what issue #188
assumed. On Project 1999 an item effect is cast as if you cast the spell
yourself, at your own level; the ``at Level N`` on a wiki item page is only the
level at which you may begin clicking it. PR #190 read that number as a caster
level and shipped it in v2.28.2, which shortened 166 durations at level 60.
These tests exist so it cannot come back.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from nparseplus.core.enums import PlayerClass
from nparseplus.core.spells import durations as durations_module
from nparseplus.core.spells.durations import get_duration_seconds, match_closest_level
from nparseplus.core.spells.spells_us import SpellBook

# -- THE regression pin -------------------------------------------------------------


def test_levitate_reads_19_minutes_at_level_60(spell_book: SpellBook) -> None:
    """The exact number #188 filed as the bug, which was the correct answer.

    Levitate is duration formula 10 — ``min(level * 3 + 10, 190)`` ticks — and
    its 190-tick cap is exactly ``60 * 3 + 10``. EQ duration data is written so
    max level reaches the cap, which is the strongest evidence the clicker's
    own level is what scales it. v2.28.2 reported 10 minutes here.
    """
    spell = spell_book.spell_by_name("Levitate")
    assert spell is not None
    assert (spell.buff_duration_formula, spell.buff_duration_ticks) == (10, 190)
    assert spell.buff_duration_ticks == 60 * 3 + 10

    # A warrior cannot cast Levitate, so at level 60 this can only be a clicky.
    assert get_duration_seconds(spell, PlayerClass.WARRIOR, 60) == 1140  # 19.0 min


def test_clicky_duration_scales_with_the_clickers_level(spell_book: SpellBook) -> None:
    """The same item must read LONGER for a higher-level clicker."""
    spell = spell_book.spell_by_name("Levitate")
    assert spell is not None
    by_level = {
        level: get_duration_seconds(spell, PlayerClass.WARRIOR, level) for level in (20, 30, 45, 60)
    }
    assert by_level[60] > by_level[45] > by_level[30]
    assert by_level == {20: 762, 30: 762, 45: 870, 60: 1140}


def test_a_class_that_cannot_cast_the_spell_still_uses_its_own_level(
    spell_book: SpellBook,
) -> None:
    spell = spell_book.spell_by_name("Levitate")
    assert spell is not None
    assert PlayerClass.WARRIOR not in spell.class_levels
    assert match_closest_level(spell, PlayerClass.WARRIOR, 60) == 60


@pytest.mark.parametrize("spell_name", ["Spirit of Ox", "Invisibility", "Bramblecoat"])
def test_spells_v2_28_2_shortened_are_back_to_the_clickers_level(
    spell_book: SpellBook, spell_name: str
) -> None:
    """Three of the worst regressions (Spirit of Ox read 3 min, not 45)."""
    spell = spell_book.spell_by_name(spell_name)
    assert spell is not None
    assert match_closest_level(spell, PlayerClass.WARRIOR, 60) == 60


# -- the structural guard -----------------------------------------------------------


def test_the_duration_path_does_not_consult_the_click_level_table() -> None:
    """``itemcasts`` answers "can I click this yet", never "at what level".

    An AST check on the module's imports, not a text search: the docstring
    cross-references ``itemcasts`` on purpose, and the bug was the IMPORT plus
    one branch, so the import is the thing worth forbidding.
    """
    tree = ast.parse(inspect.getsource(durations_module))
    imported = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not any("itemcasts" in name for name in imported), imported


# -- EQTool parity, unchanged -------------------------------------------------------


def test_observer_paths_keep_eqtools_answer(spell_book: SpellBook) -> None:
    """A spell somebody ELSE cast (TestSlowForNecro)."""
    spell = spell_book.spell_by_name("Turgur's Insects")
    assert spell is not None
    seconds = get_duration_seconds(spell, PlayerClass.NECROMANCER, 60)
    assert abs(seconds / 60.0 - 6) < 0.2


def test_unknown_class_does_not_crash(spell_book: SpellBook) -> None:
    spell = spell_book.spell_by_name("Levitate")
    assert spell is not None
    assert get_duration_seconds(spell, None, 60) > 0


def test_classless_spells_do_not_crash(spell_book: SpellBook) -> None:
    """18 disciplines in the data carry no class table at all."""
    classless = [s for s in spell_book.spells if not s.class_levels]
    assert classless, "fixture no longer has a classless spell to guard"
    for spell in classless[:50]:
        get_duration_seconds(spell, PlayerClass.WARRIOR, 60)


@pytest.mark.parametrize(
    ("spell_name", "epic_class"),
    [
        ("Wrath of Nature", PlayerClass.DRUID),
        ("Celestial Tranquility", PlayerClass.MONK),
        ("Manifest Elements", PlayerClass.MAGICIAN),
    ],
)
def test_epic_clickies_take_the_ordinary_castable_branch(
    spell_book: SpellBook, spell_name: str, epic_class: PlayerClass
) -> None:
    """``_apply_epic_fixup`` writes ``classes[epic_class] = 46``, so the epic's
    owner resolves through the normal path at their own level."""
    spell = spell_book.spell_by_name(spell_name)
    assert spell is not None
    assert spell.class_levels == {epic_class: 46}
    assert match_closest_level(spell, epic_class, 60) == 60


# -- the class-level floor, bypassed for your own clicks -----------------------------


def test_your_own_click_is_not_floored_at_the_spells_class_level(
    spell_book: SpellBook,
) -> None:
    """EQTool returns ``max(your level, highest class level)``. For a clicky
    that floor is wrong: a level-35 warrior clicking Levitate is cast at 35,
    not at ranger-39."""
    spell = spell_book.spell_by_name("Levitate")
    assert spell is not None
    assert max(spell.class_levels.values()) == 39

    assert match_closest_level(spell, PlayerClass.WARRIOR, 35, own_cast=True) == 35
    assert get_duration_seconds(spell, PlayerClass.WARRIOR, 35, own_cast=True) == 690

    # The observed cast keeps EQTool's guess: their level is unknown.
    assert match_closest_level(spell, PlayerClass.WARRIOR, 35) == 39


def test_a_class_that_gets_the_spell_later_still_clicks_at_its_own_level(
    spell_book: SpellBook,
) -> None:
    """A level-20 ranger cannot cast Levitate (ranger 39), so this is a click."""
    spell = spell_book.spell_by_name("Levitate")
    assert spell is not None
    assert spell.class_levels[PlayerClass.RANGER] == 39
    assert match_closest_level(spell, PlayerClass.RANGER, 20, own_cast=True) == 20


def test_the_bypass_is_a_no_op_for_a_real_spellbook_cast(spell_book: SpellBook) -> None:
    """You cannot cast below your class's level for a spell, so where a genuine
    self-cast is possible the floor was already your own level."""
    spell = spell_book.spell_by_name("Levitate")
    assert spell is not None
    for level in (14, 20, 39, 45, 60):  # druid gets Levitate at 14
        assert get_duration_seconds(
            spell, PlayerClass.DRUID, level, own_cast=True
        ) == get_duration_seconds(spell, PlayerClass.DRUID, level)


def test_an_unset_level_falls_through_instead_of_reading_zero(
    spell_book: SpellBook,
) -> None:
    """level 0 means "not known yet", not "level zero" — it must not produce a
    zero-length timer."""
    spell = spell_book.spell_by_name("Levitate")
    assert spell is not None
    assert match_closest_level(spell, PlayerClass.WARRIOR, 0, own_cast=True) == 39
    assert get_duration_seconds(spell, PlayerClass.WARRIOR, 0, own_cast=True) > 0


def test_observed_slow_still_matches_eqtools_answer(spell_book: SpellBook) -> None:
    """TestSlowForNecro again, explicitly against the bypass."""
    spell = spell_book.spell_by_name("Turgur's Insects")
    assert spell is not None
    seconds = get_duration_seconds(spell, PlayerClass.NECROMANCER, 60)
    assert abs(seconds / 60.0 - 6) < 0.2
