"""PlayerProfileHandler — keeps ActivePlayer and the saved PlayerInfo in sync.

Ports three EQTool behaviors that previously had no home here:

* On character switch (AfterPlayerChangedEvent), the persistent per-character
  profile (Settings.players) is loaded into the live ActivePlayer — class,
  level, zone, tracking skill (EQTool loads PlayerInfo the same way on log
  selection).
* PlayerClassDetectedHandler.cs: a detected class fills the profile only when
  no class is set yet (never overwrites a user-chosen class).
* PlayerLevelDetectionHandler.cs: a detected level only ever raises the known
  level.

Zone changes persist into the profile too (EQTool's YouZonedHandler saves on
zone change; our handlers.you_zoned keeps owning ActivePlayer.zone).

Runs on the driver thread; persistence goes through ``request_save`` (the
app's DebouncedSaver.request_save — thread-safe, atomic write).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from nparseplus.config.settings import get_player
from nparseplus.core.bus import EventBus
from nparseplus.core.enums import PlayerClass
from nparseplus.core.events import (
    AfterPlayerChangedEvent,
    ClassDetectedEvent,
    PlayerLevelDetectionEvent,
    YouZonedEvent,
)
from nparseplus.core.player import ActivePlayer

if TYPE_CHECKING:
    from nparseplus.config.settings import PlayerInfo, Settings


class PlayerProfileHandler:
    def __init__(
        self,
        bus: EventBus,
        player: ActivePlayer,
        settings: Settings,
        request_save: Callable[[], None] | None = None,
    ) -> None:
        self.player = player
        self.settings = settings
        self._request_save = request_save
        bus.subscribe(AfterPlayerChangedEvent, self._on_player_changed)
        bus.subscribe(ClassDetectedEvent, self._on_class_detected)
        bus.subscribe(PlayerLevelDetectionEvent, self._on_level_detected)
        bus.subscribe(YouZonedEvent, self._on_zoned)

    def _profile(self) -> PlayerInfo | None:
        server_key = self.player.server_key
        if server_key is None or not self.player.name:
            return None
        return get_player(self.settings, self.player.name, server_key)

    def _save(self) -> None:
        if self._request_save is not None:
            self._request_save()

    def _on_player_changed(self, _event: AfterPlayerChangedEvent) -> None:
        info = self._profile()
        if info is None:
            return
        if info.player_class is not None:
            self.player.player_class = PlayerClass(info.player_class)
        if info.level is not None:
            self.player.level = info.level
        if info.zone:
            self.player.zone = info.zone
        if info.tracking_skill:
            self.player.tracking_skill = info.tracking_skill
        if info.guild_name:
            self.player.guild_name = info.guild_name

    def _on_class_detected(self, event: ClassDetectedEvent) -> None:
        # C#: only fills an unset class; a user-chosen class is never clobbered.
        if self.player.player_class is not None:
            return
        self.player.player_class = event.player_class
        info = self._profile()
        if info is not None and info.player_class is None:
            info.player_class = int(event.player_class)
            self._save()

    def _on_level_detected(self, event: PlayerLevelDetectionEvent) -> None:
        # C#: a detected level only ever raises the known level.
        if self.player.level is not None and self.player.level >= event.player_level:
            return
        self.player.level = event.player_level
        info = self._profile()
        if info is not None:
            info.level = event.player_level
            self._save()

    def _on_zoned(self, event: YouZonedEvent) -> None:
        info = self._profile()
        if info is not None and info.zone != event.short_name:
            info.zone = event.short_name
            self._save()
