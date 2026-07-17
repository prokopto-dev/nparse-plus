"""TimerPersistenceHandler — timers survive camping and app restarts.

Two per-character stores in the profile (Settings.players):

* ``you_spells`` — EQTool's YouSpells save (ClearYouSpells/AddSavedYouSpells).
  The plumbing existed in TimersService since M1 but was never wired. Buff
  clocks freeze while camped, so seconds-left is stored and re-anchored on
  restore.
* ``respawn_timers`` — nparseplus addition (nparse #57; EQTool loses these).
  Respawns keep counting in real time, so absolute naive-local end times are
  stored and anything that popped while away is dropped on restore.

Export runs on every TimersService change (the DebouncedSaver coalesces) and
once more on BeforePlayerChangedEvent / Backend.stop so seconds-left is
computed at the moment of camp/quit, not at the last row change. Restore runs
on AfterPlayerChangedEvent — after PlayerProfileHandler (subscribed first in
composition) has loaded the profile's class/level, which the you-spell
duration math needs.

Runs on the driver thread; Backend.stop calls ``export_now`` only after the
driver thread is joined (TimersService is not thread-safe).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING

from nparseplus.config.settings import SavedTimer, YouSpell, get_player
from nparseplus.core.bus import EventBus
from nparseplus.core.events import AfterPlayerChangedEvent, BeforePlayerChangedEvent
from nparseplus.core.handlers.spawn_timer import CUSTOM_TIMER_GROUP
from nparseplus.core.player import ActivePlayer
from nparseplus.core.spells.spells_us import SpellBook
from nparseplus.core.timers import RespawnTimerSnapshot, TimersService, YouSpellSnapshot

if TYPE_CHECKING:
    from nparseplus.config.settings import PlayerInfo, Settings


class TimerPersistenceHandler:
    def __init__(
        self,
        bus: EventBus,
        player: ActivePlayer,
        settings: Settings,
        timers: TimersService,
        spells: SpellBook,
        request_save: Callable[[], None] | None = None,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.player = player
        self.settings = settings
        self.timers = timers
        self.spells = spells
        self._request_save = request_save
        self._clock = clock
        self._restoring = False
        bus.subscribe(BeforePlayerChangedEvent, self._on_before_player_changed)
        bus.subscribe(AfterPlayerChangedEvent, self._on_after_player_changed)
        timers.on_change.append(self._on_timers_changed)

    # -- export ------------------------------------------------------------------

    def _profile(self) -> PlayerInfo | None:
        server_key = self.player.server_key
        if server_key is None or not self.player.name:
            return None
        return get_player(self.settings, self.player.name, server_key)

    def _on_timers_changed(self) -> None:
        if not self._restoring:
            self.export_now()

    def export_now(self) -> None:
        """Snapshot both stores into the active character's profile."""
        info = self._profile()
        if info is None:
            return
        now = self._clock()
        info.you_spells = [
            YouSpell(name=snap.name, seconds_left=snap.total_seconds_left)
            for snap in self.timers.export_you_spells(now)
        ]
        info.respawn_timers = [
            SavedTimer(name=snap.name, ends_at=snap.ends_at, total_duration_s=snap.total_duration_s)
            for snap in self.timers.export_respawn_timers(CUSTOM_TIMER_GROUP, now)
        ]
        if self._request_save is not None:
            self._request_save()

    # -- restore -----------------------------------------------------------------

    def _on_before_player_changed(self, _event: BeforePlayerChangedEvent) -> None:
        # Final export for the outgoing character: seconds-left must be
        # computed at camp time, not at the last row change.
        if not self._restoring:
            self.export_now()

    def _on_after_player_changed(self, _event: AfterPlayerChangedEvent) -> None:
        info = self._profile()
        if info is None:
            return
        now = self._clock()
        self._restoring = True
        try:
            self.timers.clear_you_spells()
            self.timers.remove_group(CUSTOM_TIMER_GROUP)
            self.timers.restore_you_spells(
                [
                    YouSpellSnapshot(name=item.name, total_seconds_left=item.seconds_left)
                    for item in info.you_spells
                ],
                now,
                self.spells,
                player_class=self.player.player_class,
                player_level=self.player.level,
            )
            self.timers.restore_respawn_timers(
                [
                    RespawnTimerSnapshot(
                        name=item.name,
                        ends_at=item.ends_at,
                        total_duration_s=item.total_duration_s,
                    )
                    for item in info.respawn_timers
                ],
                CUSTOM_TIMER_GROUP,
                now,
            )
        finally:
            self._restoring = False
        # Re-sync the profile with what actually survived the restore.
        self.export_now()
