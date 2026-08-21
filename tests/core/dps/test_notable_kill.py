"""The notable-kill gate, answered on the driver thread (#78).

Port of the check inline in ``DPSMeter.xaml.cs LogParser_DeathEvent``: the
victim is in the current zone's NotableNPCs and not in KaelFactionMobs. EQTool
could copy from that method because WPF gave it no thread to cross; here the
clipboard is the GUI's and the zone is the driver's, so the decision stays
here and travels as ``NotableKillEvent``.

Qt-free — that is the point of the split, and these run without a window.
"""

from datetime import datetime, timedelta

import pytest

from nparseplus.config.settings import Settings, get_player
from nparseplus.core.bus import EventBus
from nparseplus.core.dps import FightTracker
from nparseplus.core.enums import Server
from nparseplus.core.events import (
    AfterPlayerChangedEvent,
    DamageEvent,
    NotableKillEvent,
    SlainEvent,
    YouZonedEvent,
)
from nparseplus.core.handlers.dps import DpsHandler
from nparseplus.core.handlers.player_profile import PlayerProfileHandler
from nparseplus.core.handlers.you_zoned import YouZonedHandler
from nparseplus.core.player import ActivePlayer
from nparseplus.core.zones import load_zone_database

T0 = datetime(2026, 7, 8, 21, 0, 0)


def _hit(target: str, damage: int = 70, offset_s: float = 0.0) -> DamageEvent:
    return DamageEvent(
        timestamp=T0 + timedelta(seconds=offset_s),
        target_name=target,
        attacker_name="You",
        damage_done=damage,
        damage_type="hit",
    )


class _Driver:
    """The handlers a log line actually passes through, on one bus.

    Built in composition's order, so the ordering these tests assert is the
    ordering the app has.
    """

    def __init__(self, zone: str = "permafrost", settings: Settings | None = None) -> None:
        self.bus = EventBus()
        self.settings = settings or Settings()
        self.player = ActivePlayer(name="Genartik", server=Server.GREEN, zone=zone)
        self.tracker = FightTracker()
        self.zones = load_zone_database()
        self.you_zoned = YouZonedHandler(self.bus, self.player)
        self.profile = PlayerProfileHandler(self.bus, self.player, self.settings)
        self.dps = DpsHandler(self.bus, self.player, self.tracker, zones=self.zones)
        #: Everything published, in publish order — what QtEventBridge buffers.
        self.published: list[object] = []
        self.bus.subscribe_all(self.published.append)

    def kills(self) -> list[NotableKillEvent]:
        return [e for e in self.published if isinstance(e, NotableKillEvent)]


def _slain(victim: str, offset_s: float = 0.0) -> SlainEvent:
    return SlainEvent(timestamp=T0 + timedelta(seconds=offset_s), victim=victim)


def _zoned(short_name: str, offset_s: float = 0.0) -> YouZonedEvent:
    return YouZonedEvent(
        timestamp=T0 + timedelta(seconds=offset_s),
        long_name=short_name,
        short_name=short_name,
    )


# -- the gate --------------------------------------------------------------------


def test_a_zone_notable_is_announced_with_its_parse() -> None:
    driver = _Driver(zone="permafrost")
    driver.bus.publish(_hit("Lady Vox", 70))
    driver.bus.publish(_slain("Lady Vox", offset_s=1))
    (kill,) = driver.kills()
    assert (kill.victim, kill.zone) == ("Lady Vox", "permafrost")
    assert kill.parse.startswith("Fight Details: Lady Vox Dmg: 70    You 100% ")


def test_a_notable_nobody_hit_has_no_parse_to_announce() -> None:
    """Notable, but nothing this meter counted landed on it."""
    driver = _Driver(zone="permafrost")
    driver.bus.publish(_slain("Lady Vox"))
    assert driver.kills() == []


def test_the_parse_is_dated_to_the_kill_not_to_when_it_is_read() -> None:
    """``total_dps`` comes out against the log's own clock at the kill."""
    driver = _Driver(zone="permafrost")
    driver.bus.publish(_hit("Lady Vox", 600))
    driver.bus.publish(_hit("Lady Vox", 600, offset_s=30))
    driver.bus.publish(_slain("Lady Vox", offset_s=30))
    (kill,) = driver.kills()
    assert "DMG:1200" in kill.parse
    assert "DPS:40" in kill.parse  # 1200 over the 30 s the fight ran


def test_a_kael_faction_mob_is_not() -> None:
    driver = _Driver(zone="kael")
    victim = driver.zones.kael_faction_mobs[0]
    driver.bus.publish(_hit(victim))
    driver.bus.publish(_slain(victim, offset_s=1))
    assert driver.kills() == []


def test_ordinary_trash_is_not() -> None:
    driver = _Driver(zone="permafrost")
    driver.bus.publish(_hit("a decaying skeleton"))
    driver.bus.publish(_slain("a decaying skeleton", offset_s=1))
    assert driver.kills() == []


def test_without_a_zone_database_the_question_is_never_asked() -> None:
    """``zones=None`` is the constructor default; every existing test uses it."""
    bus = EventBus()
    player = ActivePlayer(name="Genartik", zone="permafrost")
    seen: list[object] = []
    bus.subscribe(NotableKillEvent, seen.append)
    tracker = FightTracker()
    DpsHandler(bus, player, tracker)
    bus.publish(_hit("Lady Vox"))
    bus.publish(_slain("Lady Vox", offset_s=1))
    assert seen == []


def test_the_fight_is_still_ended() -> None:
    """Announcing must not have displaced TargetDied."""
    driver = _Driver(zone="permafrost")
    driver.bus.publish(_hit("Lady Vox", 10))
    driver.bus.publish(_slain("Lady Vox", offset_s=1))
    assert driver.tracker.fights[0].is_dead


# -- ordering: the reason the gate is here and not in the window -------------------


def test_the_zone_is_the_one_the_kill_happened_in_not_the_next_one() -> None:
    """Kill the boss, take the zone line out — one poll, both lines.

    Everything after the kill is still parsed before the GUI sees any of it,
    so a gate that read ``player.zone`` when the batch was drained would ask
    about Kael and refuse to copy Permafrost's boss.
    """
    driver = _Driver(zone="permafrost")
    driver.bus.publish(_hit("Lady Vox"))
    driver.bus.publish(_slain("Lady Vox", offset_s=1))
    driver.bus.publish(_zoned("kael", offset_s=5))

    assert [(k.victim, k.zone) for k in driver.kills()] == [("Lady Vox", "permafrost")]
    assert driver.player.zone == "kael"  # the driver really did move on
    # And zoning cleared the meter, which is why the parse had to be built at
    # the kill: there are no rows left for anyone to format now.
    assert driver.tracker.fights == []
    assert driver.kills()[0].parse.startswith("Fight Details: Lady Vox")


def test_the_after_slain_youzoned_batch() -> None:
    """The reviewer's batch: profile restore, kill, zone — all before a drain.

    ``AfterPlayerChangedEvent`` restores Permafrost from the profile, Lady Vox
    dies there, and the player has zoned to Kael by the time the GUI wakes up.
    Seeding a zone from ``player.zone`` while draining — at ANY point in the
    batch — reads Kael and loses the kill.
    """
    settings = Settings()
    profile = get_player(settings, "Genartik", "green")
    profile.zone = "permafrost"

    driver = _Driver(zone="", settings=settings)
    driver.bus.publish(AfterPlayerChangedEvent(timestamp=T0))
    driver.bus.publish(_hit("Lady Vox"))
    driver.bus.publish(_slain("Lady Vox", offset_s=1))
    driver.bus.publish(_zoned("kael", offset_s=2))

    assert [(k.victim, k.zone) for k in driver.kills()] == [("Lady Vox", "permafrost")]
    assert driver.player.zone == "kael"


def test_the_announcement_precedes_every_later_line() -> None:
    """What a consumer draining the batch in order actually sees.

    ``EventBus.publish`` runs the typed subscribers before the firehose, and
    ``DpsHandler._on_slain`` is a typed subscriber, so the nested
    ``NotableKillEvent`` publish completes — firehose included — before the
    SlainEvent that caused it reaches the firehose. The announcement therefore
    arrives just BEFORE its own slain line rather than just after.

    Harmless, and pinned here so it is a known property rather than a
    surprise: the window reads the fight from a fresh snapshot and the driver
    marked it dead long before the GUI woke up. What matters is the invariant
    below — the announcement is ahead of everything the log said next, so it
    can never be read against a later zone.
    """
    driver = _Driver(zone="permafrost")
    driver.bus.publish(_hit("Lady Vox"))
    driver.bus.publish(_slain("Lady Vox", offset_s=1))
    driver.bus.publish(_zoned("kael", offset_s=5))

    kinds = [type(e).__name__ for e in driver.published if type(e).__name__ != "DamageEvent"]
    assert kinds == ["NotableKillEvent", "SlainEvent", "YouZonedEvent"]
    assert kinds.index("NotableKillEvent") < kinds.index("YouZonedEvent")


def test_a_kill_after_the_zone_change_is_judged_in_the_new_zone() -> None:
    """The converse — the gate follows the zone, it is not pinned to one."""
    driver = _Driver(zone="permafrost")
    driver.bus.publish(_zoned("kael"))
    driver.bus.publish(_hit("Lady Vox", offset_s=4))
    driver.bus.publish(_slain("Lady Vox", offset_s=5))
    assert driver.kills() == []

    driver.bus.publish(_hit("King Tormax", offset_s=6))
    driver.bus.publish(_slain("King Tormax", offset_s=7))
    assert [(k.victim, k.zone) for k in driver.kills()] == [("King Tormax", "kael")]


@pytest.mark.parametrize("victim", ["lady vox", "LADY VOX"])
def test_matching_is_casefolded(victim: str) -> None:
    """DEVIATION from the C#'s ``==`` — see ZoneDatabase.is_notable_kill.

    Only casefolding, not the whitespace tolerance ``is_notable_kill`` also
    has: that strip is defensive, and ``end_fight``/``fight_parse`` match the
    fight on the raw name, so a padded victim would pass the gate and find no
    rows. Nothing in the pipeline emits one — the strip is covered directly in
    tests/core/test_zones.py.
    """
    driver = _Driver(zone="permafrost")
    driver.bus.publish(_hit("Lady Vox"))
    driver.bus.publish(_slain(victim, offset_s=1))
    assert len(driver.kills()) == 1
