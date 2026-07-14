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
    spell: Spell, player_class: PlayerClass | None, player_level: int | None
) -> int:
    """Best-guess caster level for a spell (static MatchClosestLevelToSpell)."""
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
    spell: Spell, player_class: PlayerClass | None, player_level: int | None
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
