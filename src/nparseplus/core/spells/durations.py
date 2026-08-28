"""Buff duration formulas — port of EQTool's Services/Spells/SpellDurations.cs.

``buffduration`` in spells_us.txt is in ticks (6 seconds each); the formula
field selects how the caster level scales it.
"""

from __future__ import annotations

import math

from nparseplus.core.enums import PlayerClass
from nparseplus.core.spells.itemcasts import item_cast_level
from nparseplus.core.spells.models import Spell

TICK_SECONDS = 6


def match_closest_level(
    spell: Spell,
    player_class: PlayerClass | None,
    player_level: int | None,
    *,
    own_cast: bool = False,
) -> int:
    """Best-guess caster level for a spell (static MatchClosestLevelToSpell).

    ``own_cast`` says the PLAYER is the caster, which is what unlocks the
    item-cast inference below. It defaults False so every observer path keeps
    EQTool's behaviour exactly.
    """
    if player_class is not None and player_level is not None:
        found = spell.class_levels.get(player_class)
        if found is not None:
            return found if player_level < found else player_level

    # The item-cast path (#188). A known class with no entry in this spell's
    # class table cannot have cast it from a spellbook, so it came from an item
    # — and an item's effect is cast at the ITEM's level, not the level of
    # whoever clicked it. EQTool has no such branch: it falls through below and
    # returns max(your level, some other class's level), which is why a level-60
    # character clicking a low-level item got a duration computed as though they
    # had cast it themselves. See core.spells.itemcasts for the two layers.
    #
    # Narrow on purpose, and the narrowness IS the fix. Three conditions, each
    # load-bearing: the cast must be the PLAYER's (a spell another player cast
    # on you or on a mob is better estimated by THEIR class's level, which is
    # what EQtoolsTests' TestSlowForNecro pins — a necro watching a shaman slow
    # a mob reads 6 min, not the shaman's minimum); the class must be KNOWN
    # (unknown is indistinguishable from an item cast); and the class must be
    # ABSENT from the table (a class that CAN cast the spell already returned
    # from the branch above, so self-casts cannot reach here at all).
    #
    # Known gap, stated rather than papered over: an INSTANT clicky with no
    # cast time and no "begins to glow" line prints only its effect message,
    # which arrives as SpellCastOnYouEvent and is indistinguishable from
    # another player buffing you. That path deliberately stays own_cast=False.
    if (
        own_cast
        and player_class is not None
        and spell.class_levels
        and player_class not in spell.class_levels
    ):
        curated = item_cast_level(spell.name)
        if curated is not None:
            return curated
        return min(spell.class_levels.values())

    if player_level is not None:
        # C# returns on the first (highest-level) class entry.
        for _cls, class_level in sorted(spell.class_levels.items(), key=lambda kv: -kv[1]):
            return class_level if player_level < class_level else player_level

    level: int | None = next(iter(spell.class_levels.values()), None)
    if (level is None or level <= 0) and player_level is not None:
        level = player_level
    if level is None or level <= 0:
        level = 30
    return level


def get_duration_seconds(
    spell: Spell,
    player_class: PlayerClass | None,
    player_level: int | None,
    *,
    own_cast: bool = False,
) -> int:
    """Port of SpellDurations.GetDuration_inSeconds (returns whole seconds)."""
    duration = spell.buff_duration_ticks
    level = match_closest_level(spell, player_class, player_level, own_cast=own_cast)
    formula = spell.buff_duration_formula

    if formula == 0:
        ticks = 0
    elif formula in (1, 6):
        ticks = min(math.ceil(level / 2.0), duration)
    elif formula == 2:
        ticks = min(math.ceil(level / 5.0 * 3), duration)
    elif formula == 3:
        ticks = min(level * 30, duration)
    elif formula == 4:
        ticks = 50 if duration == 0 else duration
    elif formula == 5:
        ticks = duration if duration != 0 else 3
    elif formula == 7:
        ticks = min(level, duration)
    elif formula == 8:
        ticks = min(level + 10, duration)
    elif formula == 9:
        ticks = min((level * 2) + 10, duration)
    elif formula == 10:
        ticks = min((level * 3) + 10, duration)
    elif formula in (11, 12, 15):
        ticks = duration
    elif formula == 50:
        ticks = 72000
    elif formula == 3600:
        ticks = 3600 if duration == 0 else duration
    else:
        ticks = duration

    return ticks * TICK_SECONDS


# Disciplines whose on-screen duration is a flat number rather than the
# spells_us.txt formula's answer. Transcribed literally from
# SpellHandlerService.Handle; five of the eight disagree with the formula, so
# this is not a redundant table (Puretone runs 240s where the formula says 120).
DISCIPLINE_DURATION_OVERRIDES_S: dict[str, int] = {
    "Voiddance Discipline": 8,
    "Weapon Shield Discipline": 20,
    "Deftdance Discipline": 15,
    "Furious Discipline": 9,
    "Defensive Discipline": 180,
    "Evasive Discipline": 180,
    "Nimble Discipline": 12,
    "Puretone Discipline": 240,
}

# One extra tick on a detrimental row whose target is an NPC, so the row
# outlives the "spell has worn off" line that ends it.
NPC_DETRIMENTAL_GRACE_S = 6


def base_timer_duration_seconds(
    spell: Spell,
    player_class: PlayerClass | None,
    player_level: int | None,
    delay_offset_ms: int = 0,
    *,
    own_cast: bool = False,
) -> float:
    """Seconds a Timers-window row counts down for ``spell``, before the NPC
    grace tick.

    The discipline override wins over the formula where there is one. Split
    from :func:`npc_grace_seconds` because the caller has to gate on this
    number: a spell whose duration works out to zero creates no row at all,
    and the grace tick would push it over that line (#177).
    """
    override = DISCIPLINE_DURATION_OVERRIDES_S.get(spell.name)
    seconds = (
        float(override)
        if override is not None
        else float(get_duration_seconds(spell, player_class, player_level, own_cast=own_cast))
    )
    return seconds + delay_offset_ms / 1000.0


def npc_grace_seconds(spell: Spell, *, on_npc: bool) -> float:
    """The extra tick :data:`NPC_DETRIMENTAL_GRACE_S` describes, or zero.

    Shared with ``TimersService.respell_row`` so that correcting a guess
    produces the same countdown the matcher would have produced had it named
    that spell in the first place (#177).
    """
    return float(NPC_DETRIMENTAL_GRACE_S) if on_npc and spell.is_detrimental else 0.0
