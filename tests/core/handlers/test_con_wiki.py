"""ConHandler's wiki leg and the drop-list merge (#113).

The window's loot list has two feeders — PigParse (priced, gated by sharing)
and the P99 wiki page (rarity + the drops PigParse has never seen, not gated
by sharing at all). These pin the merge rule and the fact that either leg
works on its own.
"""

from __future__ import annotations

from datetime import datetime

from nparseplus.core.bus import EventBus
from nparseplus.core.enums import Server
from nparseplus.core.events import ConEvent
from nparseplus.core.handlers.consider import ConHandler, LootPrice, MobInfoState, merge_loot
from nparseplus.core.player import ActivePlayer
from nparseplus.core.zones import load_zone_database
from nparseplus.net.p99wiki import WikiDrop, WikiLookupResult, WikiNpc
from nparseplus.net.pigparse_models import ItemPrice
from nparseplus.net.worker import ImmediateWorker

T0 = datetime(2026, 7, 8, 12, 0, 0)

MOB_PAGE = """{{ Mobpage
| name = Lord Nagafen
| known_loot =
*{{:Cloak of Flames}}<br>
*{{:Red Scales}}<br>
}}"""


def _npc(drops: tuple[WikiDrop, ...] = (), **kwargs) -> WikiNpc:
    return WikiNpc(title="Lord Nagafen", name="Lord Nagafen", drops=drops, **kwargs)


class FakeWiki:
    """A WikiLookup that answers from memory and records how it was asked."""

    def __init__(self, answer: WikiNpc | None) -> None:
        self.answer = answer
        self.calls: list[tuple[str, bool]] = []

    def lookup(self, title: str, *, with_image: bool = False) -> WikiLookupResult:
        self.calls.append((title, with_image))
        return WikiLookupResult(npc=self.answer, status="ok" if self.answer else "missing")


class FakeApi:
    def __init__(self, markup: str = MOB_PAGE, prices: list[ItemPrice] | None = None) -> None:
        self.markup = markup
        self.prices = prices or []

    def item_wiki(self, name: str, zone: str) -> str:
        return self.markup

    def item_prices(self, server: int, names: list[str]) -> list[ItemPrice]:
        return self.prices


def _price(name: str, value: int, item_id: int = 1) -> ItemPrice:
    return ItemPrice(
        ItemName=name,
        EQitemId=item_id,
        TotalWTSLast6MonthsAverage=value,
    )


def _handler(**kwargs) -> tuple[EventBus, ConHandler]:
    bus = EventBus()
    player = ActivePlayer()
    player.reset_for("Xantik", Server.GREEN)
    player.zone = "soldungb"
    handler = ConHandler(bus, player, load_zone_database(), **kwargs)
    return bus, handler


# --- the merge rule -------------------------------------------------------------


def test_a_priced_row_keeps_its_price_and_gains_the_wikis_rarity() -> None:
    priced = [LootPrice(name="Cloak of Flames", url="u", price="42000", price_url="p")]
    drops = (WikiDrop(name="cloak of flames", url="wiki", rarity="Ultra Rare"),)
    merged = merge_loot(priced, drops)
    assert len(merged) == 1  # case-folded name is the identity
    assert merged[0].price == "42000" and merged[0].price_url == "p"
    assert merged[0].rarity == "Ultra Rare"
    assert merged[0].url == "u"  # the priced row's URL wins


def test_wiki_only_drops_fill_the_gaps() -> None:
    priced = [LootPrice(name="Cloak of Flames", url="u", price="42000")]
    drops = (WikiDrop(name="Red Scales", url="wiki/Red_Scales", rarity="Common"),)
    merged = merge_loot(priced, drops)
    assert [entry.name for entry in merged] == ["Cloak of Flames", "Red Scales"]
    assert merged[1].url == "wiki/Red_Scales" and merged[1].price is None


def test_priced_rows_sort_first_and_keep_page_order_within_the_group() -> None:
    priced = [
        LootPrice(name="Nothing Traded", url="u", price="0"),  # "0" is not a price
        LootPrice(name="Cloak of Flames", url="u", price="42000"),
    ]
    drops = (
        WikiDrop(name="Wurm Meat", url="w1"),
        WikiDrop(name="Red Scales", url="w2"),
    )
    assert [entry.name for entry in merge_loot(priced, drops)] == [
        "Cloak of Flames",
        "Nothing Traded",
        "Wurm Meat",
        "Red Scales",
    ]


def test_the_wiki_alone_is_a_perfectly_good_loot_list() -> None:
    drops = (WikiDrop(name="Red Scales", url="w", rarity="Common"),)
    merged = merge_loot([], drops)
    assert [(e.name, e.rarity, e.has_price) for e in merged] == [("Red Scales", "Common", False)]


def test_a_wiki_drop_with_no_url_gets_one() -> None:
    merged = merge_loot([], (WikiDrop(name="Red Scales"),))
    assert merged[0].url == "https://wiki.project1999.com/Red_Scales"


# --- the handler ----------------------------------------------------------------


def test_a_con_fetches_the_page_and_publishes_it() -> None:
    wiki = FakeWiki(_npc(drops=(WikiDrop(name="Red Scales", rarity="Common"),), hp="42000"))
    state = MobInfoState()
    bus, _ = _handler(mob_info=state, wiki=wiki, wiki_submit=ImmediateWorker().submit)
    bus.publish(ConEvent(timestamp=T0, name="Lord Nagafen"))
    assert state.wiki is not None and state.wiki.hp == "42000"
    assert [entry.name for entry in state.loot] == ["Red Scales"]
    assert wiki.calls == [("Lord Nagafen", True)]  # picture on by default


def test_the_picture_is_asked_for_live() -> None:
    wiki = FakeWiki(_npc())
    wanted = [False]
    bus, _h = _handler(
        wiki=wiki,
        wiki_submit=ImmediateWorker().submit,
        want_image=lambda: wanted[0],
    )
    bus.publish(ConEvent(timestamp=T0, name="Lord Nagafen"))
    wanted[0] = True
    bus.publish(ConEvent(timestamp=T0, name="Vilefang"))
    assert wiki.calls == [("Lord Nagafen", False), ("Vilefang", True)]


def test_both_legs_merge_whichever_lands_second() -> None:
    wiki = FakeWiki(
        _npc(
            drops=(
                WikiDrop(name="Cloak of Flames", rarity="Ultra Rare"),
                WikiDrop(name="Wurm Meat", rarity="Common"),
            )
        )
    )
    state = MobInfoState()
    bus, _h = _handler(
        mob_info=state,
        wiki=wiki,
        wiki_submit=ImmediateWorker().submit,
        api=FakeApi(prices=[_price("Cloak of Flames", 42000)]),
        submit=ImmediateWorker().submit,
    )
    bus.publish(ConEvent(timestamp=T0, name="Lord Nagafen"))
    rows = {entry.name: entry for entry in state.loot}
    assert rows["Cloak of Flames"].price == "42000"
    assert rows["Cloak of Flames"].rarity == "Ultra Rare"  # the wiki's half
    assert rows["Wurm Meat"].price is None  # PigParse never saw it
    assert state.loot[0].name == "Cloak of Flames"  # priced first


def test_the_prices_survive_a_wiki_page_that_does_not_exist() -> None:
    state = MobInfoState()
    bus, _h = _handler(
        mob_info=state,
        wiki=FakeWiki(None),
        wiki_submit=ImmediateWorker().submit,
        api=FakeApi(prices=[_price("Cloak of Flames", 42000)]),
        submit=ImmediateWorker().submit,
    )
    bus.publish(ConEvent(timestamp=T0, name="Lord Nagafen"))
    assert state.wiki is None
    assert [entry.name for entry in state.loot] == ["Cloak of Flames", "Red Scales"]


def test_conning_a_second_mob_clears_the_first_ones_page() -> None:
    state = MobInfoState()
    wiki = FakeWiki(_npc(hp="42000"))
    bus, _h = _handler(mob_info=state, wiki=wiki, wiki_submit=ImmediateWorker().submit)
    bus.publish(ConEvent(timestamp=T0, name="Lord Nagafen"))
    assert state.wiki is not None
    wiki.answer = None
    bus.publish(ConEvent(timestamp=T0, name="a lava basilisk"))
    assert state.wiki is None and state.loot == []


def test_no_wiki_lookup_configured_is_a_no_op() -> None:
    state = MobInfoState()
    bus, _h = _handler(mob_info=state)
    bus.publish(ConEvent(timestamp=T0, name="Lord Nagafen"))
    assert state.name == "Lord Nagafen" and state.wiki is None


def test_a_pet_is_never_looked_up() -> None:
    from nparseplus.core.pets import PlayerPet

    pet = PlayerPet()
    pet.pet_name = "Vexer"
    wiki = FakeWiki(_npc())
    bus, _h = _handler(player_pet=pet, wiki=wiki, wiki_submit=ImmediateWorker().submit)
    bus.publish(ConEvent(timestamp=T0, name="Vexer"))
    assert wiki.calls == []


def test_an_unreachable_wiki_is_recorded_separately_from_a_missing_page() -> None:
    class UnreachableWiki(FakeWiki):
        def lookup(self, title: str, *, with_image: bool = False) -> WikiLookupResult:
            self.calls.append((title, with_image))
            return WikiLookupResult(status="unreachable")

    state = MobInfoState()
    bus, _h = _handler(
        mob_info=state, wiki=UnreachableWiki(None), wiki_submit=ImmediateWorker().submit
    )
    bus.publish(ConEvent(timestamp=T0, name="Lord Nagafen"))
    assert state.wiki is None
    assert state.wiki_unreachable is True


def test_reconsidering_an_unreachable_mob_retries_its_wiki_lookup() -> None:
    class RecoveringWiki(FakeWiki):
        def lookup(self, title: str, *, with_image: bool = False) -> WikiLookupResult:
            self.calls.append((title, with_image))
            if len(self.calls) == 1:
                return WikiLookupResult(status="unreachable")
            return WikiLookupResult(npc=_npc(hp="42000"), status="ok")

    state = MobInfoState()
    wiki = RecoveringWiki(None)
    bus, _h = _handler(mob_info=state, wiki=wiki, wiki_submit=ImmediateWorker().submit)
    event = ConEvent(timestamp=T0, name="Lord Nagafen")
    bus.publish(event)
    assert state.wiki_unreachable is True

    bus.publish(event)
    assert len(wiki.calls) == 2
    assert state.wiki is not None and state.wiki.hp == "42000"
    assert state.wiki_unreachable is False
