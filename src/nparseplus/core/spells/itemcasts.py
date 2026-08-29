"""Effective caster level for a spell that came from an ITEM, not a spellbook.

An item's clicky effect is cast at the item's own level, which has nothing to
do with the level of the player holding it. The spell data carries no such
field, so the level has to be inferred or curated — this module is the one
place that decides, and :func:`nparseplus.core.spells.durations.match_closest_level`
is its only caller.

Two layers, cheapest first (#188):

1. Inference. Your class having no entry in a spell's class table means you
   cannot have cast it from a spellbook, so it came from an item. The spell's
   MINIMUM class level is the closest thing the data has to the item's caster
   level, and it is always a better answer than yours.
2. Curation, for the items where the inference is visibly wrong. ``_CURATED``
   is the hand-maintained half (the ``_EPIC_SPELLS`` pattern) and
   ``data/items/item_clickies.json`` is the generated half, written by
   ``tools/convert_item_clickies.py`` from the P99 wiki.

DELIBERATE DIVERGENCE from #188's suggested mechanism, which was to apply the
curated levels "at load" the way ``_apply_epic_fixup`` does. A load-time fixup
mutates ``Spell.class_levels``, and that table is read by far more than
durations: the spell MATCHER ranks candidates with it (so a fixup would move
``tests/fixtures/spell_match_baseline.json`` and could undo #182), and
``YouBeginCastingParser`` gates class detection on ``len(class_levels) == 1``,
so adding one entry silently disables detection for that spell. Consulting the
table only on the item-cast path instead makes the blast radius exactly the
bug being fixed: a class that CAN cast the spell never reaches this module.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from pathlib import Path

# Spell name -> the level its item effect is cast at. Hand-curated; entries
# here win over both the generated table and the inference, so this is where a
# wrong scrape gets corrected. Keep it small and say why each row exists.
_CURATED: dict[str, int] = {}


def _data_path() -> Path:
    return Path(str(resources.files("nparseplus") / "data" / "items" / "item_clickies.json"))


@lru_cache(maxsize=1)
def _generated(path: Path | None = None) -> dict[str, int]:
    """The converter's output, or an empty table when it has not been run.

    Absent by design rather than by accident: layers 1 and 2 of #188 ship
    without it, so a thin or missing scrape degrades to the inference instead
    of breaking the import.
    """
    target = path or _data_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    clickies = raw.get("clickies", {})
    return {name: int(level) for name, level in clickies.items() if int(level) > 0}


def item_cast_level(spell_name: str) -> int | None:
    """The curated item-cast level for ``spell_name``, or None to infer."""
    curated = _CURATED.get(spell_name)
    if curated is not None:
        return curated
    return _generated().get(spell_name)
