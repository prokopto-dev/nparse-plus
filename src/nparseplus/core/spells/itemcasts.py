"""The minimum level at which an item's clicky effect may be used.

NOT a caster level, and the distinction is the whole point of this module.
On Project 1999 an item effect is cast **as if you cast the spell yourself, at
your own level** — the spell's own duration formula and cap do the scaling. The
``at Level N`` a P99 wiki item page prints:

    Effect: [[Gather Shadows]] (Any Slot/Can Equip, Casting Time: 5.0) at Level 20

is the level at which you may *begin* clicking the item, nothing more.

#188 read that number as the level the effect is cast at and PR #190 shipped it
in v2.28.2, substituting it for the player's level in
:func:`nparseplus.core.spells.durations.match_closest_level`. It was wrong and
is reverted: it shortened 166 durations at level 60, worst Spirit of Ox 45 min
-> 3 min. Levitate is the proof — duration formula 10 is
``min(level * 3 + 10, 190)`` ticks and the 190-tick cap is exactly
``60 * 3 + 10``, so EQ's own data is built for max level to reach the cap, and
the "19 minutes" #188 filed as the bug was the correct level-60 value.

So this table has NO caller in the duration path and must not grow one. It is
kept because the scrape is sound data for the question it actually answers —
"can I click this yet" — which nothing surfaces today. The generator
``tools/convert_item_clickies.py`` and its ``--check`` guard remain the only
way to change ``data/items/item_clickies.json`` (CLAUDE.md: never hand-edit
generated JSON).
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from pathlib import Path

# Spell name -> the minimum level to click an item carrying it. Hand-curated;
# entries here win over the generated table, so this is where a wrong scrape
# gets corrected. Keep it small and say why each row exists.
_CURATED: dict[str, int] = {}


def _data_path() -> Path:
    return Path(str(resources.files("nparseplus") / "data" / "items" / "item_clickies.json"))


@lru_cache(maxsize=1)
def _generated(path: Path | None = None) -> dict[str, int]:
    """The converter's output, or an empty table when it has not been run.

    Absent by design rather than by accident: a thin or missing scrape degrades
    to "unknown" instead of breaking the import.
    """
    target = path or _data_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    clickies = raw.get("clickies", {})
    return {name: int(level) for name, level in clickies.items() if int(level) > 0}


def minimum_click_level(spell_name: str) -> int | None:
    """Lowest level seen for clicking an item that casts ``spell_name``.

    ``None`` when no item page stated one. Where several items cast the same
    spell at different requirements the generator keeps the LOWEST, since the
    log names only the spell and cannot identify the item.
    """
    curated = _CURATED.get(spell_name)
    if curated is not None:
        return curated
    return _generated().get(spell_name)
