"""Offline tests for the P99 wiki NPC client (fixture-based, no network)."""

import json
from pathlib import Path

import httpx
import pytest

from nparseplus.core.zones import load_zone_database
from nparseplus.net.p99wiki import (
    MAX_IMAGE_BYTES,
    P99WikiClient,
    WikiNpc,
    is_npc_page,
    parse_drops,
    parse_image_filename,
    parse_image_url,
    parse_links,
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


# --- the full page: the EQtoolsTests corpus (#113) ------------------------------
#
# These five fixtures are the const strings from EQtoolsTests/MobInfoTests.cs
# at spec commit d8e8084f, verbatim. The C# tests are the spec for how a P99
# NPC page parses, so they are what this ports against.

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


@pytest.fixture(scope="module")
def mraaka() -> WikiNpc:
    return parse_npc("Mraaka", _fixture("wiki_mraaka.txt"))


def test_stat_block(mraaka: WikiNpc) -> None:
    assert mraaka.name == "Mraaka"
    assert mraaka.npc_class == "Warrior"
    assert mraaka.race == "Wurm"
    assert mraaka.level == "66"
    assert mraaka.hp == "~51k (?)"  # the page's own hedging, kept verbatim
    assert mraaka.ac == "492"
    assert mraaka.damage_per_hit == "161 - 264"
    assert mraaka.attacks_per_round == "Normal (3) - Flurry (6)"
    assert mraaka.attack_speed == "78%"
    assert mraaka.run_speed == "1.9"
    assert mraaka.aggro_radius == "300"
    assert mraaka.has_details


def test_spawn_location_and_respawn(mraaka: WikiNpc) -> None:
    assert mraaka.spawn_location == "100% @ (-1699, -73)"
    assert mraaka.respawn == "?"
    assert mraaka.location == (-1699.0, -73.0)  # still the map's first pair


def test_every_spawn_point_survives_in_the_text() -> None:
    """The map wants one pair; a player wants all three."""
    npc = parse_npc("a burning guardian", _fixture("wiki_a_burning_guardian.txt"))
    assert npc.spawn_location == ("50% @ (-1040, -1040), 50% @ (-1540, -1355), 50% @ (-820, -1117)")
    assert npc.location == (-1040.0, -1040.0)


def test_link_fields(mraaka: WikiNpc) -> None:
    assert [link.name for link in mraaka.factions] == ["Claws of Veeshan", "Yelinak"]
    assert mraaka.factions[0].note == "(-30)"  # EQTool glues this onto the name
    assert mraaka.factions[0].url == "https://wiki.project1999.com/Claws_of_Veeshan"
    assert [link.name for link in mraaka.opposing_factions] == ["Kromzek"]
    assert [link.name for link in mraaka.specials][:3] == ["Lava Breath", "Enrage", "Summon"]
    assert mraaka.related_quests == ()  # the page says "None"


def test_a_gloss_lands_on_the_spell_it_describes() -> None:
    npc = parse_npc("a burning guardian", _fixture("wiki_a_burning_guardian.txt"))
    names = [(link.name, link.note) for link in npc.specials]
    assert ("Rain of Molten Lava", "(PB AE 300 Fire Damage)") in names
    assert "See Invis" in [link.name for link in npc.specials]


def test_drops_from_the_known_loot_field(mraaka: WikiNpc) -> None:
    drops = {drop.name: drop.rarity for drop in mraaka.drops}
    assert drops["Exquisite Velium Warsword"] == "Uncommon"
    assert drops["Exquisite Velium Claidhmore"] == "Ultra Rare"
    assert drops["Wurm Meat"] == "Common"
    # A plain-text row with no item page still counts, minus its annotations.
    assert drops["Level 50+ Velious Spells"] == "Uncommon"
    assert mraaka.drops[0].url == "https://wiki.project1999.com/Exquisite_Velium_Warsword"


def test_a_piped_drop_link_names_the_page_not_the_display_text() -> None:
    """MobInfoTests.ParseKnownLoot: "Spell: X" is the label, X is the item."""
    npc = parse_npc("a burning guardian", _fixture("wiki_a_burning_guardian.txt"))
    names = [drop.name for drop in npc.drops]
    assert names == [
        "Form of the Great Bear",
        "Circle of Cobalt Scar",
        "Stun Command",
        "Nature Walker's Behest",
        "Wurm Meat",
    ]


def test_a_page_whose_loot_is_none_drops_nothing() -> None:
    npc = parse_npc("Taia Lyfol", _fixture("wiki_taia_lyfol.txt"))
    assert npc.drops == ()
    assert npc.hp == "1739"  # ... but it is still an NPC page


def test_a_citation_glued_to_the_name_is_trimmed() -> None:
    npc = parse_npc("Sir Edwin Motte", _fixture("wiki_sir_edwin_motte.txt"))
    assert npc.name == "Sir Edwin Motte"
    assert npc.npc_class == "Quest NPC"  # ''italics'' in the wikitext
    assert [drop.rarity for drop in npc.drops][-1] == "Always"


def test_drops_from_a_body_table_when_there_is_no_known_loot_field() -> None:
    """Shape B: the page lists loot in a wikitable under a heading."""
    npc = parse_npc("a shady goblin", _fixture("wiki_loot_table_npc.txt"))
    assert [drop.name for drop in npc.drops] == [
        "Rusty Short Sword",
        "Goblin Gazughi Ring",
        "Tarnished Bronze Mask",
    ]
    # The other cells in those rows are not items.
    assert not any(drop.name in {"Common", "Rare", "Primary"} for drop in npc.drops)


def test_drops_from_a_body_bullet_list() -> None:
    text = (
        "{{Namedmobpage\n| name = a rat\n| HP = 30\n}}\n\n"
        "== Drops ==\n* [[Rat Ear]]\n* [[Tiny Whisker]]\n\n[[Category:Qeynos]]\n"
    )
    assert [drop.name for drop in parse_drops(text)] == ["Rat Ear", "Tiny Whisker"]


def test_a_page_with_no_loot_anywhere_yields_nothing() -> None:
    assert parse_drops("{{Namedmobpage\n| name = a rat\n| HP = 30\n}}\n") == []


def test_a_page_with_no_template_has_no_details() -> None:
    npc = parse_npc("Some Quest", "A prose article about a quest. [[Qeynos]] is nearby.\n")
    assert not npc.has_details


def test_image_filename_is_trimmed_at_its_extension() -> None:
    assert parse_image_filename("| imagefilename = Mraaka.jpg (old)\n") == "Mraaka.jpg"
    assert parse_image_filename("| name = x\n") == ""


# --- the picture ----------------------------------------------------------------


def test_parse_image_url_reads_both_api_shapes() -> None:
    legacy = json.dumps(
        {"query": {"pages": {"1234": {"imageinfo": [{"thumburl": "https://w/thumb.png"}]}}}}
    )
    modern = json.dumps({"query": {"pages": [{"imageinfo": [{"url": "https://w/full.png"}]}]}})
    assert parse_image_url(legacy) == "https://w/thumb.png"
    assert parse_image_url(modern) == "https://w/full.png"


def test_parse_image_url_degrades() -> None:
    assert parse_image_url("not json at all") is None
    assert parse_image_url(json.dumps({"query": {"pages": {"-1": {"missing": ""}}}})) is None


def test_npc_with_image_resolves_and_caches_the_file(tmp_path: Path) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requests.append(url)
        if "prop=imageinfo" in url:
            return httpx.Response(
                200,
                text=json.dumps(
                    {"query": {"pages": {"7": {"imageinfo": [{"thumburl": "https://w/m.png"}]}}}}
                ),
            )
        if url == "https://w/m.png":
            return httpx.Response(200, content=b"\x89PNG-pretend")
        return httpx.Response(200, text=_fixture("wiki_mraaka.txt"))

    client = P99WikiClient(client=_mock_client(handler), image_cache_dir=tmp_path)
    npc = client.npc("Mraaka", with_image=True)
    assert npc is not None and npc.image_url == "https://w/m.png"
    assert npc.image_path is not None and npc.image_path.read_bytes() == b"\x89PNG-pretend"
    assert not list(tmp_path.glob("*.part"))  # promoted by rename, not left staged

    # Second look: everything is cached, page and picture alike.
    before = len(requests)
    again = client.npc("Mraaka", with_image=True)
    assert again is not None and again.image_path == npc.image_path
    assert len(requests) == before


def test_without_with_image_nothing_reaches_the_image_endpoints(tmp_path: Path) -> None:
    """The picture setting is off: not one extra request leaves."""
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, text=_fixture("wiki_mraaka.txt"))

    client = P99WikiClient(client=_mock_client(handler), image_cache_dir=tmp_path)
    npc = client.npc("Mraaka")
    assert npc is not None and npc.image_file == "Mraaka.jpg"
    assert npc.image_path is None and npc.image_url is None
    assert len(requests) == 1
    assert not list(tmp_path.iterdir())


def test_no_cache_dir_means_no_download() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"no request should be made: {request.url}")

    client = P99WikiClient(client=_mock_client(handler))
    assert client.cache_image("https://w/m.png") is None


def test_an_oversized_image_is_refused(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (MAX_IMAGE_BYTES + 1))

    client = P99WikiClient(client=_mock_client(handler), image_cache_dir=tmp_path)
    assert client.cache_image("https://w/huge.png") is None
    assert not list(tmp_path.iterdir())


def test_a_failing_image_lookup_leaves_the_rest_of_the_page(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "prop=imageinfo" in str(request.url):
            return httpx.Response(500)
        return httpx.Response(200, text=_fixture("wiki_mraaka.txt"))

    client = P99WikiClient(client=_mock_client(handler), image_cache_dir=tmp_path)
    npc = client.npc("Mraaka", with_image=True)
    assert npc is not None and npc.hp == "~51k (?)"
    assert npc.image_url is None and npc.image_path is None


# --- caching + redirects --------------------------------------------------------


def test_one_page_request_per_title_until_the_ttl_expires() -> None:
    fetches: list[str] = []
    now = [1000.0]

    def handler(request: httpx.Request) -> httpx.Response:
        fetches.append(str(request.url))
        return httpx.Response(200, text=_fixture("wiki_vilefang.txt"))

    client = P99WikiClient(client=_mock_client(handler), ttl_s=60.0, clock=lambda: now[0])
    assert client.npc("Vilefang") is not None
    for _ in range(10):  # what the window's poll would do if it fetched
        client.npc("Vilefang")
    assert len(fetches) == 1
    now[0] += 61.0
    client.npc("Vilefang")
    assert len(fetches) == 2


def test_a_missing_page_is_remembered_as_missing() -> None:
    fetches: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        fetches.append(str(request.url))
        return httpx.Response(404)

    client = P99WikiClient(client=_mock_client(handler))
    assert client.npc("a gnoll pup") is None
    assert client.npc("a gnoll pup") is None
    assert len(fetches) == 1  # a mob with no page must not be re-asked


def test_a_redirect_page_is_followed_once() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "Lord" not in str(request.url):
            return httpx.Response(200, text="#REDIRECT [[Lord Nagafen]]\n")
        return httpx.Response(200, text=_fixture("wiki_vilefang.txt"))

    client = P99WikiClient(client=_mock_client(handler))
    npc = client.npc("Nagafen")
    assert npc is not None and npc.hp == "19000"


# --- shapes found on the live wiki ----------------------------------------------


def test_an_empty_field_does_not_swallow_the_next_one() -> None:
    """ "A Lava Basilisk" states no attacks per round.

    An ``\\s*`` after the "=" crosses the newline and captures the following
    field's whole line as the empty one's value, so the page reported
    ``attacks_per_round = "| attack_speed = 95%"`` and no attack speed at all.
    """
    text = (
        "{{Namedmobpage\n"
        "| name              = A Lava Basilisk\n"
        "| attacks_per_round = \n"
        "| attack_speed      = 95%\n"
        "| damage_per_hit    = 6 - 29\n"
        "}}\n"
    )
    npc = parse_npc("A Lava Basilisk", text)
    assert npc.attacks_per_round == ""
    assert npc.attack_speed == "95%"
    assert npc.damage_per_hit == "6 - 29"


def test_the_body_scan_does_not_run_on_a_page_that_is_not_an_npcs() -> None:
    """ "Cazic Thule" redirects to the ZONE article, whose tables are not loot."""
    zone_page = (
        "== Notable items ==\n"
        '{| class="wikitable"\n! Item !! Where\n|-\n| [[Radiant]] || [[Feerrott]]\n|}\n'
    )
    assert is_npc_page(zone_page) is False
    assert parse_drops(zone_page) == []
    # The same table on a page that IS an NPC's is read.
    assert is_npc_page("| HP = 300\n" + zone_page) is True
    assert [d.name for d in parse_drops("| HP = 300\n" + zone_page)] == ["Radiant", "Feerrott"]


def test_a_faction_page_link_names_the_faction() -> None:
    links = parse_links("* [[Trakanon (Faction)]] <span class='profac'>(-30)</span>")
    assert links[0].name == "Trakanon"
    assert links[0].note == "(-30)"
    # ... and still points at the page that exists.
    assert links[0].url == "https://wiki.project1999.com/Trakanon_(Faction)"


def test_lookup_keeps_an_unreachable_wiki_distinct_and_does_not_cache_it() -> None:
    fetches: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        fetches.append(str(request.url))
        return httpx.Response(500)

    client = P99WikiClient(client=_mock_client(handler))
    assert client.lookup("Lord Nagafen").status == "unreachable"
    assert client.lookup("Lord Nagafen").status == "unreachable"
    assert len(fetches) == 2  # recovery must not wait out a negative TTL


def test_lookup_remembers_a_page_the_wiki_says_is_missing() -> None:
    fetches: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        fetches.append(str(request.url))
        return httpx.Response(404)

    client = P99WikiClient(client=_mock_client(handler))
    assert client.lookup("a gnoll pup").status == "missing"
    assert client.lookup("a gnoll pup").status == "missing"
    assert len(fetches) == 1
