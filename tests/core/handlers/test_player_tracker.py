"""PlayerTrackerHandler — /who roster, merge rules, 20s PigParse sync."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from nparseplus.core.bus import EventBus
from nparseplus.core.enums import PlayerClass, Server
from nparseplus.core.events import (
    AfterPlayerChangedEvent,
    WhoPlayer,
    WhoPlayerEvent,
    YouZonedEvent,
)
from nparseplus.core.handlers.player_tracker import PlayerTrackerHandler
from nparseplus.core.player import ActivePlayer
from nparseplus.net.worker import ImmediateWorker

T0 = datetime(2026, 7, 8, 12, 0, 0)


class FakeApi:
    def __init__(self) -> None:
        self.upserts: list[tuple[list, int]] = []
        self.lookups: list[tuple[list[str], int]] = []
        self.records: list = []

    def upsert_players(self, players: list, server: int) -> None:
        self.upserts.append((list(players), server))

    def players_by_names(self, names: list[str], server: int) -> list:
        self.lookups.append((list(names), server))
        return self.records


def _make(api: FakeApi | None = None) -> tuple[PlayerTrackerHandler, EventBus, ActivePlayer]:
    bus = EventBus()
    player = ActivePlayer()
    player.reset_for("Tester", Server.GREEN)
    handler = PlayerTrackerHandler(
        bus,
        player,
        api=api,
        submit=ImmediateWorker().submit if api is not None else None,
    )
    return handler, bus, player


def _who(bus: EventBus, name: str, **kwargs) -> None:
    bus.publish(WhoPlayerEvent(timestamp=T0, player=WhoPlayer(name=name, **kwargs)))


def test_who_row_builds_roster_and_known_players() -> None:
    handler, bus, player = _make()
    _who(bus, "Joe", level=50, player_class=PlayerClass.CLERIC, guild_name="Bregan")

    assert handler.is_player("Joe") and handler.is_player("joe")
    assert not handler.is_player("a rat")
    assert handler.get_class("Joe") is PlayerClass.CLERIC
    assert "Joe" in player.known_players
    assert [p.name for p in handler.players_in_zone()] == ["Joe"]
    # You are always a player (C# IsPlayer special-case).
    assert handler.is_player("Tester")


def test_merge_fills_missing_fields_without_overwriting() -> None:
    handler, bus, _player = _make()
    _who(bus, "Joe", level=50, player_class=PlayerClass.CLERIC)
    _who(bus, "Joe", level=54, guild_name="Bregan")  # anon row later

    entry = handler.get_player("Joe")
    assert entry is not None
    assert entry.level == 50  # ?? semantics: existing value kept
    assert entry.guild_name == "Bregan"  # missing field filled
    assert entry.player_class is PlayerClass.CLERIC


def test_own_who_row_updates_guild() -> None:
    handler, bus, player = _make()
    _who(bus, "Tester", guild_name="Bregan D'Aerth")
    assert player.guild_name == "Bregan D'Aerth"
    assert handler is not None


def test_zone_change_clears_zone_roster_only() -> None:
    handler, bus, player = _make()
    _who(bus, "Joe", level=50)
    player.zone = "commons"  # YouZonedHandler owns this in production
    bus.publish(YouZonedEvent(timestamp=T0, long_name="West Commonlands", short_name="commons"))
    assert handler.players_in_zone() == []
    assert handler.is_player("Joe")


def test_who_zone_reannouncement_keeps_zone_roster() -> None:
    # The /who block's trailing zone line re-publishes YouZonedEvent for the
    # SAME zone — the roster the block just built must survive (C# CurrentZone
    # comparison).
    handler, bus, player = _make()
    player.zone = "ecommons"
    handler._current_zone = player.zone
    _who(bus, "Joe", level=50)
    bus.publish(YouZonedEvent(timestamp=T0, long_name="East Commonlands", short_name="ecommons"))
    assert [p.name for p in handler.players_in_zone()] == ["Joe"]


def test_character_change_clears_everything() -> None:
    handler, bus, _player = _make()
    _who(bus, "Joe", level=50)
    bus.publish(AfterPlayerChangedEvent(timestamp=T0))
    assert not handler.is_player("Joe")
    assert handler.players_in_zone() == []


def test_tick_upserts_dirty_and_backfills_classes() -> None:
    api = FakeApi()
    handler, bus, _player = _make(api)
    _who(bus, "Joe", level=50, guild_name="Bregan")  # unknown class
    api.records = [SimpleNamespace(name="Joe", player_class=int(PlayerClass.DRUID))]

    handler.tick(T0)

    assert len(api.upserts) == 1
    sent, server = api.upserts[0]
    assert server == int(Server.GREEN)
    assert [p.name for p in sent] == ["Joe"] and sent[0].level == 50
    assert api.lookups == [(["Joe"], int(Server.GREEN))]
    assert handler.get_class("Joe") is PlayerClass.DRUID

    # Dirty set was drained: a second tick past the interval sends nothing.
    handler.tick(T0 + timedelta(seconds=30))
    assert len(api.upserts) == 1  # nothing dirty, no unknowns -> no fetch


def test_tick_respects_20s_interval() -> None:
    api = FakeApi()
    handler, bus, _player = _make(api)
    _who(bus, "Joe", level=50)
    handler.tick(T0)
    _who(bus, "Ann", level=10)
    handler.tick(T0 + timedelta(seconds=5))  # too soon
    assert len(api.upserts) == 1
    handler.tick(T0 + timedelta(seconds=21))
    assert len(api.upserts) == 2


def test_tick_noop_without_api_or_server() -> None:
    handler, bus, player = _make()  # api/submit None
    _who(bus, "Joe", level=50)
    handler.tick(T0)  # must not raise
    player.server = None
    handler.tick(T0)
