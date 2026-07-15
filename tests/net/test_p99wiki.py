"""Offline tests for the P99 wiki NPC client (fixture-based, no network)."""

import json
from pathlib import Path

import httpx
import pytest

from nparseplus.core.zones import load_zone_database
from nparseplus.net.p99wiki import (
    P99WikiClient,
    parse_npc,
    parse_template_fields,
    strip_links,
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "wiki_boomba_the_big.txt"


@pytest.fixture(scope="module")
def boomba_text() -> str:
    return FIXTURE.read_text()


def test_strip_links() -> None:
    assert strip_links("[[Freeport|West Freeport]]") == "West Freeport"
    assert strip_links("[[Ogre]]") == "Ogre"
    assert strip_links("plain") == "plain"


def test_parse_template_fields(boomba_text: str) -> None:
    fields = parse_template_fields(boomba_text)
    assert fields["name"] == "Boomba the Big"
    assert fields["level"] == "20"
    assert "Freeport" in fields["zone"]


def test_parse_npc_boomba(boomba_text: str) -> None:
    zones = load_zone_database()
    npc = parse_npc("Boomba the Big", boomba_text, zones)
    assert npc.name == "Boomba the Big"
    assert npc.race == "Ogre"
    assert npc.level == "20"
    assert npc.zone_display == "West Freeport"
    assert npc.zone_short == "freportw"
    assert npc.location == (-24.0, -32.0)
    assert npc.map_location == (32.0, 24.0)
    assert npc.url.endswith("/Boomba_the_Big")


def test_parse_npc_without_location() -> None:
    npc = parse_npc("Some NPC", "{{NPCPage\n| name = Some NPC\n| zone = [[Kedge Keep]]\n}}")
    assert npc.location is None
    assert npc.map_location is None
    assert npc.zone_display == "Kedge Keep"


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_client_search_and_npc(boomba_text: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "api.php" in str(request.url):
            return httpx.Response(
                200, text=json.dumps(["Boomba", ["Boomba the Big", "Boomba the big"]])
            )
        return httpx.Response(200, text=boomba_text)

    client = P99WikiClient(zones=load_zone_database(), client=_mock_client(handler))
    titles = client.search("Boomba")
    assert titles == ["Boomba the Big", "Boomba the big"]
    npcs = client.find_npcs("Boomba", limit=2)
    assert npcs and npcs[0].zone_short == "freportw"
    # cached: a failing transport now would not matter
    assert client.search("Boomba") == titles


def test_client_degrades_on_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = P99WikiClient(client=_mock_client(handler))
    assert client.search("anything") == []
    assert client.npc("Whatever") is None
