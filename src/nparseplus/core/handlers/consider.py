"""ConHandler — records the last-considered mob for the MobInfo UI.

Port of EQTool's Services/Handlers/ConHandler.cs. Local state (name, pet
flag, spawn time, notable flag) updates synchronously; a con then enriches
that state from two independent sources, each on its own net-worker job:

* **PigParse** (``api/item/wiki`` + ``api/item/postmultiple``) — the
  ``known_loot`` field priced at the 6-month WTS average, like the C#. Gated
  by sharing, because it is pigparse.org's API.
* **wiki.project1999.com directly** (``net.p99wiki``) — the whole NPC page:
  stat block, spawn location, factions, drops, picture (#113). Gated by
  ``mobinfo.wiki_details``, not by sharing: the wiki is not pigparse's, and
  a player who shares no location still wants to know what they conned.

They are two jobs rather than one because they are two failures: a wiki
timeout must not hold the prices back, and each has its own gate. Both
``apply`` halves land on the driver thread, so :func:`merge_loot` sees
whatever the other leg has delivered so far without a lock.

Deliberate divergence: the C# captures the *previous* mob's loot list
before fetching and prices that (a race in ConHandler.cs); we price the
loot parsed from the fetched page itself, which is what the display
expects.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from nparseplus.core.bus import EventBus
from nparseplus.core.events import ConEvent
from nparseplus.core.handlers.base import BaseHandler
from nparseplus.core.pets import PlayerPet
from nparseplus.core.pigparse import PigParseApi, SubmitFn
from nparseplus.core.player import ActivePlayer
from nparseplus.core.wiki import WikiLookup
from nparseplus.core.zones import ZoneDatabase

if TYPE_CHECKING:
    from nparseplus.net.p99wiki import WikiDrop, WikiNpc

WIKI_BASE = "https://wiki.project1999.com"
PIGPARSE_ITEM_URL = "https://pigparse.azurewebsites.net/ItemDetails"

# The known_loot template field runs until the next top-level "|field" line.
_KNOWN_LOOT_FIELD = re.compile(
    r"^\|\s*known_loot\s*=\s*(?P<value>.*?)(?=^\|\s*\w+\s*=|\Z)",
    re.MULTILINE | re.DOTALL,
)
# "{{ :Item Name }}" transclusions and "[[Item Name]]" links, one per line.
_TRANSCLUSION = re.compile(r"\{\{\s*:?\s*(?P<name>[^}|]+?)\s*\}\}")
_WIKI_LINK = re.compile(r"\[\[(?:[^|\]]*\|)?(?P<name>[^\]]+)\]\]")


@dataclass
class LootPrice:
    """One known-loot row (PricingUriViewModel)."""

    name: str
    url: str
    price: str | None = None
    price_url: str | None = None
    #: What the wiki says about how often it drops ("Rare", "Always"). Only
    #: the wiki leg fills this; PigParse's answer carries no rarity.
    rarity: str = ""

    @property
    def has_price(self) -> bool:
        """PigParse answers "0" for an item nobody has traded — not a price."""
        return bool(self.price) and self.price != "0"


def merge_loot(priced: Sequence[LootPrice], drops: Sequence[WikiDrop]) -> list[LootPrice]:
    """One drop list out of the two sources, keyed on the case-folded name.

    THE MERGE RULE (#113), because the two lists overlap almost entirely:

    * **PigParse pricing wins.** A row that both sources know keeps the
      PigParse price and URL and gains the wiki's rarity. What an item sells
      for is the one thing the wiki cannot tell you, so it is never dropped
      in favour of a wiki row.
    * **Wiki drops fill the gaps** — items PigParse has never seen, and the
      case that matters most, sharing turned off, where the window has no
      loot list at all otherwise.
    * **Priced rows sort first**, then the rest, each keeping page order
      within its group. The window clips the list, so ordering decides what
      survives the clip and the useful rows should be what does.
    """
    out: list[LootPrice] = []
    by_key: dict[str, LootPrice] = {}
    for entry in priced:
        key = entry.name.casefold()
        if key in by_key:
            continue
        by_key[key] = entry
        out.append(entry)
    for drop in drops:
        key = drop.name.casefold()
        known = by_key.get(key)
        if known is not None:
            known.rarity = known.rarity or drop.rarity
            known.url = known.url or drop.url
            continue
        entry = LootPrice(
            name=drop.name,
            url=drop.url or f"{WIKI_BASE}/{drop.name.replace(' ', '_')}",
            rarity=drop.rarity,
        )
        by_key[key] = entry
        out.append(entry)
    out.sort(key=lambda entry: 0 if entry.has_price else 1)  # stable: order kept
    return out


def parse_known_loot(wikitext: str) -> list[LootPrice]:
    """Item names from the mob page's known_loot field (ParseKnownLoot)."""
    match = _KNOWN_LOOT_FIELD.search(wikitext)
    if match is None:
        return []
    loot: list[LootPrice] = []
    seen: set[str] = set()
    for line in match.group("value").splitlines():
        if "casts:" in line.lower():
            continue
        hit = _TRANSCLUSION.search(line) or _WIKI_LINK.search(line)
        if hit is None:
            continue
        name = hit.group("name").strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        loot.append(LootPrice(name=name, url=f"{WIKI_BASE}/{name.replace(' ', '_')}"))
    return loot


@dataclass
class MobInfoState:
    """The last-considered mob (MobInfoViewModel's Qt-free core)."""

    name: str = ""
    zone: str = ""
    is_pet: bool = False
    spawn_seconds: int | None = None
    is_notable: bool = False
    loot: list[LootPrice] = field(default_factory=list)
    #: The mob's P99 wiki page, once the lookup lands. ``None`` while it is
    #: in flight, when the lookup is off, and for a page that does not exist.
    wiki: WikiNpc | None = None
    on_change: list[Callable[[MobInfoState], None]] = field(default_factory=list)

    def _notify(self) -> None:
        for callback in list(self.on_change):
            callback(self)


class ConHandler(BaseHandler):
    def __init__(
        self,
        bus: EventBus,
        player: ActivePlayer,
        zones: ZoneDatabase,
        player_pet: PlayerPet | None = None,
        mob_info: MobInfoState | None = None,
        api: PigParseApi | None = None,
        submit: SubmitFn | None = None,
        wiki: WikiLookup | None = None,
        wiki_submit: SubmitFn | None = None,
        want_image: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__(bus, player)
        self.zones = zones
        self.player_pet = player_pet
        self.mob_info = mob_info if mob_info is not None else MobInfoState()
        self.api = api
        self.submit = submit
        # The wiki leg's own handles: `submit` above is the sharing-gated one
        # (composition wraps it), and the wiki is not sharing's to gate.
        self.wiki = wiki
        self.wiki_submit = wiki_submit
        #: Read at fetch time, not at construction — the picture is a live
        #: setting and a running session must follow it.
        self.want_image = want_image if want_image is not None else (lambda: True)
        # The priced half, kept so the wiki half can merge against it (and
        # vice versa) whichever lands first. Driver-thread only.
        self._priced_for = ""
        self._priced: list[LootPrice] = []
        bus.subscribe(ConEvent, self._on_con)

    def _on_con(self, event: ConEvent) -> None:
        info = self.mob_info
        if self.player_pet is not None and event.name == self.player_pet.pet_name:
            info.is_pet = True
            info.name = event.name
            info.zone = self.player.zone
            info.spawn_seconds = None
            info.is_notable = False
            info._notify()
            return

        if event.name == info.name and not info.is_pet:
            return  # C# skips the refetch when the same mob is conned again

        zone = self.zones.get(self.player.zone) if self.player.zone else None
        notable = zone is not None and any(
            npc.casefold() == event.name.casefold() for npc in zone.notable_npcs
        )
        info.is_pet = False
        info.name = event.name
        info.zone = self.player.zone
        info.spawn_seconds = self.zones.spawn_time(event.name, self.player.zone)
        info.is_notable = notable
        info.loot = []
        info.wiki = None
        self._priced_for = ""
        self._priced = []
        info._notify()
        self._enrich(event.name)

    def _enrich(self, name: str) -> None:
        self._enrich_prices(name)
        self._enrich_wiki(name)

    def _enrich_prices(self, name: str) -> None:
        """Fetch known loot + prices off-thread; apply if still displayed."""
        api, submit, server = self.api, self.submit, self.player.server
        if api is None or submit is None:
            return
        zone = self.player.zone

        def fetch() -> list[LootPrice]:
            markup = api.item_wiki(name, zone)
            loot = parse_known_loot(markup) if markup else []
            if loot and server is not None:
                prices = api.item_prices(int(server), [entry.name for entry in loot])
                by_name = {item.item_name.casefold(): item for item in prices}
                for entry in loot:
                    item = by_name.get(entry.name.casefold())
                    if item is not None:
                        entry.price = str(item.total_wts_last_6_months_average)
                        entry.price_url = f"{PIGPARSE_ITEM_URL}/{item.eq_item_id}"
            return loot

        def apply(loot: list[LootPrice]) -> None:
            if self.mob_info.name != name:
                return
            self._priced_for, self._priced = name, loot
            self._republish()

        submit(fetch, apply)

    def _enrich_wiki(self, name: str) -> None:
        """Fetch the mob's whole wiki page off-thread (#113)."""
        wiki, submit = self.wiki, self.wiki_submit
        if wiki is None or submit is None:
            return
        want_image = self.want_image

        def fetch() -> WikiNpc | None:
            return wiki.npc(name, with_image=want_image())

        def apply(npc: WikiNpc | None) -> None:
            if self.mob_info.name != name:
                return
            self.mob_info.wiki = npc
            self._republish()

        submit(fetch, apply)

    def _republish(self) -> None:
        """Re-merge both legs' answers and tell the window. Driver thread."""
        info = self.mob_info
        priced = self._priced if self._priced_for == info.name else []
        wiki = info.wiki
        info.loot = merge_loot(priced, wiki.drops if wiki is not None else ())
        info._notify()
