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

import logging
from collections.abc import Iterator, Mapping, Sequence
from enum import Enum

from nparseplus.core.enums import PlayerClass
from nparseplus.core.spells.models import Spell

logger = logging.getLogger(__name__)

MAX_TARGET_WORDS = 5


class SpellMatchMode(Enum):
    """Who the player is to the cast being matched (#177).

    Many EQ spells share one cast message, so the message alone names a *list*
    of candidates. The player's own class is a strong signal for which one it
    was — but only when the player is part of the cast. Casting it themselves,
    or being its target, means the spell has to be one their class can produce
    or receive; watching a third party get it says nothing about the caster,
    and reading the observer's class as the caster's would invent a signal the
    log never carried.
    """

    #: The player casts it, or it lands on the player.
    PARTICIPANT = "participant"
    #: A third party casts it on a third party; the player only sees the line.
    BYSTANDER = "bystander"


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
    *,
    mode: SpellMatchMode = SpellMatchMode.BYSTANDER,
) -> Spell | None:
    """Disambiguate same-message spell candidates by the player's class/level.

    Rules in order, first non-empty wins:

    1. *(participant only)* castable by the player's class at or below their
       level — of those, the **highest** requirement: the best version they
       actually know. This is what separates Ultravision from See Invisible.
    2. *(participant only)* castable by the player's class at any level —
       closest to their level. Covers a buff from a higher-level caster of a
       class the player shares, and a spell they have not grown into yet.
    3. EQTool's rule: closest level across every class of every candidate.
    4. The first candidate.

    DELIBERATE DIVERGENCE (#177) from ``SpellDurations.MatchClosestLevelToSpell``
    (``EQTool/Services/Spells/SpellDurations.cs:71`` at ``d8e8084f``): the C#
    takes ``playerClass`` only to ask ``HasValue``, then scores every candidate
    against *every* class's level — so a spell the player's class cannot cast
    wins on some other class's number. Against this repo's own spells_us.txt
    that makes Pack Spirit (druid 39) beat a shaman's own Spirit of Wolf
    (shaman 9) for any level-40 shaman, swaps See Invisible with Ultravision,
    and picks the wrong Levitate.

    Rules 1-2 are the fix and are new. Rule 3 is the C# unchanged, which is why
    the EQtoolsTests-derived cases still resolve exactly as they did — including
    the cross-class answer a shadow knight gets for Symbol of Naltron. That is
    the whole reason the class rules are a layer ABOVE the C# rule rather than a
    filter in front of it: a blanket "must be castable by my class" would be
    right for the player's own buffs and wrong for everything they merely watch.

    REJECTED, so nobody re-proposes it: breaking a rule-3 tie toward the
    candidate with the lowest minimum class level ("the version most characters
    have"). It was measured against the bundled database and is a net loss — it
    never fires in participant mode at all, and in bystander mode it flips ~75
    message/level pairs toward the LOWER tier, including "you feel much better."
    from Superior Healing to Greater Healing for a level-54 character and "your
    eyes tingle." from Ultravision back to See Invisible at 34. It also does not
    rescue the case that motivated it: an observed Spirit of Wolf has no tie to
    break, because Pack Spirit wins rule 3 outright. An observed cast carries no
    signal about the caster, so the residual ambiguity is answered by letting
    the user correct the row (see ``other_matches``) rather than by a stronger
    guess.

    Every answer this function gives over the pinned corpus is recorded in
    ``tests/fixtures/spell_match_baseline.json`` (regenerate with
    ``tools/gen_spell_match_baseline.py``). Change anything here and the diff
    on that file is the change — which is how the numbers above were arrived
    at, and the check the absence of which is why #177 went unnoticed.
    """
    if not spells:
        return None

    level = player_level if player_level is not None else 0

    if mode is SpellMatchMode.PARTICIPANT and player_class is not None:
        castable = [
            (s, s.class_levels[player_class]) for s in spells if player_class in s.class_levels
        ]
        known = [pair for pair in castable if 0 < pair[1] <= level]
        # ``max``/``min`` keep the FIRST extreme they see, so a tie resolves to
        # the earliest candidate — the same way the C#'s strict ``<`` does.
        if known:
            return max(known, key=lambda pair: pair[1])[0]
        if castable:
            return min(castable, key=lambda pair: abs(pair[1] - level))[0]

    if player_class is not None:
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


def other_matches(spells: Sequence[Spell], chosen: Spell | None) -> list[Spell]:
    """The candidates ``match_closest_level_to_spell`` passed over (#177).

    Carried onto the timer row so the user can correct a guess from the
    Timers window. Deduplicated by name, because the same spell can appear in
    a candidate list twice under one name and the menu is a list of names.
    """
    seen = {chosen.name.casefold()} if chosen is not None else set()
    rest: list[Spell] = []
    for spell in spells:
        key = spell.name.casefold()
        if key in seen:
            continue
        seen.add(key)
        rest.append(spell)
    return rest


def log_candidates(what: str, message: str, spells: Sequence[Spell], chosen: Spell | None) -> None:
    """Debug-log an ambiguous guess and what it was chosen from (#177).

    A mis-guess is only reportable if the report can name the candidate list
    the guess came out of — the user's own spells_us.txt is not necessarily
    this repo's, so the candidates are the evidence. Guarded on the level so
    the string work stays off the driver thread's hot path when nobody is
    listening.
    """
    if len(spells) < 2 or not logger.isEnabledFor(logging.DEBUG):
        return
    logger.debug(
        "%s %r -> %s (from %d candidates: %s)",
        what,
        message,
        chosen.name if chosen is not None else "no match",
        len(spells),
        ", ".join(_describe(spell) for spell in spells),
    )


def _describe(spell: Spell) -> str:
    levels = ",".join(f"{cls.name}:{lvl}" for cls, lvl in spell.class_levels.items())
    return f"{spell.name}[{levels or 'classless'}]"


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
