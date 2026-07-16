"""hide_spell — exact port of SpellUIExtensions.HideSpell (truth table)."""

from nparseplus.core.enums import PlayerClass
from nparseplus.core.spells.matching import hide_spell

DRUID_SPELL = {PlayerClass.DRUID: 24, PlayerClass.RANGER: 30}
CLERIC_SPELL = {PlayerClass.CLERIC: 44}


def test_none_filter_never_hides() -> None:
    assert hide_spell(None, DRUID_SPELL) is False
    assert hide_spell(None, {}) is False


def test_empty_spell_classes_never_hidden() -> None:
    assert hide_spell([PlayerClass.CLERIC], {}) is False


def test_any_selected_class_castable_shows() -> None:
    assert hide_spell([PlayerClass.DRUID], DRUID_SPELL) is False
    assert hide_spell([PlayerClass.RANGER, PlayerClass.WIZARD], DRUID_SPELL) is False


def test_no_selected_class_castable_hides() -> None:
    assert hide_spell([PlayerClass.CLERIC], DRUID_SPELL) is True
    assert hide_spell([PlayerClass.DRUID], CLERIC_SPELL) is True


def test_empty_selection_hides_castables_csharp_quirk() -> None:
    # C# checks only `== null`; an empty (non-null) list hides everything
    # that has castable classes.
    assert hide_spell([], DRUID_SPELL) is True
    assert hide_spell([], {}) is False


def test_wire_ints_accepted() -> None:
    assert hide_spell([int(PlayerClass.DRUID)], DRUID_SPELL) is False
    assert hide_spell([int(PlayerClass.CLERIC)], DRUID_SPELL) is True
