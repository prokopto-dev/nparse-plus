"""Cast-message matching helpers.

Ports the candidate-selection logic the EQTool parsers share:

- ``match_closest_level_to_spell`` — instance-side
  ``SpellDurations.MatchClosestLevelToSpell(List<Spell>, ...)``: pick the
  candidate whose class levels sit closest to the active player's level.
- ``possessive_message`` — the ``message.IndexOf("'")`` trick from
  SpellCastOnOtherParser: possessive cast texts ("Joe's skin ...",
  "Someone's image shimmers") match from the apostrophe on.
- ``iter_target_splits`` — the progressive space-walk (up to 5 words) that
  peels a target name off the front of a cast-on-other message.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence

from nparseplus.core.enums import PlayerClass
from nparseplus.core.spells.models import Spell

MAX_TARGET_WORDS = 5


def hide_spell(
    show_classes: Sequence[PlayerClass | int] | None,
    spell_classes: Mapping[PlayerClass, int],
) -> bool:
    """Class-filter predicate — exact port of SpellUIExtensions.HideSpell.

    Show (False) when the filter is None (EQTool's null = show all) or when
    the spell has no class table; otherwise show iff ANY selected class can
    cast the spell. Note the C# quirk kept on purpose: an EMPTY (non-null)
    selection hides every spell that has castable classes.
    """
    if show_classes is None or not spell_classes:
        return False
    selected = {PlayerClass(cls) for cls in show_classes}
    return not any(cls in spell_classes for cls in selected)


def match_closest_level_to_spell(
    spells: Sequence[Spell],
    player_class: PlayerClass | None,
    player_level: int | None,
) -> Spell | None:
    """Disambiguate same-message spell candidates by the player's level."""
    if not spells:
        return None

    if player_class is not None:
        level = player_level if player_level is not None else 0
        smallest_delta = level
        closest: Spell | None = None
        for spell in spells:
            for class_level in spell.class_levels.values():
                delta = abs(class_level - level)
                if delta < smallest_delta:
                    closest = spell
                    smallest_delta = delta
        if closest is not None:
            return closest

    for spell in spells:
        if any(0 < class_level <= 60 for class_level in spell.class_levels.values()):
            return spell

    return spells[0]


def possessive_message(message: str) -> str | None:
    """Message from the first apostrophe on ("Joe's hand ..." -> "'s hand ...")."""
    index = message.find("'")
    if index == -1:
        return None
    return message[index:].strip()


def iter_target_splits(
    message: str, max_words: int = MAX_TARGET_WORDS
) -> Iterator[tuple[str, str]]:
    """Yield (target_name, spell_message) pairs splitting at each of the first
    ``max_words`` spaces, mirroring SpellCastOnOtherParser.HandleBestGuessSpell."""
    index = 0
    for _ in range(max_words):
        if index > len(message):
            break
        index = message.find(" ", index + 1)
        if index == -1:
            break
        yield message[: index + 1].strip(), message[index:].strip()
