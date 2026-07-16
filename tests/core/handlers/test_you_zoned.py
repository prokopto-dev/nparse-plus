"""YouZonedHandler — ActivePlayer.zone tracking (YouZonedHandler.cs)."""

from datetime import datetime

from nparseplus.core.bus import EventBus
from nparseplus.core.events import YouZonedEvent
from nparseplus.core.handlers.you_zoned import YouZonedHandler
from nparseplus.core.player import ActivePlayer

T0 = datetime(2026, 7, 8, 12, 0, 0)


def test_zone_event_updates_active_player() -> None:
    bus = EventBus()
    player = ActivePlayer()
    YouZonedHandler(bus, player)
    bus.publish(YouZonedEvent(timestamp=T0, long_name="greater faydark", short_name="gfaydark"))
    assert player.zone == "gfaydark"
    bus.publish(YouZonedEvent(timestamp=T0, long_name="east commonlands", short_name="ecommons"))
    assert player.zone == "ecommons"
