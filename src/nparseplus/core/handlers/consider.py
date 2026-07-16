"""ConHandler — records the last-considered mob for the MobInfo UI.

Port of EQTool's Services/Handlers/ConHandler.cs. Local state (name, pet
flag, spawn time, notable flag) updates synchronously; with the network
layer available a con also fetches the mob's P99 wiki markup through
PigParse (``api/item/wiki``), parses the ``known_loot`` template field
(MobInfoParsing.ParseKnownLoot), and prices the drops via
``api/item/postmultiple`` — the 6-month WTS average, like the C#.

Deliberate divergence: the C# captures the *previous* mob's loot list
before fetching and prices that (a race in ConHandler.cs); we price the
loot parsed from the fetched page itself, which is what the display
expects.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from nparseplus.core.bus import EventBus
from nparseplus.core.events import ConEvent
from nparseplus.core.handlers.base import BaseHandler
from nparseplus.core.pets import PlayerPet
from nparseplus.core.pigparse import PigParseApi, SubmitFn
from nparseplus.core.player import ActivePlayer
from nparseplus.core.zones import ZoneDatabase

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
    ) -> None:
        super().__init__(bus, player)
        self.zones = zones
        self.player_pet = player_pet
        self.mob_info = mob_info if mob_info is not None else MobInfoState()
        self.api = api
        self.submit = submit
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
        info._notify()
        self._enrich(event.name)

    def _enrich(self, name: str) -> None:
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
            if self.mob_info.name == name:
                self.mob_info.loot = loot
                self.mob_info._notify()

        submit(fetch, apply)
