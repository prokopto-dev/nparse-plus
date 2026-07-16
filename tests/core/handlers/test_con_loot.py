"""ConHandler wiki-loot parsing + price enrichment."""

from datetime import datetime

from nparseplus.core.bus import EventBus
from nparseplus.core.enums import Server
from nparseplus.core.events import ConEvent
from nparseplus.core.handlers.con import ConHandler, MobInfoState, parse_known_loot
from nparseplus.core.player import ActivePlayer
from nparseplus.core.zones import load_zone_database
from nparseplus.net.pigparse_models import ItemPrice
from nparseplus.net.worker import ImmediateWorker

T0 = datetime(2026, 7, 8, 12, 0, 0)

MOB_PAGE = """{{ Mobpage
| name = Lord Nagafen
| known_loot =
*{{:Cloak of Flames}}<br>
*{{ :Dragon Scales }}<br>
*[[Red Dragon Scales|Red Scales]]<br>
casts: [[Lava Breath]]<br>
*{{:Cloak of Flames}}<br>
| faction = Nagafen
}}"""


def test_parse_known_loot_transclusions_links_and_dedupe() -> None:
    loot = parse_known_loot(MOB_PAGE)
    names = [entry.name for entry in loot]
    # casts: line skipped; duplicate dropped; [[A|B]] keeps the display name.
    assert names == ["Cloak of Flames", "Dragon Scales", "Red Scales"]
    assert loot[0].url == "https://wiki.project1999.com/Cloak_of_Flames"


def test_parse_known_loot_absent_field() -> None:
    assert parse_known_loot("{{ Mobpage | name = A Rat }}") == []


def test_con_enriches_loot_with_prices() -> None:
    bus = EventBus()
    player = ActivePlayer()
    player.reset_for("Xantik", Server.GREEN)
    player.zone = "soldungb"

    class Api:
        def item_wiki(self, name: str, zone: str) -> str:
            assert (name, zone) == ("Lord Nagafen", "soldungb")
            return MOB_PAGE

        def item_prices(self, server: int, names: list[str]) -> list[ItemPrice]:
            assert "Cloak of Flames" in names
            return [
                ItemPrice.model_validate(
                    {
                        "eQitemId": 1234,
                        "itemName": "cloak of flames",  # case-insensitive match
                        "totalWTSLast6MonthsAverage": 8500,
                    }
                )
            ]

    state = MobInfoState()
    ConHandler(
        bus,
        player,
        load_zone_database(),
        mob_info=state,
        api=Api(),
        submit=ImmediateWorker().submit,
    )
    bus.publish(ConEvent(timestamp=T0, name="Lord Nagafen"))
    assert [e.name for e in state.loot][:2] == ["Cloak of Flames", "Dragon Scales"]
    assert state.loot[0].price == "8500"
    assert state.loot[0].price_url.endswith("/ItemDetails/1234")
    assert state.loot[1].price is None


def test_con_without_api_keeps_local_state_only() -> None:
    bus = EventBus()
    player = ActivePlayer()
    player.reset_for("Xantik", Server.GREEN)
    state = MobInfoState()
    ConHandler(bus, player, load_zone_database(), mob_info=state)
    bus.publish(ConEvent(timestamp=T0, name="Lord Nagafen"))
    assert state.name == "Lord Nagafen"
    assert state.loot == []
