"""Auto-counter spell lists (EQSpells.SpellsThatNeedCounts and friends).

Loads ``nparseplus/data/spells_counters.json`` when present; otherwise falls
back to the lists hardcoded in EQTool's EQSpells.cs.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

logger = logging.getLogger(__name__)

# EQSpells.SpellsThatNeedCounts — casts tallied per target instead of timed.
DEFAULT_SPELLS_THAT_NEED_COUNTS: tuple[str, ...] = (
    "Mana Sieve",
    "LowerElement",
    "Concussion",
    "Flame Lick",
    "Jolt",
    "Cinder Jolt",
    "Rage of Vallon",
    "Waves of the Deep Sea",
    "Anarchy",
    "Breath of the Sea",
    "Frostbite",
    "Judgment of Ice",
    "Storm Strike",
    "Shrieking Howl",
    "Static Strike",
    "Rage of Zek",
    "Blinding Luminance",
    "Flash of Light",
)

# EQSpells.BardSpellsThatNeedResists — bard AOEs given resist/hit summaries.
DEFAULT_BARD_SPELLS_THAT_NEED_RESISTS: tuple[str, ...] = (
    "Chords of Dissonance",
    "Denon's Disruptive Discord",
    "Selo's Chords of Cessation",
    "Selo's Assonant Strane",
)


@dataclass(frozen=True)
class CounterLists:
    spells_that_need_counts: frozenset[str]
    bard_spells_that_need_resists: frozenset[str]

    def needs_count(self, spell_name: str) -> bool:
        return spell_name in self.spells_that_need_counts


_DEFAULTS = CounterLists(
    spells_that_need_counts=frozenset(DEFAULT_SPELLS_THAT_NEED_COUNTS),
    bard_spells_that_need_resists=frozenset(DEFAULT_BARD_SPELLS_THAT_NEED_RESISTS),
)


def load_counter_lists(path: Path | None = None) -> CounterLists:
    """Read spells_counters.json (packaged by default); fall back to defaults."""
    try:
        if path is not None:
            text = path.read_text(encoding="utf-8")
        else:
            text = resources.files("nparseplus.data").joinpath("spells_counters.json").read_text()
        data = json.loads(text)
    except (FileNotFoundError, ModuleNotFoundError, OSError, json.JSONDecodeError):
        logger.info("spells_counters.json not found or invalid; using built-in lists")
        return _DEFAULTS

    counts = data.get("spells_that_need_counts") or DEFAULT_SPELLS_THAT_NEED_COUNTS
    bard = data.get("bard_spells_that_need_resists") or DEFAULT_BARD_SPELLS_THAT_NEED_RESISTS
    return CounterLists(
        spells_that_need_counts=frozenset(counts),
        bard_spells_that_need_resists=frozenset(bard),
    )
