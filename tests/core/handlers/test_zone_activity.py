"""ZoneActivityHandler — npcactivity sends with allow-list + Kael throttle."""

from datetime import datetime, timedelta

from tests.core.handlers.test_rest_sends import FakeApi

from nparseplus.core.bus import EventBus
from nparseplus.core.enums import Server
from nparseplus.core.events import (
    ConEvent,
    DamageEvent,
    PlayerLocationEvent,
    SlainEvent,
)
from nparseplus.core.geometry import Loc
from nparseplus.core.handlers.zone_activity import ZoneActivityHandler
from nparseplus.core.player import ActivePlayer
from nparseplus.core.zones import load_zone_database
from nparseplus.net.worker import ImmediateWorker

T0 = datetime(2026, 7, 8, 12, 0, 0)
ZONES = load_zone_database()
KAEL_MOB = sorted(ZONES.kael_faction_mobs)[0]


def _rig():
    bus = EventBus()
    player = ActivePlayer()
    player.reset_for("Xantik", Server.GREEN)
    player.zone = "kael"
    api = FakeApi()
    ZoneActivityHandler(bus, player, ZONES, api=api, submit=ImmediateWorker().submit)
    return bus, api


def _npc_sends(api: FakeApi) -> list[dict]:
    return [call[1] for call in api.calls if call[0] == "send_npc_activity"]


def test_con_of_tracked_npc_sends_with_last_loc_in_wire_order() -> None:
    bus, api = _rig()
    # "Your Location is 111, 222, 3" -> Loc(x=222, y=111); wire LocX = 111.
    bus.publish(PlayerLocationEvent(timestamp=T0, location=Loc(x=222.0, y=111.0, z=3.0)))
    bus.publish(ConEvent(timestamp=T0, name="Scout Charisa"))
    (send,) = _npc_sends(api)
    assert send["name"] == "Scout Charisa"
    assert send["is_death"] is False and send["is_engaged"] is False
    assert (send["loc_x"], send["loc_y"]) == (111.0, 222.0)
    assert send["zone"] == "kael" and send["server"] == 0


def test_untracked_names_never_hit_the_wire() -> None:
    bus, api = _rig()
    bus.publish(ConEvent(timestamp=T0, name="a rat"))
    bus.publish(SlainEvent(timestamp=T0, victim="a rat", killer="Xantik"))
    assert _npc_sends(api) == []


def test_slain_tracked_npc_sends_death() -> None:
    bus, api = _rig()
    bus.publish(SlainEvent(timestamp=T0, victim="a Kromzek Captain", killer="Xantik"))
    (send,) = _npc_sends(api)
    assert send["is_death"] is True


def test_kael_engage_throttled_to_15s() -> None:
    bus, api = _rig()

    def hit(when: datetime) -> None:
        bus.publish(
            DamageEvent(
                timestamp=when,
                target_name=KAEL_MOB,
                attacker_name="Xantik",
                damage_done=10,
                damage_type="slash",
            )
        )

    hit(T0)
    hit(T0 + timedelta(seconds=5))  # throttled
    hit(T0 + timedelta(seconds=14))  # throttled
    hit(T0 + timedelta(seconds=16))  # allowed
    engages = [s for s in _npc_sends(api) if s["is_engaged"]]
    assert len(engages) == 2
    assert engages[0]["name"] == KAEL_MOB


def test_no_api_is_silent() -> None:
    bus = EventBus()
    player = ActivePlayer()
    player.reset_for("Xantik", Server.GREEN)
    ZoneActivityHandler(bus, player, ZONES)  # api=None
    bus.publish(ConEvent(timestamp=T0, name="Scout Charisa"))  # no crash
