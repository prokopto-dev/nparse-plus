"""NpcSearchIndex / search_all_zones tests (pure, Qt-free)."""

from __future__ import annotations

from nparseplus.core.npc_search import (
    NpcSearchIndex,
    SearchHit,
    search_all_zones,
    zone_display_names,
)
from nparseplus.core.zones import (
    DEFAULT_RESPAWN_SECONDS,
    NpcSpawnTime,
    ZoneDatabase,
    ZoneInfo,
)


def make_db() -> ZoneDatabase:
    zones = {
        "unrest": ZoneInfo(
            name="unrest",
            respawn_seconds=1320,
            notable_npcs=("Garanel Rucksif", "Khrix Fritchoff"),
            npc_spawn_times=(
                NpcSpawnTime(name="Garanel Rucksif", seconds=600),
                NpcSpawnTime(name="The Hag", seconds=900),
            ),
        ),
        "mistmoore": ZoneInfo(
            name="mistmoore",
            respawn_seconds=1200,
            notable_npcs=("Advisor Svartmane", "Garanel Rucksif"),
        ),
        "gfaydark": ZoneInfo(name="gfaydark"),
    }
    return ZoneDatabase(
        zones=zones,
        boats=[],
        kael_faction_mobs=[],
        zone_name_mapper={
            "the estate of unrest": "unrest",
            "castle mistmoore": "mistmoore",
            "greater faydark": "gfaydark",
        },
        zone_who_mapper={},
    )


LABELS = [
    ("Garanel Rucksif", 10.0, 20.0, -5.0),
    ("Priest of Najena", 40.0, 50.0, 0.0),
    ("Torch (Priest key)", 1.0, 2.0, 3.0),
]


def make_index(zone_key: str = "unrest") -> NpcSearchIndex:
    return NpcSearchIndex(zone_key=zone_key, labels=LABELS, zones=make_db())


def test_label_substring_match_case_insensitive() -> None:
    hits = make_index().search("nAjEnA")
    assert [hit.name for hit in hits] == ["Priest of Najena"]
    assert hits[0].kind == "label"


def test_startswith_ranked_before_substring() -> None:
    hits = make_index().search("priest")
    assert [hit.name for hit in hits] == ["Priest of Najena", "Torch (Priest key)"]


def test_label_hit_has_location_and_zone_default_respawn() -> None:
    (hit,) = make_index().search("najena")
    assert hit.location == (40.0, 50.0, 0.0)
    assert hit.respawn_seconds == 1320  # unrest zone default


def test_notable_with_matching_label_gets_location() -> None:
    hits = make_index().search("garanel")
    notable = next(hit for hit in hits if hit.kind == "notable")
    assert notable.location == (10.0, 20.0, -5.0)
    assert notable.respawn_seconds == 600  # exact npc_spawn_times match


def test_notable_without_label_has_no_location_but_respawn() -> None:
    hits = make_index().search("khrix")
    assert hits == [
        SearchHit(kind="notable", name="Khrix Fritchoff", location=None, respawn_seconds=1320)
    ]


def test_notable_names_deduplicated_across_sources() -> None:
    # "Garanel Rucksif" appears in both notable_npcs and npc_spawn_times.
    hits = make_index().search("garanel rucksif")
    assert [hit.kind for hit in hits] == ["label", "notable"]


def test_spawn_time_only_npcs_are_searchable() -> None:
    hits = make_index().search("the hag")
    assert [(hit.name, hit.respawn_seconds) for hit in hits] == [("The Hag", 900)]


def test_empty_and_unmatched_queries() -> None:
    index = make_index()
    assert index.search("") == []
    assert index.search("   ") == []
    assert index.search("zebra") == []


def test_labels_rank_before_notables() -> None:
    hits = make_index().search("garanel")
    assert [hit.kind for hit in hits] == ["label", "notable"]


def test_index_without_zone_database() -> None:
    index = NpcSearchIndex(zone_key=None, labels=LABELS, zones=None)
    (hit,) = index.search("najena")
    assert hit.respawn_seconds is None
    assert index.notables() == []


def test_notables_listing_sorted() -> None:
    names = [hit.name for hit in make_index().notables()]
    assert names == ["Garanel Rucksif", "Khrix Fritchoff", "The Hag"]


def test_unknown_zone_key_yields_labels_only() -> None:
    index = NpcSearchIndex(zone_key="nowhere", labels=LABELS, zones=make_db())
    assert index.notables() == []
    assert index.search("najena")[0].kind == "label"


def test_search_all_zones_cross_zone_hits() -> None:
    hits = search_all_zones("garanel", make_db())
    assert [(hit.zone_key, hit.name) for hit in hits] == [
        ("mistmoore", "Garanel Rucksif"),
        ("unrest", "Garanel Rucksif"),
    ]
    assert all(hit.kind == "zone-notable" and hit.location is None for hit in hits)
    by_zone = {hit.zone_key: hit for hit in hits}
    assert by_zone["unrest"].respawn_seconds == 600
    assert by_zone["mistmoore"].respawn_seconds == 1200


def test_search_all_zones_display_names() -> None:
    hits = search_all_zones("svartmane", make_db())
    assert [hit.zone_display for hit in hits] == ["Castle Mistmoore"]


def test_search_all_zones_empty_query() -> None:
    assert search_all_zones("", make_db()) == []


def test_zone_display_names_prefers_shortest_alias() -> None:
    db = ZoneDatabase(
        zones={"sebilis": ZoneInfo(name="sebilis")},
        boats=[],
        kael_faction_mobs=[],
        zone_name_mapper={"ruins of sebilis": "sebilis", "old sebilis": "sebilis"},
        zone_who_mapper={},
    )
    assert zone_display_names(db) == {"sebilis": "Old Sebilis"}


def test_underscore_labels_match_spaced_queries() -> None:
    # EQ map labels use underscores instead of spaces.
    index = NpcSearchIndex(
        zone_key="unrest",
        labels=[("Garanel_Rucksif", 1.0, 2.0, 3.0)],
        zones=make_db(),
    )
    hits = index.search("garanel rucksif")
    assert [hit.kind for hit in hits] == ["label", "notable"]
    assert hits[0].name == "Garanel_Rucksif"
    # The notable also picks its location up from the underscored label.
    assert hits[1].location == (1.0, 2.0, 3.0)


def test_default_respawn_fallback() -> None:
    db = make_db()
    index = NpcSearchIndex(zone_key="gfaydark", labels=[("Orc Camp", 1.0, 2.0, 3.0)], zones=db)
    (hit,) = index.search("orc")
    assert hit.respawn_seconds == DEFAULT_RESPAWN_SECONDS
