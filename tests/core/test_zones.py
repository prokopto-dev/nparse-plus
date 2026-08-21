"""ZoneDatabase.is_notable_kill — the auto-copy gate (#78).

Port of ``EQTool/UI/DPSMeter.xaml.cs LogParser_DeathEvent``: notable in the
zone you are standing in, and not one of the Kael faction giants.
"""

from nparseplus.core.zones import ZoneDatabase, load_zone_database


def _zones() -> ZoneDatabase:
    return load_zone_database()


def test_a_zone_notable_is_copied() -> None:
    assert _zones().is_notable_kill("Lady Vox", "permafrost")


def test_a_kael_faction_mob_is_not_copied_even_though_it_is_notable() -> None:
    """The whole reason the predicate has two clauses.

    Kael's faction giants are listed as notable so the map and spawn timers
    treat them properly, and they die by the hundred — auto-copying each one
    would overwrite the clipboard all evening.
    """
    zones = _zones()
    kael = zones.get("kael")
    assert kael is not None
    victim = zones.kael_faction_mobs[0]
    assert victim in kael.notable_npcs  # notable, and still refused
    assert not zones.is_notable_kill(victim, "kael")


def test_ordinary_trash_is_not_copied() -> None:
    assert not _zones().is_notable_kill("a decaying skeleton", "permafrost")


def test_a_notable_from_another_zone_is_not_copied() -> None:
    """The list is per zone, so the same name elsewhere is just a stranger."""
    assert not _zones().is_notable_kill("Lady Vox", "kael")


def test_an_unknown_or_missing_zone_never_copies() -> None:
    zones = _zones()
    assert not zones.is_notable_kill("Lady Vox", "notazone")
    assert not zones.is_notable_kill("Lady Vox", None)
    assert not zones.is_notable_kill("Lady Vox", "")


def test_matching_is_casefolded() -> None:
    """DEVIATION from the C#'s ``==`` — see the docstring on the method."""
    zones = _zones()
    assert zones.is_notable_kill("lady vox", "permafrost")
    assert zones.is_notable_kill("  LADY VOX  ", "PERMAFROST")


def test_an_empty_victim_is_not_a_kill() -> None:
    zones = _zones()
    assert not zones.is_notable_kill("", "permafrost")
    assert not zones.is_notable_kill("   ", "permafrost")
