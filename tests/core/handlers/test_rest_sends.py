"""REST send wiring — quake, boat, FTE guild, con enrichment (fake api)."""

from datetime import datetime

from nparseplus.core.bus import EventBus
from nparseplus.core.enums import Server
from nparseplus.core.events import BoatEvent, FTEEvent, OverlayEvent, QuakeEvent
from nparseplus.core.handlers.boat import BoatHandler
from nparseplus.core.handlers.fte import FTEHandler
from nparseplus.core.handlers.quake import QuakeHandler
from nparseplus.core.player import ActivePlayer
from nparseplus.core.timers import TimersService
from nparseplus.core.zones import load_zone_database
from nparseplus.net.worker import ImmediateWorker

T0 = datetime(2026, 7, 8, 12, 0, 0)


class FakeApi:
    """Recording double for the PigParseApi protocol."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.player_records: list = []

    def send_quake(self, server: int) -> None:
        self.calls.append(("send_quake", server))

    def boat_seen(self, *, start_point: str, boat: int, server: int) -> None:
        self.calls.append(("boat_seen", start_point, boat, server))

    def players_by_names(self, names: list[str], server: int) -> list:
        self.calls.append(("players_by_names", tuple(names), server))
        return self.player_records

    def send_npc_activity(self, **kwargs) -> None:
        self.calls.append(("send_npc_activity", kwargs))

    def item_wiki(self, name: str, zone: str) -> str | None:
        self.calls.append(("item_wiki", name, zone))
        return None

    def item_prices(self, server: int, names: list[str]) -> list:
        self.calls.append(("item_prices", server, tuple(names)))
        return []

    def boat_activity(self, server: int) -> list:
        return []

    def roll_timers(self, server: int) -> list:
        return []

    def upsert_players(self, players: list, server: int) -> None:
        pass


def _rig():
    bus = EventBus()
    player = ActivePlayer()
    player.reset_for("Xantik", Server.GREEN)
    player.zone = "oasis"
    api = FakeApi()
    return bus, player, api, ImmediateWorker().submit


def test_quake_sends_and_still_announces() -> None:
    bus, player, api, submit = _rig()
    spoken: list[str] = []

    class Speaker:
        def speak(self, text: str) -> None:
            spoken.append(text)

    overlays: list[OverlayEvent] = []
    bus.subscribe(OverlayEvent, overlays.append)
    QuakeHandler(bus, player, speaker=Speaker(), api=api, submit=submit)
    bus.publish(QuakeEvent(timestamp=T0))
    assert ("send_quake", 0) in api.calls
    assert spoken == ["Earthquake"]
    assert overlays and overlays[0].text == "EARTHQUAKE"


def test_quake_without_api_is_local_only() -> None:
    bus, player, _api, _submit = _rig()
    QuakeHandler(bus, player)  # api=None: no crash, no sends
    bus.publish(QuakeEvent(timestamp=T0))


def test_boat_sighting_shared_with_wire_enum() -> None:
    bus, player, api, submit = _rig()
    zones = load_zone_database()
    BoatHandler(bus, player, TimersService(), zones, api=api, submit=submit)
    start_point = next(b.start_point for b in zones.boats if b.boat == "BarrelBarge")
    bus.publish(BoatEvent(timestamp=T0, boat="BarrelBarge", start_point=start_point))
    assert ("boat_seen", start_point, 0, 0) in api.calls  # BarrelBarge=0, Green=0


def test_fte_overlay_decorated_with_guild() -> None:
    bus, player, api, submit = _rig()

    class Record:
        name = "Soandso"
        guild_name = "Bregan D'Aerth"

    api.player_records = [Record()]
    overlays: list[OverlayEvent] = []
    bus.subscribe(OverlayEvent, overlays.append)
    FTEHandler(bus, player, TimersService(), api=api, submit=submit)
    bus.publish(FTEEvent(timestamp=T0, npc_name="Lodizal", fte_person="Soandso"))
    assert ("players_by_names", ("Soandso",), 0) in api.calls
    assert overlays[0].text == "Soandso <Bregan D'Aerth> FTE Lodizal"


def test_fte_overlay_plain_when_lookup_empty_or_no_api() -> None:
    bus, player, api, submit = _rig()
    overlays: list[OverlayEvent] = []
    bus.subscribe(OverlayEvent, overlays.append)
    FTEHandler(bus, player, TimersService(), api=api, submit=submit)
    bus.publish(FTEEvent(timestamp=T0, npc_name="Lodizal", fte_person="Nobody"))
    assert overlays[0].text == "Nobody FTE Lodizal"

    bus2 = EventBus()
    overlays2: list[OverlayEvent] = []
    bus2.subscribe(OverlayEvent, overlays2.append)
    FTEHandler(bus2, player, TimersService())
    bus2.publish(FTEEvent(timestamp=T0, npc_name="Lodizal", fte_person="Nobody"))
    assert overlays2[0].text == "Nobody FTE Lodizal"
