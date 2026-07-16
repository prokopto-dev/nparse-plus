"""SharingCoordinator outbound policy tests (fake client, explicit clocks)."""

from datetime import datetime, timedelta

from nparseplus.config.settings import PlayerInfo, Settings
from nparseplus.core.bus import EventBus
from nparseplus.core.enums import Server
from nparseplus.core.events import CampEvent, DragonRoarEvent, PlayerLocationEvent
from nparseplus.core.geometry import Loc
from nparseplus.core.player import ActivePlayer
from nparseplus.core.sharing import SharingCoordinator
from nparseplus.core.spells.models import Spell
from nparseplus.core.timers import TimersService

T0 = datetime(2026, 7, 8, 12, 0, 0)
LOC = Loc(x=222.0, y=111.0, z=3.0)
DRAGON_ROAR = Spell(id=1, name="Dragon Roar")


class FakeSharingClient:
    def __init__(self) -> None:
        self.locations: list[dict] = []
        self.roars: list[dict] = []
        self.servers: list[int | None] = []
        self.started = 0
        self.stopped = 0
        self.status = "connected"

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1

    def set_server(self, server: int | None) -> None:
        self.servers.append(server)

    def send_location(self, **kwargs) -> None:
        self.locations.append(kwargs)

    def send_dragon_roar(self, **kwargs) -> None:
        self.roars.append(kwargs)


class Rig:
    def __init__(
        self,
        mode: str = "pigparse",
        player_info: PlayerInfo | None = None,
        server: Server | None = Server.GREEN,
        zone: str = "gfaydark",
    ) -> None:
        self.settings = Settings()
        self.settings.sharing.mode = mode
        if player_info is not None:
            self.settings.players.append(player_info)
        self.bus = EventBus()
        self.player = ActivePlayer()
        self.player.reset_for("Xantik", server)
        self.player.zone = zone
        self.player.guild_name = "Bregan D'Aerth"
        self.timers = TimersService()
        self.last_you: datetime | None = T0
        self.client = FakeSharingClient()
        self.coordinator = SharingCoordinator(
            bus=self.bus,
            player=self.player,
            settings=self.settings,
            timers=self.timers,
            last_you_activity=lambda: self.last_you,
            client=self.client,
        )

    def push_location(self, when: datetime = T0, loc: Loc = LOC) -> None:
        self.bus.publish(PlayerLocationEvent(timestamp=when, location=loc))

    def push_roar(self, when: datetime = T0) -> None:
        self.bus.publish(DragonRoarEvent(timestamp=when, spell=DRAGON_ROAR))


def test_location_event_sends_immediately_with_player_facts() -> None:
    rig = Rig()
    rig.push_location()
    (sent,) = rig.client.locations
    assert sent["name"] == "Xantik"
    assert sent["guild_name"] == "Bregan D'Aerth"
    assert sent["server"] == int(Server.GREEN)
    assert sent["zone"] == "gfaydark"
    assert sent["sharing"] == 0  # everyone (default)
    assert sent["loc"] == LOC


def test_keepalive_resends_every_10s_when_active() -> None:
    rig = Rig()
    rig.push_location(T0)
    rig.last_you = T0 + timedelta(seconds=15)
    rig.coordinator.tick(T0 + timedelta(seconds=5))
    assert len(rig.client.locations) == 1  # too soon
    rig.coordinator.tick(T0 + timedelta(seconds=10))
    assert len(rig.client.locations) == 2
    rig.last_you = T0 + timedelta(seconds=25)
    rig.coordinator.tick(T0 + timedelta(seconds=20))
    assert len(rig.client.locations) == 3
    assert rig.client.locations[-1]["loc"] == LOC


def test_keepalive_stops_after_5_idle_minutes() -> None:
    rig = Rig()
    rig.push_location(T0)
    rig.last_you = T0
    quiet = T0 + timedelta(minutes=5, seconds=11)
    rig.coordinator.tick(quiet)
    assert len(rig.client.locations) == 1  # suppressed, state cleared
    rig.last_you = quiet  # activity resumes, but no /loc since
    rig.coordinator.tick(quiet + timedelta(seconds=10))
    assert len(rig.client.locations) == 1  # stays quiet until the next /loc


def test_camp_clears_keepalive() -> None:
    rig = Rig()
    rig.push_location(T0)
    rig.bus.publish(CampEvent(timestamp=T0 + timedelta(seconds=1)))
    rig.last_you = T0 + timedelta(seconds=12)
    rig.coordinator.tick(T0 + timedelta(seconds=12))
    assert len(rig.client.locations) == 1


def test_mode_off_sends_nothing() -> None:
    rig = Rig(mode="off")
    rig.push_location()
    rig.push_roar()
    assert rig.client.locations == [] and rig.client.roars == []
    assert rig.coordinator.status == "off"


def test_unknown_server_sends_nothing() -> None:
    rig = Rig(server=None)
    rig.push_location()
    rig.push_roar()
    assert rig.client.locations == [] and rig.client.roars == []


def test_per_character_map_sharing_off_blocks_location_not_roar() -> None:
    info = PlayerInfo(name="Xantik", server="green", map_location_sharing="off")
    rig = Rig(player_info=info)
    rig.push_location()
    rig.push_roar()
    assert rig.client.locations == []
    assert len(rig.client.roars) == 1  # roars gate on share_timers only


def test_per_character_guild_sharing_wire_value() -> None:
    info = PlayerInfo(name="Xantik", server="green", map_location_sharing="guild")
    rig = Rig(player_info=info)
    rig.push_location()
    assert rig.client.locations[0]["sharing"] == 1  # MapLocationSharing.GUILD_ONLY


def test_share_timers_off_blocks_roar() -> None:
    info = PlayerInfo(name="Xantik", server="green", share_timers=False)
    rig = Rig(player_info=info)
    rig.push_roar()
    assert rig.client.roars == []


def test_roar_requires_zone() -> None:
    rig = Rig(zone="")
    rig.push_roar()
    assert rig.client.roars == []


def test_roar_dedupes_within_4s_and_carries_last_loc() -> None:
    rig = Rig()
    rig.push_location(T0)
    rig.push_roar(T0 + timedelta(seconds=1))
    rig.push_roar(T0 + timedelta(seconds=3))  # same spell, <4s: dropped
    assert len(rig.client.roars) == 1
    rig.push_roar(T0 + timedelta(seconds=6))  # >4s since the first: sent
    assert len(rig.client.roars) == 2
    assert rig.client.roars[0]["spell_name"] == "Dragon Roar"
    assert rig.client.roars[0]["loc"] == LOC
    assert rig.client.roars[0]["server"] == 0


def test_roar_without_prior_location_has_no_loc() -> None:
    rig = Rig()
    rig.push_roar()
    assert rig.client.roars[0]["loc"] is None


def test_location_carries_tracking_distance_for_trackable_class() -> None:
    from nparseplus.core.enums import PlayerClass

    rig = Rig()
    rig.player.player_class = PlayerClass.DRUID
    rig.player.tracking_skill = 100
    rig.push_location()
    assert rig.client.locations[0]["tracking_distance"] == 2000.0

    rig2 = Rig()
    rig2.player.player_class = PlayerClass.WARRIOR
    rig2.push_location()
    assert rig2.client.locations[0]["tracking_distance"] is None


def test_player_name_override_is_used() -> None:
    rig = Rig()
    rig.settings.sharing.player_name_override = "Anonymoose"
    rig.push_location()
    assert rig.client.locations[0]["name"] == "Anonymoose"


def test_tick_syncs_server_to_client_once_per_change() -> None:
    rig = Rig()
    rig.coordinator.tick(T0)
    rig.coordinator.tick(T0 + timedelta(seconds=1))
    assert rig.client.servers == [int(Server.GREEN)]
    rig.player.reset_for("Altchar", Server.BLUE)
    rig.coordinator.tick(T0 + timedelta(seconds=2))
    assert rig.client.servers == [int(Server.GREEN), int(Server.BLUE)]


def test_inbound_callables_run_on_tick_and_failures_are_contained() -> None:
    rig = Rig()
    ran: list[str] = []
    rig.coordinator.enqueue_inbound(lambda: ran.append("a"))

    def boom() -> None:
        raise RuntimeError("scripted")

    rig.coordinator.enqueue_inbound(boom)
    rig.coordinator.enqueue_inbound(lambda: ran.append("b"))
    rig.coordinator.tick(T0)
    assert ran == ["a", "b"]


def test_status_reflects_mode_and_client() -> None:
    rig = Rig()
    assert rig.coordinator.status == "pigparse — connected"
    rig.coordinator.set_client(None)
    assert rig.coordinator.status == "off"
