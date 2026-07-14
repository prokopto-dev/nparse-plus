"""Zone database — loader and queries over data/zones.json (Zones.cs port)."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from pathlib import Path

from pydantic import BaseModel, ConfigDict

DEFAULT_RESPAWN_SECONDS = 400  # 6:40, EQTool's global fallback


class NpcSpawnTime(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    seconds: int


class NpcAoe(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")

    name: str


class ZoneInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    respawn_seconds: int = DEFAULT_RESPAWN_SECONDS
    show_all_map_levels: bool = False
    zone_level_height: int | None = None
    notable_npcs: tuple[str, ...] = ()
    npc_spawn_times: tuple[NpcSpawnTime, ...] = ()
    npc_contains_spawn_times: tuple[NpcSpawnTime, ...] = ()
    npcs_that_aoe: tuple[NpcAoe, ...] = ()


class BoatInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    boat: str
    pretty_name: str
    start_announcement: str
    start_point: str
    end_point: str
    trip_time_in_seconds: float
    announcement_to_dock_in_seconds: float


class ZoneDatabase:
    def __init__(
        self,
        zones: dict[str, ZoneInfo],
        boats: list[BoatInfo],
        kael_faction_mobs: list[str],
        zone_name_mapper: dict[str, str],
        zone_who_mapper: dict[str, str],
    ) -> None:
        self.zones = zones
        self.boats = boats
        self.kael_faction_mobs = kael_faction_mobs
        self._name_to_short = zone_name_mapper  # long name (lower) -> short key
        self._who_to_name = zone_who_mapper  # /who name (lower) -> long name (lower)
        self._boats_by_announcement = {b.start_announcement: b for b in boats}

    def get(self, short_name: str) -> ZoneInfo | None:
        return self.zones.get(short_name.lower())

    def short_name(self, long_name: str) -> str | None:
        """'You have entered <long name>.' -> short zone key."""
        return self._name_to_short.get(long_name.strip().lower())

    def short_name_from_who(self, who_name: str) -> str | None:
        """'There are N players in <who name>.' -> short zone key."""
        who = who_name.strip().lower()
        long_name = self._who_to_name.get(who, who)
        return self._name_to_short.get(long_name)

    def spawn_time(self, npc_name: str, short_zone: str | None) -> int:
        """EQTool's ZoneSpawnTimes.GetSpawnTime lookup order: exact NPC match,
        substring match, zone default, global 6:40."""
        zone = self.zones.get((short_zone or "").lower())
        if zone is None:
            return DEFAULT_RESPAWN_SECONDS
        npc = npc_name.strip().lower()
        for entry in zone.npc_spawn_times:
            if entry.name.lower() == npc:
                return entry.seconds
        for entry in zone.npc_contains_spawn_times:
            if entry.name.lower() in npc:
                return entry.seconds
        return zone.respawn_seconds

    def boat_for_announcement(self, message: str) -> BoatInfo | None:
        return self._boats_by_announcement.get(message)


def _data_path() -> Path:
    return Path(str(resources.files("nparseplus") / "data" / "zones.json"))


@lru_cache(maxsize=1)
def load_zone_database(path: Path | None = None) -> ZoneDatabase:
    raw = json.loads((path or _data_path()).read_text())
    zones = {key: ZoneInfo(**value) for key, value in raw["zones"].items()}
    boats = [BoatInfo(**b) for b in raw["boats"]]
    aliases = raw.get("aliases", {})
    return ZoneDatabase(
        zones=zones,
        boats=boats,
        kael_faction_mobs=list(raw.get("kael_faction_mobs", [])),
        zone_name_mapper=aliases.get("zone_name_mapper", {}),
        zone_who_mapper=aliases.get("zone_who_mapper", {}),
    )
