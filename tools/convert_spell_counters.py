#!/usr/bin/env python
"""Extract spell-count lists from EQTool's EQTool/Models/EQSpells.cs.

Produces src/nparseplus/data/spells_counters.json containing:
- spells_that_need_counts: spells whose casts EQTool tallies per target
  (SpellsThatNeedCounts)
- bard_spells_that_need_resists: bard AOE songs for which EQTool produces
  resist/hit count summaries (BardSpellsThatNeedResists)

Usage:
    .venv/bin/python tools/convert_spell_counters.py [path-to-eqtool-checkout]

The EQTool checkout path may also be given via the EQTOOL_SRC env var.
"""

import json
import os
import re
import sys
from pathlib import Path

SOURCE_FILE = "EQTool/Models/EQSpells.cs"
SOURCE_COMMIT = "fdd3f25a274defade4e6330c5b7724144a11000b"

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "src" / "nparseplus" / "data" / "spells_counters.json"


def extract_string_list(text: str, field_name: str) -> list[str]:
    """Parse `FieldName = new List<string>...{ "a", "b" };` into a Python list."""
    m = re.search(
        rf"{field_name}\s*=\s*new\s+List<string>\s*(?:\(\s*\))?\s*\{{(.*?)\}};",
        text,
        re.DOTALL,
    )
    if not m:
        raise ValueError(f"{field_name} list not found in {SOURCE_FILE}")
    return re.findall(r'"([^"]+)"', m.group(1))


def main() -> None:
    if len(sys.argv) > 1:
        eqtool_src = Path(sys.argv[1])
    elif os.environ.get("EQTOOL_SRC"):
        eqtool_src = Path(os.environ["EQTOOL_SRC"])
    else:
        sys.exit("usage: convert_spell_counters.py <path-to-eqtool-checkout>  (or set EQTOOL_SRC)")

    text = (eqtool_src / SOURCE_FILE).read_text(encoding="utf-8-sig")

    spells_that_need_counts = extract_string_list(text, "SpellsThatNeedCounts")
    bard_resists = extract_string_list(text, "BardSpellsThatNeedResists")

    assert spells_that_need_counts, "SpellsThatNeedCounts is empty"
    assert bard_resists, "BardSpellsThatNeedResists is empty"

    data = {
        "source": {"file": SOURCE_FILE, "commit": SOURCE_COMMIT},
        "meta": {
            "notes": (
                "spells_that_need_counts: spells whose casts are counted per "
                "target (EQSpells.SpellsThatNeedCounts). "
                "bard_spells_that_need_resists: bard AOE songs for which "
                "resist/hit summaries are produced "
                "(EQSpells.BardSpellsThatNeedResists)."
            )
        },
        "spells_that_need_counts": spells_that_need_counts,
        "bard_spells_that_need_resists": bard_resists,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    loaded = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    print(f"wrote {OUTPUT_PATH}")
    print(f"spells_that_need_counts: {len(loaded['spells_that_need_counts'])}")
    print(f"bard_spells_that_need_resists: {len(loaded['bard_spells_that_need_resists'])}")


if __name__ == "__main__":
    main()
