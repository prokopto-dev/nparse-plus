"""NPC / map-label search (Qt-free).

Builds a small in-memory index from the current zone's map POI labels plus
the zone database's notable NPCs and NPC spawn-time entries, and answers
"find NPC" queries — including cross-zone "where is X?" lookups.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from nparseplus.core.zones import ZoneDatabase, ZoneInfo

Location = tuple[float, float, float]
LabelEntry = tuple[str, float, float, float]

KIND_LABEL = "label"
KIND_NOTABLE = "notable"
KIND_ZONE_NOTABLE = "zone-notable"


@dataclass(frozen=True)
class SearchHit:
    kind: str  # "label" | "notable" | "zone-notable"
    name: str
    location: Location | None
    respawn_seconds: int | None
    zone_key: str | None = None
    zone_display: str | None = None


def normalize_name(text: str) -> str:
    """Normalize for matching: map labels use underscores instead of spaces."""
    return text.strip().lower().replace("_", " ")


def _notable_names(info: ZoneInfo) -> list[str]:
    """Notable NPC names for a zone, deduplicated case-insensitively."""
    names: list[str] = []
    seen: set[str] = set()
    for name in (*info.notable_npcs, *(entry.name for entry in info.npc_spawn_times)):
        key = name.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        names.append(name.strip())
    return names


def _rank_key(hit: SearchHit, query: str) -> tuple[int, int, str]:
    name = normalize_name(hit.name)
    starts = 0 if name.startswith(query) else 1
    kind_rank = 0 if hit.kind == KIND_LABEL else 1
    return (starts, kind_rank, name)


class NpcSearchIndex:
    """Search index for a single zone's map labels and notable NPCs."""

    def __init__(
        self,
        zone_key: str | None,
        labels: Iterable[LabelEntry] = (),
        zones: ZoneDatabase | None = None,
    ) -> None:
        self.zone_key = zone_key
        self._hits: list[SearchHit] = []

        clean_labels: list[LabelEntry] = [
            (str(text).strip(), float(x), float(y), float(z))
            for text, x, y, z in labels
            if str(text).strip()
        ]
        for text, x, y, z in clean_labels:
            respawn = zones.spawn_time(text, zone_key) if zones else None
            self._hits.append(SearchHit(KIND_LABEL, text, (x, y, z), respawn))

        info = zones.get(zone_key) if (zones and zone_key) else None
        if zones is not None and info is not None:
            for name in _notable_names(info):
                needle = normalize_name(name)
                location: Location | None = None
                for text, x, y, z in clean_labels:
                    if needle in normalize_name(text):
                        location = (x, y, z)
                        break
                self._hits.append(
                    SearchHit(KIND_NOTABLE, name, location, zones.spawn_time(name, zone_key))
                )

    def search(self, query: str) -> list[SearchHit]:
        """Case-insensitive substring search, startswith matches ranked first."""
        q = normalize_name(query)
        if not q:
            return []
        matches = [hit for hit in self._hits if q in normalize_name(hit.name)]
        matches.sort(key=lambda hit: _rank_key(hit, q))
        return matches

    def notables(self) -> list[SearchHit]:
        """All notable-NPC hits for the zone, sorted by name."""
        hits = [hit for hit in self._hits if hit.kind == KIND_NOTABLE]
        hits.sort(key=lambda hit: hit.name.lower())
        return hits


def _title_case(name: str) -> str:
    """Title-case words without str.title()'s apostrophe artifact."""
    return " ".join(word[:1].upper() + word[1:] for word in name.split())


def zone_display_names(zones: ZoneDatabase) -> dict[str, str]:
    """short zone key -> human-friendly display name (title-cased long name)."""
    canonical: dict[str, str] = {}
    for long_name, short in zones._name_to_short.items():
        current = canonical.get(short)
        if current is None or (len(long_name), long_name) < (len(current), current):
            canonical[short] = long_name
    return {short: _title_case(long_name) for short, long_name in canonical.items()}


def search_all_zones(query: str, zones: ZoneDatabase) -> list[SearchHit]:
    """Search notable NPCs across every zone ("where is X?")."""
    q = normalize_name(query)
    if not q:
        return []
    display = zone_display_names(zones)
    hits: list[SearchHit] = []
    for zone_key in sorted(zones.zones):
        info = zones.zones[zone_key]
        for name in _notable_names(info):
            if q in normalize_name(name):
                hits.append(
                    SearchHit(
                        KIND_ZONE_NOTABLE,
                        name,
                        None,
                        zones.spawn_time(name, zone_key),
                        zone_key=zone_key,
                        zone_display=display.get(zone_key, _title_case(zone_key)),
                    )
                )
    hits.sort(key=lambda hit: (*_rank_key(hit, q), hit.zone_key or ""))
    return hits
