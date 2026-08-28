#!/usr/bin/env python
"""Scrape the P99 wiki for the level an item casts its clicky effect at.

Layer 3 of #188. Layers 1 and 2 infer an item's caster level from the spell's
minimum class level, which is always better than the clicker's level but is
still a guess. The P99 wiki states the real number on the ITEM page:

    Effect: [[Gather Shadows]] (Any Slot/Can Equip, Casting Time: 5.0) at Level 20

Output is ``src/nparseplus/data/items/item_clickies.json``, committed like
every other generated asset and read by ``core/spells/itemcasts.py``.

Usage:
    uv run python tools/convert_item_clickies.py --refresh   # scrape and write
    uv run python tools/convert_item_clickies.py --check     # validate, no network

``--check`` DELIBERATELY DOES NOT RE-SCRAPE, which is where this converter
differs from convert_zones.py and gen_registry_schema.py. Those derive from
something in the tree (a C# checkout, live pydantic models), so regenerating
under --check is free and exact. This one derives from a third-party website
over ~130 HTTP requests, so a --check that re-scraped would put CI traffic on
a community wiki on every run and would fail for edits made on the wiki rather
than in this repo. --check therefore guards what a local check honestly can:
that the committed file parses, that its levels are sane, and that every spell
it names still exists in the bundled spells_us.txt — which is what catches the
file rotting against a spell-data regeneration.

The scrape drives from the SPELL side, not the item side. Walking
data/items/master_item_list.txt would be 26,691 items = 534 batched requests;
each spell page carries an ``items_with_effect`` list, so 4,779 spells = 96
requests narrows it to the few hundred items that actually cast something we
know about. Pages are fetched 50 at a time (the MediaWiki API cap) with a
delay between batches.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from nparseplus.core.spells.spells_us import load_spell_book  # noqa: E402
from nparseplus.net.p99wiki import BASE_URL, strip_html  # noqa: E402

OUTPUT_PATH = REPO_ROOT / "src" / "nparseplus" / "data" / "items" / "item_clickies.json"
#: The bundled database (repo root ``data/``), the same copy composition.py
#: falls back to when no EQ install is configured.
SPELLS_PATH = REPO_ROOT / "data" / "spells" / "spells_us.txt"

BATCH_SIZE = 50  # the MediaWiki API's titles cap
REQUEST_DELAY_S = 0.4  # courtesy to a community wiki
TIMEOUT_S = 30.0
MAX_LEVEL = 65

#: "Effect: <link or plain name> (...) at Level 20"
_EFFECT_RE = re.compile(
    r"Effect:\s*(?P<name>.+?)\s*\((?P<slot>[^)]*)\)\s*(?:at\s*Level\s*(?P<level>\d{1,2}))?",
    re.IGNORECASE,
)
#: items_with_effect = <ul><li> {{:Item}} </li>...  (up to the next | field)
_ITEMS_FIELD_RE = re.compile(
    r"items_with_effect\s*=\s*(?P<body>.*?)(?=\n\s*\|\s*\w+\s*=|\Z)", re.DOTALL
)
_TRANSCLUDE_RE = re.compile(r"\{\{:\s*(?P<name>[^}|]+?)\s*\}\}")
_PIPED_RE = re.compile(r"\[\[\s*(?P<name>[^\]|]+?)\s*\|[^\]]*\]\]")
_PLAIN_RE = re.compile(r"\[\[\s*(?P<name>[^\]|]+?)\s*\]\]")


def _norm(name: str) -> str:
    """Match a wiki page title to a spells_us.txt name.

    The two disagree on spacing for a handful of spells — the wiki page is
    "Lower Element" where the data says "LowerElement" — so the comparison
    folds case and drops spaces, apostrophes and backticks.
    """
    return re.sub(r"[\s'`]+", "", name).casefold()


def _effect_spell_name(text: str) -> str | None:
    """The spell a wiki Effect line names, preferring the PAGE over the label."""
    for pattern in (_TRANSCLUDE_RE, _PIPED_RE, _PLAIN_RE):
        hit = pattern.search(text)
        if hit is not None:
            return hit.group("name").strip()
    plain = strip_html(text).strip()
    return plain or None


def _item_names(wikitext: str) -> list[str]:
    hit = _ITEMS_FIELD_RE.search(wikitext)
    if hit is None:
        return []
    body = hit.group("body")
    names = [m.group("name").strip() for m in _TRANSCLUDE_RE.finditer(body)]
    names += [m.group("name").strip() for m in _PIPED_RE.finditer(body)]
    names += [m.group("name").strip() for m in _PLAIN_RE.finditer(body)]
    return [n for n in names if n]


def _fetch_pages(client, titles: list[str]) -> dict[str, str]:
    """title -> wikitext, for up to BATCH_SIZE titles in one request."""
    resp = client.get(
        f"{BASE_URL}/api.php",
        params={
            "action": "query",
            "prop": "revisions",
            "rvprop": "content",
            "titles": "|".join(titles),
            "redirects": "1",
            "format": "json",
        },
    )
    resp.raise_for_status()
    pages = resp.json().get("query", {}).get("pages", {})
    out: dict[str, str] = {}
    for page in pages.values():
        revisions = page.get("revisions")
        if revisions:
            out[page["title"]] = revisions[0]["*"]
    return out


def _walk(client, titles: list[str], label: str) -> dict[str, str]:
    found: dict[str, str] = {}
    total = (len(titles) + BATCH_SIZE - 1) // BATCH_SIZE
    for index in range(0, len(titles), BATCH_SIZE):
        batch = titles[index : index + BATCH_SIZE]
        try:
            found.update(_fetch_pages(client, batch))
        except Exception as exc:  # a dead batch must not lose the whole run
            print(f"  ! {label} batch {index // BATCH_SIZE + 1} failed: {exc}", file=sys.stderr)
        done = index // BATCH_SIZE + 1
        print(f"  {label}: batch {done}/{total} ({len(found)} pages)", end="\r", file=sys.stderr)
        time.sleep(REQUEST_DELAY_S)
    print(file=sys.stderr)
    return found


def scrape(limit: int | None = None) -> dict:
    import httpx

    from nparseplus.net.tls import verify_option

    book = load_spell_book(SPELLS_PATH)
    known = {_norm(s.name): s.name for s in book.spells if s.name}
    spell_titles = sorted({s.name for s in book.spells if s.name})
    if limit is not None:
        spell_titles = spell_titles[:limit]

    client = httpx.Client(
        timeout=TIMEOUT_S,
        headers={"User-Agent": "nparseplus-item-clickies (+https://github.com/prokopto-dev)"},
        follow_redirects=True,
        verify=verify_option(),
    )
    with client:
        print(f"scanning {len(spell_titles)} spell pages for item sources", file=sys.stderr)
        spell_pages = _walk(client, spell_titles, "spells")

        items: set[str] = set()
        for wikitext in spell_pages.values():
            items.update(_item_names(wikitext))
        item_titles = sorted(items)
        print(f"{len(item_titles)} distinct items cast a spell we know", file=sys.stderr)
        item_pages = _walk(client, item_titles, "items")

    # spell -> level -> the items observed casting it at that level
    observed: dict[str, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    no_level = 0
    for title, wikitext in sorted(item_pages.items()):
        for line in wikitext.splitlines():
            hit = _EFFECT_RE.search(line)
            if hit is None:
                continue
            raw_name = _effect_spell_name(hit.group("name"))
            if not raw_name:
                continue
            spell = known.get(_norm(raw_name))
            if spell is None:
                continue
            if hit.group("level") is None:
                no_level += 1
                continue
            level = int(hit.group("level"))
            if 0 < level <= MAX_LEVEL and title not in observed[spell][level]:
                observed[spell][level].append(title)

    clickies: dict[str, int] = {}
    sources: dict[str, dict] = {}
    ambiguous = 0
    for spell, by_level in sorted(observed.items()):
        levels = sorted(by_level)
        if len(levels) > 1:
            ambiguous += 1
        # The lowest observed level wins. Several items can cast one spell at
        # different levels and the log names only the spell, so the item cannot
        # be identified — and a timer that expires early makes a player rebuff,
        # while one that runs long tells them they are still buffed when they
        # are not. `levels` keeps the spread so the choice stays auditable.
        clickies[spell] = levels[0]
        sources[spell] = {
            "levels": levels,
            "items": sorted({i for items in by_level.values() for i in items}),
        }

    return {
        "meta": {
            "source": BASE_URL,
            "generated_by": "tools/convert_item_clickies.py",
            "spell_pages_found": len(spell_pages),
            "item_pages_found": len(item_pages),
            "effects_without_a_level": no_level,
            "spells_with_a_level": len(clickies),
            "spells_with_conflicting_levels": ambiguous,
        },
        "clickies": clickies,
        "sources": sources,
    }


def validate(document: dict) -> list[str]:
    """Local integrity checks — everything --check can honestly assert."""
    problems: list[str] = []
    clickies = document.get("clickies")
    sources = document.get("sources", {})
    if not isinstance(clickies, dict) or not clickies:
        return ["'clickies' is missing or empty"]

    book = load_spell_book(SPELLS_PATH)
    known = {s.name for s in book.spells if s.name}
    for spell, level in sorted(clickies.items()):
        if not isinstance(level, int) or not (0 < level <= MAX_LEVEL):
            problems.append(f"{spell!r}: level {level!r} is out of range")
        if spell not in known:
            problems.append(f"{spell!r}: no longer in spells_us.txt")
        entry = sources.get(spell)
        if entry is None:
            problems.append(f"{spell!r}: no sources entry")
        elif level != min(entry.get("levels", [level])):
            problems.append(f"{spell!r}: level {level} is not the lowest observed")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="scrape the wiki and rewrite")
    parser.add_argument("--check", action="store_true", help="validate the committed file")
    parser.add_argument("--limit", type=int, help="scan only the first N spells (development)")
    args = parser.parse_args()

    if args.check:
        if not OUTPUT_PATH.exists():
            sys.exit(f"{OUTPUT_PATH.relative_to(REPO_ROOT)} is missing; run --refresh")
        try:
            document = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
        except ValueError as exc:
            sys.exit(f"{OUTPUT_PATH.relative_to(REPO_ROOT)} is not valid JSON: {exc}")
        problems = validate(document)
        if problems:
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            sys.exit(f"{len(problems)} problem(s) in {OUTPUT_PATH.relative_to(REPO_ROOT)}")
        print(
            f"{OUTPUT_PATH.relative_to(REPO_ROOT)} is valid ({len(document['clickies'])} spells)."
        )
        return

    if not args.refresh:
        parser.error("pass --refresh to scrape, or --check to validate the committed file")

    document = scrape(limit=args.limit)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    meta = document["meta"]
    print(f"wrote {OUTPUT_PATH.relative_to(REPO_ROOT)}")
    for key, value in meta.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
