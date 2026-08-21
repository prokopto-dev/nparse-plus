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

    def get(self, short_name: str) -> ZoneInfo | None:
        return self.zones.get(short_name.lower())

    def short_name(self, long_name: str) -> str | None:
        """'You have entered <long name>.' -> short zone key."""
        return self._name_to_short.get(long_name.strip().lower())

    def long_names(self) -> list[str]:
        """Every known long zone name (the zone-name mapper's keys)."""
        return list(self._name_to_short)

    def long_name(self, short_name: str) -> str | None:
        """Short zone key -> a long display name (first mapper entry).

        The legacy nparse sharing protocol keys zones by the long name the
        maps window shows; this is the inverse of ``short_name``.
        """
        short = short_name.strip().lower()
        for long_form, mapped in self._name_to_short.items():
            if mapped == short:
                return long_form
        return None

    def short_name_from_who(self, who_name: str) -> str | None:
        """'There are N players in <who name>.' -> short zone key."""
        who = who_name.strip().lower()
        long_name = self._who_to_name.get(who, who)
        return self._name_to_short.get(long_name)

    def is_notable_kill(self, victim: str, short_zone: str | None) -> bool:
        """Whether this kill is one EQTool copies the fight parse for (#78).

        Port of ``UI/DPSMeter.xaml.cs LogParser_DeathEvent``: the victim is in
        the current zone's ``NotableNPCs`` **and not** in the global
        ``KaelFactionMobs`` list. That second clause is why the predicate is
        not simply "is this notable" — Kael's faction-grind giants are listed
        as notable so the map and timers treat them properly, but they are
        killed by the hundred, and auto-copying each one would overwrite the
        clipboard all evening.

        DEVIATION from the C#, which compares with ``==``: matching is
        casefolded here, the way every other name lookup in this class already
        is (``spawn_time``, ``short_name``, ``short_name_from_who``). The
        slain line does not reliably reproduce the article capitalization the
        zone data carries, and a raid target that silently fails to copy is
        indistinguishable from the feature not working.
        """
        name = victim.strip().casefold()
        if not name:
            return False
        zone = self.zones.get((short_zone or "").lower())
        if zone is None:
            return False
        if not any(notable.casefold() == name for notable in zone.notable_npcs):
            return False
        return not any(mob.casefold() == name for mob in self.kael_faction_mobs)

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
