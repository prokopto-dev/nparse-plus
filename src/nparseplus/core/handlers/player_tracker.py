"""PlayerTrackerHandler — the /who roster + PigParse player upserts.

Port of EQTool's Services/PlayerTrackerService.cs: every /who row upserts a
session-wide roster (AllPlayers), feeds ``ActivePlayer.known_players`` (the
SlainHandler's player-vs-NPC discriminator), captures your own guild name,
and every 20 s syncs with PigParse — newly-learned facts are POSTed to
api/player/upsertplayers and classes for still-unknown names are fetched
back from api/player/getbynames.

Divergences from the C#:
- The C# backfills TargetClass onto live spell view models via the UI
  dispatcher; here the spell window asks ``get_class()`` at paint time.
- The roster clears on character change (the C# keeps it for the app
  lifetime): a character switch can change servers, and cross-server names
  must not leak into known_players or the upsert stream.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from nparseplus.core.bus import EventBus
from nparseplus.core.enums import PlayerClass
from nparseplus.core.events import AfterPlayerChangedEvent, WhoPlayerEvent, YouZonedEvent
from nparseplus.core.handlers.base import BaseHandler
from nparseplus.core.pigparse import PigParseApi, SubmitFn
from nparseplus.core.player import ActivePlayer

SYNC_INTERVAL_SECONDS = 20.0


@dataclass
class TrackedPlayer:
    name: str
    guild_name: str | None = None
    player_class: PlayerClass | None = None
    level: int | None = None


class PlayerTrackerHandler(BaseHandler):
    def __init__(
        self,
        bus: EventBus,
        player: ActivePlayer,
        api: PigParseApi | None = None,
        submit: SubmitFn | None = None,
    ) -> None:
        super().__init__(bus, player)
        self.api = api
        self.submit = submit
        self._all: dict[str, TrackedPlayer] = {}
        self._in_zone: dict[str, TrackedPlayer] = {}
        self._dirty: dict[str, TrackedPlayer] = {}
        self._last_sync: datetime | None = None
        bus.subscribe(WhoPlayerEvent, self._on_who_player)
        bus.subscribe(YouZonedEvent, self._on_zoned)
        bus.subscribe(AfterPlayerChangedEvent, self._on_player_changed)

    # -- roster queries ----------------------------------------------------------

    def is_player(self, name: str) -> bool:
        return bool(name) and (name == self.player.name or name.lower() in self._all)

    def get_player(self, name: str) -> TrackedPlayer | None:
        if not name or name.startswith(" "):
            return None
        return self._all.get(name.lower())

    def get_class(self, name: str) -> PlayerClass | None:
        entry = self.get_player(name)
        return entry.player_class if entry is not None else None

    def players_in_zone(self) -> list[TrackedPlayer]:
        return list(self._in_zone.values())

    # -- bus subscribers (driver thread) -------------------------------------------

    def _on_who_player(self, event: WhoPlayerEvent) -> None:
        who = event.player
        if self.player.name and who.name == self.player.name and who.guild_name:
            self.player.guild_name = who.guild_name

        key = who.name.lower()
        existing = self._all.get(key)
        if existing is not None:
            # C# merge: mark dirty when a non-null fact changed, but only
            # FILL missing fields (?? semantics) — never overwrite.
            changed = (
                (who.level is not None and existing.level != who.level)
                or (bool(who.guild_name) and existing.guild_name != who.guild_name)
                or (who.player_class is not None and existing.player_class != who.player_class)
            )
            if changed:
                self._dirty.setdefault(
                    key,
                    TrackedPlayer(
                        name=who.name,
                        guild_name=who.guild_name or None,
                        player_class=who.player_class,
                        level=who.level,
                    ),
                )
            existing.level = existing.level if existing.level is not None else who.level
            existing.guild_name = existing.guild_name or (who.guild_name or None)
            existing.player_class = (
                existing.player_class if existing.player_class is not None else who.player_class
            )
            entry = existing
        else:
            entry = TrackedPlayer(
                name=who.name,
                guild_name=who.guild_name or None,
                player_class=who.player_class,
                level=who.level,
            )
            self._all[key] = entry
            self._dirty.setdefault(key, replace(entry))

        self._in_zone.setdefault(key, entry)
        self.player.known_players.add(who.name)

    def _on_zoned(self, _event: YouZonedEvent) -> None:
        self._in_zone.clear()

    def _on_player_changed(self, _event: AfterPlayerChangedEvent) -> None:
        self._all.clear()
        self._in_zone.clear()
        self._dirty.clear()

    # -- 20s PigParse sync (driver tick) ---------------------------------------------

    def tick(self, now: datetime) -> None:
        api, submit, server = self.api, self.submit, self.player.server
        if api is None or submit is None or server is None:
            return
        if (
            self._last_sync is not None
            and (now - self._last_sync).total_seconds() < SYNC_INTERVAL_SECONDS
        ):
            return
        self._last_sync = now

        dirty = list(self._dirty.values())
        self._dirty.clear()
        unknown = sorted(p.name for p in self._all.values() if p.player_class is None)
        if not dirty and not unknown:
            return
        server_int = int(server)

        def fetch() -> list:
            api.upsert_players(dirty, server_int)
            return list(api.players_by_names(unknown, server_int)) if unknown else []

        def apply(records: list) -> None:
            for record in records:
                if record.player_class is None:
                    continue
                entry = self._all.get(record.name.lower())
                if entry is None:
                    entry = TrackedPlayer(name=record.name)
                    self._all[record.name.lower()] = entry
                if entry.player_class is None:
                    entry.player_class = PlayerClass(record.player_class)

        submit(fetch, apply)
