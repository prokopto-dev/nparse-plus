"""Pure helpers for the merchant-prices plugin (unit-testable without Qt)."""

from __future__ import annotations

import re

# Sellers separate items with these; "WTB" starts the buying half of a line.
_SEPARATORS = re.compile(r"\s*(?:\||,|/|;)\s*")

MAX_TRACKED_ITEMS = 40


def extract_wts_items(auction_content: str) -> list[str]:
    """Item names offered for sale in one auction body (may be empty).

    Takes a ``CommsEvent.content`` for an AUCTION channel line, e.g.
    ``WTS Words of Crippling Force | Words of Incarceration`` ->
    ``["Words of Crippling Force", "Words of Incarceration"]``. Anything
    after a WTB marker is ignored; price tags like ``100pp`` are stripped.
    """
    body = auction_content
    upper = body.upper()
    wts_at = upper.find("WTS")
    if wts_at == -1:
        return []
    body = body[wts_at + 3 :]
    wtb_at = body.upper().find("WTB")
    if wtb_at != -1:
        body = body[:wtb_at]
    items: list[str] = []
    for chunk in _SEPARATORS.split(body):
        name = _strip_price_tags(chunk).strip(" .!'\"")
        if len(name) >= 3 and not name.isdigit():
            items.append(name)
    return items


def _strip_price_tags(chunk: str) -> str:
    # "Words of Odus 150pp" / "x2" / "ea." tails
    chunk = re.sub(r"\b\d+(?:\.\d+)?\s*[kp]{1,2}p?\b", "", chunk, flags=re.IGNORECASE)
    chunk = re.sub(r"\bx\s*\d+\b", "", chunk, flags=re.IGNORECASE)
    chunk = re.sub(r"\bea\.?\b", "", chunk, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", chunk).strip()


def format_platinum(average: int) -> str:
    """PigParse averages are platinum ints; 0 means never seen."""
    if average <= 0:
        return "—"
    return f"{average:,}pp"


def merge_tracked(existing: list[str], new_items: list[str]) -> list[str]:
    """Case-insensitive de-dup, newest last, capped at MAX_TRACKED_ITEMS."""
    seen = {name.lower(): name for name in existing}
    for name in new_items:
        seen.setdefault(name.lower(), name)
    merged = list(seen.values())
    return merged[-MAX_TRACKED_ITEMS:]
