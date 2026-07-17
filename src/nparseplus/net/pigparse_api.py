"""PigParse REST API client (port of EQTool/Services/PigParseApi.cs).

Same shape as :mod:`nparseplus.net.p99wiki`: sync httpx with an injectable
client, and every failure degrades to ``None``/``[]``/no-op with a warning —
network trouble must never take down parsing. Per the M3 brief this client
is stricter than the C# (which uses a default 100 s HttpClient timeout and
no retry): 5 s timeout, one retry.

Callers run on a worker thread (``net.worker``), never the driver thread.
Path-parameter server names are the C# ``Servers`` enum member name
("Green"), which ASP.NET route binding parses case-insensitively.
"""

from __future__ import annotations

import logging

import httpx
from pydantic import TypeAdapter

from nparseplus.core.enums import Server
from nparseplus.net.pigparse_models import (
    BoatActivity,
    ItemPrice,
    RollTimer,
    WirePlayerRecord,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://pigparse.azurewebsites.net"
TIMEOUT_S = 5.0
ATTEMPTS = 2  # initial call + one retry

_ITEMS = TypeAdapter(list[ItemPrice])
_PLAYERS = TypeAdapter(list[WirePlayerRecord])
_BOATS = TypeAdapter(list[BoatActivity])
_ROLL_TIMERS = TypeAdapter(list[RollTimer])


def server_route_name(server: int) -> str:
    """Wire int -> the enum-name path segment EQTool puts in URLs ("Green")."""
    return Server(server).name.title()


class PigParseApiClient:
    def __init__(self, base_url: str = BASE_URL, client: httpx.Client | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._client = client or httpx.Client(
            timeout=TIMEOUT_S, headers={"User-Agent": "nparseplus"}
        )

    def _request(
        self, method: str, path: str, json_body: object | None = None
    ) -> httpx.Response | None:
        url = f"{self._base}/{path}"
        for attempt in range(1, ATTEMPTS + 1):
            try:
                resp = self._client.request(method, url, json=json_body)
                resp.raise_for_status()
                return resp
            except Exception:
                if attempt == ATTEMPTS:
                    logger.warning("pigparse %s %s failed", method, path, exc_info=True)
        return None

    def _parse_list(self, adapter: TypeAdapter, resp: httpx.Response | None, what: str) -> list:
        if resp is None:
            return []
        try:
            return adapter.validate_json(resp.content)
        except Exception:
            logger.warning("pigparse %s bad payload", what, exc_info=True)
            return []

    # --- item pricing / wiki --------------------------------------------------

    def item_prices(self, server: int, names: list[str]) -> list[ItemPrice]:
        """POST api/item/postmultiple — auction price stats for item names."""
        if not names:
            return []
        resp = self._request(
            "POST", "api/item/postmultiple", {"Server": server, "Itemnames": names}
        )
        return self._parse_list(_ITEMS, resp, "item_prices")

    def item_wiki(self, name: str, zone: str) -> str | None:
        """POST api/item/wiki — raw P99 wiki markup for an item/NPC name."""
        resp = self._request("POST", "api/item/wiki", {"Name": name, "Zone": zone})
        return resp.text if resp is not None else None

    # --- players ---------------------------------------------------------------

    def players_by_names(self, names: list[str], server: int) -> list[WirePlayerRecord]:
        """POST api/player/getbynames — known guild/class/level for names."""
        if not names:
            return []
        resp = self._request("POST", "api/player/getbynames", {"Players": names, "Server": server})
        return self._parse_list(_PLAYERS, resp, "players_by_names")

    def upsert_players(self, players: list, server: int) -> None:
        """POST api/player/upsertplayers — share newly-learned player facts.

        Accepts WirePlayerRecord or any core-side object with name /
        guild_name / player_class / level attributes (core never imports
        wire models)."""
        if not players:
            return
        records = [
            p
            if isinstance(p, WirePlayerRecord)
            else WirePlayerRecord(
                name=p.name,
                guild_name=p.guild_name or None,
                server=server,
                player_class=int(p.player_class) if p.player_class is not None else None,
                level=p.level,
            )
            for p in players
        ]
        self._request(
            "POST",
            "api/player/upsertplayers",
            {"Players": [r.wire_dump() for r in records], "Server": server},
        )

    # --- zone activity ----------------------------------------------------------

    def send_npc_activity(
        self,
        *,
        name: str,
        zone: str,
        server: int,
        is_death: bool = False,
        is_engaged: bool = False,
        loc_x: float | None = None,
        loc_y: float | None = None,
    ) -> None:
        """POST api/zone/npcactivity — Scout Charisa / Kromzek / Kael tracking.

        The name allow-list lives in the calling handler (it needs
        ZoneDatabase's Kael faction mobs); the C# keeps it inside
        PigParseApi.SendNPCActivity instead.
        """
        self._request(
            "POST",
            "api/zone/npcactivity",
            {
                "NPCData": {"Name": name, "Zone": zone, "LocX": loc_x, "LocY": loc_y},
                "IsDeath": is_death,
                "IsEngaged": is_engaged,
                "Server": server,
            },
        )

    def send_quake(self, server: int) -> None:
        """GET api/zone/quakev2/{server} — report an earthquake (server dedupes)."""
        self._request("GET", f"api/zone/quakev2/{server_route_name(server)}")

    # --- boats -------------------------------------------------------------------

    def boat_seen(self, *, start_point: str, boat: int, server: int) -> None:
        """POST api/boat/seen — share a boat departure sighting."""
        self._request(
            "POST",
            "api/boat/seen",
            {"StartPoint": start_point, "Boat": boat, "Server": server},
        )

    def boat_activity(self, server: int) -> list[BoatActivity]:
        """GET api/boat/serverActivity/{server} — everyone's recent sightings."""
        resp = self._request("GET", f"api/boat/serverActivity/{server_route_name(server)}")
        return self._parse_list(_BOATS, resp, "boat_activity")

    # --- roll timers ---------------------------------------------------------------

    def roll_timers(self, server: int) -> list[RollTimer]:
        """GET api/rolltimer/timers/{server} — shared quake/scout roll state."""
        resp = self._request("GET", f"api/rolltimer/timers/{server_route_name(server)}")
        return self._parse_list(_ROLL_TIMERS, resp, "roll_timers")
