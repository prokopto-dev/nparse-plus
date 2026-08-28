#!/usr/bin/env python
"""Record what the spell matcher resolves every ambiguous cast message to.

Produces tests/fixtures/spell_match_baseline.json — a differential guard over
`core/spells/matching.match_closest_level_to_spell`.

Many EQ spells share one cast message, so the message names a *list* and the
matcher picks one. That pick is a judgement call over 800-odd real candidate
lists, and #177 is what happens when nobody can see the whole surface: the
matcher ignored the player's class for years, and the argument for the fix —
and against the tiebreak originally proposed with it — could only be settled
by counting. This file is that count, committed, so the next change to that
function is MEASURED rather than argued. A diff here is not a failure; it is
the change, made reviewable.

Shape, per ambiguous message:

    "candidates"             every spell sharing the message, in list order
    "bystander"              level -> chosen name. NO class axis, because
                             bystander mode is class-INDEPENDENT by
                             construction (it is EQTool's rule, which reads
                             the level only). Encoding that as one row per
                             level rather than one per class*level is what
                             keeps this file small, and it also asserts the
                             invariant: if a change makes bystander read the
                             class, --check cannot express the result and
                             fails loudly.
    "participant_overrides"  "CLASS:LEVEL" -> chosen name, recorded ONLY where
                             participant mode disagrees with bystander at the
                             same level. That difference IS the class rule, so
                             this section is the fix's whole footprint: it was
                             empty before #177 and every entry in it is a case
                             the player's own class decided.

Messages the matcher answers identically everywhere are still listed, because
a change that makes a previously-stable message vary is exactly the kind of
regression this exists to surface.

Usage:
    uv run python tools/gen_spell_match_baseline.py [--check]

    --check exits non-zero if the committed file is stale (what
    tests/core/spells/test_match_baseline.py asserts).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from nparseplus.core.enums import PlayerClass  # noqa: E402
from nparseplus.core.spells.matching import (  # noqa: E402
    SpellMatchMode,
    match_closest_level_to_spell,
)
from nparseplus.core.spells.spells_us import SpellBook, load_spell_book  # noqa: E402

#: The pinned corpus, never the bundled data/ copy: this file has to be stable
#: across a spells_us.txt refresh, or every regeneration would be unreadable.
FIXTURE = REPO / "tests" / "fixtures" / "spells_us.txt"
BASELINE = REPO / "tests" / "fixtures" / "spell_match_baseline.json"

#: A spread rather than 1..60: the matcher's answer changes at class-level
#: boundaries, and five points spaced across the range hit every band a real
#: character passes through while keeping the file reviewable in a diff.
LEVELS = (1, 15, 30, 45, 60)


def _resolve(candidates, player_class, level, mode) -> str:
    found = match_closest_level_to_spell(candidates, player_class, level, mode=mode)
    return found.name if found is not None else ""


def build(book: SpellBook) -> dict:
    """Every ambiguous cast message in the book, keyed message -> record."""
    seen: dict[str, list] = {}
    for index in (book._cast_on_you_spells, book._cast_other_spells, book._you_cast_spells):
        for message, candidates in index.items():
            if len(candidates) > 1:
                seen.setdefault(message, candidates)

    out: dict[str, dict] = {}
    for message in sorted(seen):
        candidates = seen[message]
        bystander = {
            str(level): _resolve(candidates, PlayerClass.WARRIOR, level, SpellMatchMode.BYSTANDER)
            for level in LEVELS
        }
        # The class-independence claim above, enforced rather than trusted.
        for player_class in PlayerClass:
            for level in LEVELS:
                other = _resolve(candidates, player_class, level, SpellMatchMode.BYSTANDER)
                if other != bystander[str(level)]:
                    raise AssertionError(
                        f"bystander mode read the player's class: {message!r} "
                        f"{player_class.name} {level} -> {other!r}, "
                        f"expected {bystander[str(level)]!r}"
                    )

        overrides = {}
        for player_class in PlayerClass:
            for level in LEVELS:
                chosen = _resolve(candidates, player_class, level, SpellMatchMode.PARTICIPANT)
                if chosen != bystander[str(level)]:
                    overrides[f"{player_class.name}:{level}"] = chosen

        out[message] = {
            "candidates": [spell.name for spell in candidates],
            "bystander": bystander,
            "participant_overrides": overrides,
        }
    return out


def render(book: SpellBook) -> str:
    document = {
        "_comment": (
            "Generated by tools/gen_spell_match_baseline.py from "
            "tests/fixtures/spells_us.txt. Do not hand-edit; regenerate and "
            "review the diff. See the tool's docstring for the format."
        ),
        "levels": list(LEVELS),
        "messages": build(book),
    }
    return json.dumps(document, indent=1, sort_keys=False, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the committed file is stale")
    args = parser.parse_args(argv[1:])

    text = render(load_spell_book(FIXTURE))
    if args.check:
        if not BASELINE.exists():
            print(f"{BASELINE} is missing; run tools/gen_spell_match_baseline.py")
            return 1
        if BASELINE.read_text(encoding="utf-8") != text:
            print(
                f"{BASELINE.relative_to(REPO)} is stale.\n"
                "The spell matcher's answers changed. Regenerate with\n"
                "    uv run python tools/gen_spell_match_baseline.py\n"
                "and REVIEW THE DIFF: each line is a cast message whose "
                "resolution moved."
            )
            return 1
        print(f"{BASELINE.relative_to(REPO)} is up to date.")
        return 0

    BASELINE.write_text(text, encoding="utf-8")
    print(f"wrote {BASELINE.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
