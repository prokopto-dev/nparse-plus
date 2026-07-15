"""P99 wiki (wiki.project1999.com) NPC lookup.

The wiki is a MediaWiki whose NPC/merchant pages are structured templates::

    {{MerchantPage
    | name     = Boomba the Big
    | level    = 20
    | zone     = [[Freeport|West Freeport]]
    | location = (-24, -32)
    ...

``search`` uses the opensearch API; ``npc`` fetches the raw wikitext and
parses the template fields. Wiki ``location`` is in game (X, Y) order —
map-file/scene coordinates are ``(-y, -x)`` of that (calibrated against
NPCs that appear both on the wiki and as map labels, e.g. Lord Nagafen).

All failures degrade to ``None``/``[]``; never raises to callers.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

import httpx
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from nparseplus.core.zones import ZoneDatabase

logger = logging.getLogger(__name__)

BASE_URL = "https://wiki.project1999.com"
TIMEOUT_S = 6.0

_FIELD_RE = re.compile(r"^\|\s*(?P<key>\w+)\s*=\s*(?P<value>.*?)\s*$", re.MULTILINE)
_LINK_RE = re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]")
_LOC_RE = re.compile(r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)")


class WikiNpc(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str
    name: str
    race: str = ""
    npc_class: str = ""
    level: str = ""
    zone_display: str = ""
    zone_short: str | None = None
    # Game-coordinate (x, y) from the wiki page, if stated.
    location: tuple[float, float] | None = None
    description: str = ""
    url: str = ""

    @property
    def map_location(self) -> tuple[float, float] | None:
        """Scene/map-file coordinates: (-y, -x) of the game location."""
        if self.location is None:
            return None
        x, y = self.location
        return (-y, -x)


def strip_links(text: str) -> str:
    """[[A|B]] -> B, [[A]] -> A; drops surrounding wiki markup."""
    return _LINK_RE.sub(lambda m: m.group(1), text).strip()


def parse_template_fields(wikitext: str) -> dict[str, str]:
    return {m.group("key").lower(): m.group("value") for m in _FIELD_RE.finditer(wikitext)}


def parse_npc(title: str, wikitext: str, zones: ZoneDatabase | None = None) -> WikiNpc:
    fields = parse_template_fields(wikitext)
    zone_display = strip_links(fields.get("zone", ""))
    zone_short = zones.short_name(zone_display) if (zones and zone_display) else None
    loc_match = _LOC_RE.search(fields.get("location", ""))
    location = (float(loc_match.group(1)), float(loc_match.group(2))) if loc_match else None
    return WikiNpc(
        title=title,
        name=strip_links(fields.get("name", "")) or title,
        race=strip_links(fields.get("race", "")),
        npc_class=strip_links(fields.get("class", "")),
        level=strip_links(fields.get("level", "")),
        zone_display=zone_display,
        zone_short=zone_short,
        location=location,
        description=strip_links(fields.get("description", "")),
        url=f"{BASE_URL}/{title.replace(' ', '_')}",
    )


class P99WikiClient:
    def __init__(
        self,
        base_url: str = BASE_URL,
        zones: ZoneDatabase | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._zones = zones
        self._client = client or httpx.Client(
            timeout=TIMEOUT_S, headers={"User-Agent": "nparseplus"}, follow_redirects=True
        )
        self._search_cache: dict[str, list[str]] = {}
        self._npc_cache: dict[str, WikiNpc | None] = {}

    def search(self, query: str, limit: int = 8) -> list[str]:
        """Page titles matching the query (opensearch)."""
        key = f"{query.lower()}|{limit}"
        if key in self._search_cache:
            return self._search_cache[key]
        try:
            resp = self._client.get(
                f"{self._base}/api.php",
                params={
                    "action": "opensearch",
                    "search": query,
                    "limit": str(limit),
                    "format": "json",
                },
            )
            resp.raise_for_status()
            payload = json.loads(resp.text)
            titles = [t for t in payload[1] if isinstance(t, str)]
        except Exception:
            logger.warning("wiki search failed for %r", query, exc_info=True)
            return []
        self._search_cache[key] = titles
        return titles

    def npc(self, title: str) -> WikiNpc | None:
        """Fetch and parse one NPC page (None on failure/non-NPC pages)."""
        if title in self._npc_cache:
            return self._npc_cache[title]
        try:
            resp = self._client.get(
                f"{self._base}/index.php", params={"title": title, "action": "raw"}
            )
            resp.raise_for_status()
            result = parse_npc(title, resp.text, self._zones)
        except Exception:
            logger.warning("wiki page fetch failed for %r", title, exc_info=True)
            result = None
        self._npc_cache[title] = result
        return result

    def find_npcs(self, query: str, limit: int = 5) -> list[WikiNpc]:
        """Search + fetch in one call (worker-thread friendly)."""
        results = []
        for title in self.search(query, limit=limit):
            npc = self.npc(title)
            if npc is not None:
                results.append(npc)
        return results
