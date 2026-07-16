"""SharingCoordinator inbound dispatch: self-echo, server gates, timers."""

from datetime import datetime, timedelta

from nparseplus.config.settings import PlayerInfo, Settings
from nparseplus.core.bus import EventBus
from nparseplus.core.enums import Server
from nparseplus.core.events import (
    CustomTimerReceivedRemoteEvent,
    DragonRoarRemoteEvent,
    OtherPlayerLocationReceivedRemoteEvent,
    PlayerDisconnectReceivedRemoteEvent,
    RemotePlayer,
)
from nparseplus.core.handlers.spawn_timer import CUSTOM_TIMER_GROUP
from nparseplus.core.player import ActivePlayer
from nparseplus.core.sharing import SharingCoordinator
from nparseplus.core.timers import TimerRow, TimersService

T0 = datetime(2026, 7, 8, 12, 0, 0)


def remote(name: str = "Soandso", server: int = 0, zone: str = "gfaydark") -> RemotePlayer:
    return RemotePlayer(name=name, server=server, zone=zone, x=1.0, y=2.0, z=3.0)


class Rig:
    def __init__(self, player_info: PlayerInfo | None = None) -> None:
        self.settings = Settings()
        if player_info is not None:
            self.settings.players.append(player_info)
        self.bus = EventBus()
        self.player = ActivePlayer()
        self.player.reset_for("Xantik", Server.GREEN)
        self.player.zone = "gfaydark"
        self.timers = TimersService()
        self.published: list[object] = []
        self.bus.subscribe_all(self.published.append)
        self.coordinator = SharingCoordinator(
            bus=self.bus,
            player=self.player,
            settings=self.settings,
            timers=self.timers,
            last_you_activity=lambda: None,
            client=None,  # inbound path needs no client
        )

    def deliver(self, item: object) -> None:
        self.coordinator.enqueue_inbound(item)
        self.coordinator.tick(T0)


def test_remote_location_published_to_bus() -> None:
    rig = Rig()
    event = OtherPlayerLocationReceivedRemoteEvent(player=remote())
    rig.deliver(event)
    assert event in rig.published


def test_self_echo_dropped() -> None:
    rig = Rig()
    rig.deliver(OtherPlayerLocationReceivedRemoteEvent(player=remote(name="Xantik", server=0)))
    assert rig.published == []
    # Same name from another server is a different character: passes.
    other_server = OtherPlayerLocationReceivedRemoteEvent(player=remote(name="Xantik", server=1))
    rig.deliver(other_server)
    assert other_server in rig.published


def test_self_echo_uses_override_name() -> None:
    rig = Rig()
    rig.settings.sharing.player_name_override = "Anonymoose"
    rig.deliver(OtherPlayerLocationReceivedRemoteEvent(player=remote(name="Anonymoose", server=0)))
    assert rig.published == []


def test_disconnect_published_with_same_filter() -> None:
    rig = Rig()
    ours = PlayerDisconnectReceivedRemoteEvent(player=remote(name="Xantik", server=0))
    theirs = PlayerDisconnectReceivedRemoteEvent(player=remote(name="Soandso"))
    rig.deliver(ours)
    rig.deliver(theirs)
    assert ours not in rig.published
    assert theirs in rig.published


def test_dragon_roar_same_server_and_share_timers() -> None:
    rig = Rig()
    roar = DragonRoarRemoteEvent(spell_name="Dragon Roar", server=0)
    rig.deliver(roar)
    assert roar in rig.published


def test_dragon_roar_wrong_server_dropped() -> None:
    rig = Rig()
    roar = DragonRoarRemoteEvent(spell_name="Dragon Roar", server=1)
    rig.deliver(roar)
    assert rig.published == []


def test_dragon_roar_share_timers_off_dropped() -> None:
    rig = Rig(player_info=PlayerInfo(name="Xantik", server="green", share_timers=False))
    rig.deliver(DragonRoarRemoteEvent(spell_name="Dragon Roar", server=0))
    assert rig.published == []


def test_custom_timer_adds_row_and_publishes() -> None:
    rig = Rig()
    event = CustomTimerReceivedRemoteEvent(
        name="Kael Faction Pull In Progress", duration_in_seconds=90, server=0
    )
    rig.deliver(event)
    row = rig.timers.find("Kael Faction Pull In Progress", CUSTOM_TIMER_GROUP)
    assert isinstance(row, TimerRow)
    assert row.ends_at == T0 + timedelta(seconds=90)
    assert event in rig.published


def test_custom_timer_restarts_existing_row() -> None:
    rig = Rig()
    event = CustomTimerReceivedRemoteEvent(
        name="Next Kael Faction Pull", duration_in_seconds=28 * 60, server=0
    )
    rig.deliver(event)
    # Second push restarts the same row instead of duplicating.
    rig.coordinator.enqueue_inbound(event)
    rig.coordinator.tick(T0 + timedelta(seconds=60))
    rows = [row for row in rig.timers.rows_of(TimerRow) if row.name == "Next Kael Faction Pull"]
    assert len(rows) == 1
    assert rows[0].ends_at == T0 + timedelta(seconds=60 + 28 * 60)


def test_custom_timer_wrong_server_dropped() -> None:
    rig = Rig()
    rig.deliver(CustomTimerReceivedRemoteEvent(name="X", duration_in_seconds=90, server=2))
    assert rig.published == []
    assert rig.timers.find("X", CUSTOM_TIMER_GROUP) is None
