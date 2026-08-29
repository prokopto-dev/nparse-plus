"""Buff duration formulas — port of EQTool's Services/Spells/SpellDurations.cs.

``buffduration`` in spells_us.txt is in ticks (6 seconds each); the formula
field selects how the caster level scales it.
"""

from __future__ import annotations

import math

from nparseplus.core.enums import PlayerClass
from nparseplus.core.spells.models import Spell

TICK_SECONDS = 6


def match_closest_level(
    spell: Spell,
    player_class: PlayerClass | None,
    player_level: int | None,
) -> int:
    """Best-guess caster level for a spell (static MatchClosestLevelToSpell).

    A 1:1 port again, deliberately. #188 added an "item cast" branch here that
    replaced the player's level with the item's, on the theory that a clicky
    fires at the item's own level. That is NOT how Project 1999 works: an item
    effect is cast **as if you cast the spell yourself, at your own level**,
    and the ``at Level N`` a wiki item page prints is only the level at which
    you may begin clicking it. The spell's own formula and cap do the scaling.

    The spell data says so plainly. Levitate is duration formula 10,
    ``min(level * 3 + 10, 190)`` ticks, and its 190-tick cap is exactly
    ``60 * 3 + 10`` — EQ duration data is written so that max level reaches the
    cap. So the 19 minutes #188 reported as "wildly inflated" for a level-60
    clicker was the correct answer, and substituting the item's level cut it to
    10. Across the spell book that shortened 166 durations at level 60.

    See :mod:`nparseplus.core.spells.itemcasts`, which still owns the scraped
    numbers — as the click *requirement* they actually are.
    """
    if player_class is not None and player_level is not None:
        found = spell.class_levels.get(player_class)
        if found is not None:
            return found if player_level < found else player_level

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
) -> int:
    """Port of SpellDurations.GetDuration_inSeconds (returns whole seconds)."""
    duration = spell.buff_duration_ticks
    level = match_closest_level(spell, player_class, player_level)
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
        else float(get_duration_seconds(spell, player_class, player_level))
    )
    return seconds + delay_offset_ms / 1000.0


def npc_grace_seconds(spell: Spell, *, on_npc: bool) -> float:
    """The extra tick :data:`NPC_DETRIMENTAL_GRACE_S` describes, or zero.

    Shared with ``TimersService.respell_row`` so that correcting a guess
    produces the same countdown the matcher would have produced had it named
    that spell in the first place (#177).
    """
    return float(NPC_DETRIMENTAL_GRACE_S) if on_npc and spell.is_detrimental else 0.0
