"""Offline tests for the PigParse REST client (MockTransport, no network)."""

import json

import httpx

from nparseplus.net.pigparse_api import PigParseApiClient, server_route_name
from nparseplus.net.pigparse_models import WirePlayerRecord


def _client(handler, sleeps: list[float] | None = None) -> PigParseApiClient:
    return PigParseApiClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=(sleeps.append if sleeps is not None else lambda seconds: None),
    )


def test_server_route_name() -> None:
    assert server_route_name(0) == "Green"
    assert server_route_name(1) == "Blue"
    assert server_route_name(3) == "Quarm"


def test_item_prices_round_trip() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        # Server responds camelCase (ASP.NET System.Text.Json default).
        return httpx.Response(
            200,
            json=[{"itemName": "Rusty Sword", "totalWTSLast30DaysAverage": 12}],
        )

    items = _client(handler).item_prices(0, ["Rusty Sword"])
    assert items[0].item_name == "Rusty Sword"
    assert items[0].total_wts_last_30_days_average == 12
    body = json.loads(seen[0].content)
    assert body == {"Server": 0, "Itemnames": ["Rusty Sword"]}  # PascalCase out
    assert seen[0].url.path == "/api/item/postmultiple"


def test_item_prices_empty_names_skips_network() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    assert _client(handler).item_prices(0, []) == []


def test_item_wiki_returns_raw_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == {"Name": "Lord Nagafen", "Zone": "soldungb"}
        return httpx.Response(200, text="{{NPCPage | name = Lord Nagafen}}")

    assert "Lord Nagafen" in _client(handler).item_wiki("Lord Nagafen", "soldungb")


def test_players_by_names_and_upsert() -> None:
    posts: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posts.append((request.url.path, json.loads(request.content)))
        if request.url.path.endswith("getbynames"):
            return httpx.Response(
                200,
                json=[{"name": "Soandso", "guildName": "Bregan D'Aerth", "playerClass": 1}],
            )
        return httpx.Response(200)

    client = _client(handler)
    players = client.players_by_names(["Soandso"], 0)
    assert players[0].guild_name == "Bregan D'Aerth"
    assert players[0].player_class == 1

    client.upsert_players([WirePlayerRecord(name="Soandso", server=0, level=50)], 0)
    path, body = posts[1]
    assert path == "/api/player/upsertplayers"
    assert body["Players"][0]["Name"] == "Soandso"
    assert body["Players"][0]["Level"] == 50


def test_send_quake_and_npc_activity_paths() -> None:
    reqs: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        reqs.append(request)
        return httpx.Response(200)

    client = _client(handler)
    client.send_quake(0)
    client.send_npc_activity(name="a Kromzek Captain", zone="kael", server=0, is_engaged=True)
    client.boat_seen(start_point="TIMORROUS", boat=2, server=0)

    assert reqs[0].url.path == "/api/zone/quakev2/Green"
    npc_body = json.loads(reqs[1].content)
    assert npc_body["NPCData"]["Name"] == "a Kromzek Captain"
    assert npc_body["IsEngaged"] is True and npc_body["IsDeath"] is False
    boat_body = json.loads(reqs[2].content)
    assert boat_body == {"StartPoint": "TIMORROUS", "Boat": 2, "Server": 0}


def test_boat_activity_and_roll_timers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "serverActivity" in request.url.path:
            return httpx.Response(
                200,
                json=[{"startPoint": "TIMORROUS", "boat": 2, "lastSeen": "2026-07-16T14:00:00Z"}],
            )
        return httpx.Response(
            200,
            json=[
                {
                    "rollTimerType": 2,
                    "guess": True,
                    "name": "Quake",
                    "dateTime": "2026-07-16T14:00:00Z",
                }
            ],
        )

    client = _client(handler)
    boats = client.boat_activity(0)
    assert boats[0].boat == 2 and boats[0].last_seen.tzinfo is None
    rolls = client.roll_timers(0)
    assert rolls[0].roll_timer_type == 2 and rolls[0].guess is True


def test_retry_once_then_success() -> None:
    calls = {"n": 0}
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500)
        return httpx.Response(200, json=[])

    assert _client(handler, sleeps).roll_timers(0) == []
    assert calls["n"] == 2
    assert sleeps == [0.5]


def test_permanent_client_error_is_not_retried() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401)

    assert _client(handler).roll_timers(0) == []
    assert calls["n"] == 1


def test_failures_degrade_to_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = _client(handler)
    assert client.item_prices(0, ["x"]) == []
    assert client.item_wiki("x", "y") is None
    assert client.players_by_names(["x"], 0) == []
    assert client.boat_activity(0) == []
    assert client.roll_timers(0) == []
    client.send_quake(0)  # no raise
    client.boat_seen(start_point="p", boat=0, server=0)  # no raise


def test_bad_payload_degrades_to_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json at all")

    assert _client(handler).roll_timers(0) == []
