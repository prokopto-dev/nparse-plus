"""TimerPersistenceHandler — timers survive camping, relogging and restarts.

Four per-character stores in the profile (Settings.players), split by whether
the clock behind the row keeps running while the character is not logged in:

* ``you_spells`` — EQTool's YouSpells save (ClearYouSpells/AddSavedYouSpells).
  Buff clocks **freeze** while you are away, so seconds-left is stored and
  re-anchored on restore.
* ``you_cooldowns`` — YOU_GROUP reuse timers (#120): Lay on Hands, Harm Touch,
  mend, disciplines, spell recasts, memorize. These **keep counting** in the
  real world, so absolute ends are stored and anything that came up while away
  is dropped on restore.
* ``you_counters`` — YOU_GROUP tallies (bard song counts, #120). No end time;
  their idle expiry runs in real time, so the last-updated stamp is stored.
* ``respawn_timers`` — nparseplus addition (nparse #57; EQTool loses these).
  Respawns keep counting, so absolute ends are stored. A variable respawn
  ("pop") window (#125) rides in the same store: both of its anchors are
  absolute too, and ``window_opened_at`` comes back exactly as saved, since
  the restore below runs on every character swap.

Camping (#120) exports, then **removes only this character's rows** — never
via ``clear_you_spells``, which drops every YOU_GROUP row while
``restore_you_spells`` only re-adds buffs; boats, roll windows, custom/shared
timers and mob respawns are world state and stay visible and counting.
``WelcomeEvent`` ("Welcome to EverQuest!") is the counterpart: whatever
YOU_GROUP rows are on screen belong to a session that has ended, so they are
dropped and the stores are restored, re-anchored at the login moment. That
also covers the app running straight through a linkdead and relog.

``_camped`` suppresses export for exactly the reason ``_restoring`` does:
export runs on **every** TimersService change, so removing the rows on camp
would immediately re-export an empty set over the snapshot just taken. It is
checked inside ``export_now`` so BeforePlayerChangedEvent and ``Backend.stop``
respect it too — camp-then-quit must not lose the buffs.

**Seconds-left is measured against the last log line, not the wall clock.**
Linkdead emits nothing at all — the client simply stops writing — so with a
wall-clock anchor the app kept draining the rows, kept exporting, and erased
the very buffs it exists to save. The log's own clock is already the authority
in this pipeline, so a log that stopped growing freezes the snapshot at the
moment the player actually went away; and a change that the log did not
explain (a row the wall clock drained out from under a client that is no
longer there) does not rewrite the store at all. Camp gets this for free — the
camp line is itself a fresh timestamp. Known limit: a player idle in a silent
zone has a stale anchor, so a linkdead after long silence restores slightly
generously. That errs in the right direction and is bounded by the silence.

Runs on the driver thread — including the camp path, which is why
``CampParser`` resolves its delay on the driver tick instead of a timer thread
(``core/parsers/camp.py``). ``Backend.stop`` calls ``export_now`` only after
the driver thread is joined (TimersService is not thread-safe).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING

from nparseplus.config.settings import SavedCooldown, SavedCounter, SavedTimer, YouSpell, get_player
from nparseplus.core.bus import EventBus
from nparseplus.core.events import (
    AfterPlayerChangedEvent,
    BeforePlayerChangedEvent,
    CampEvent,
    WelcomeEvent,
)
from nparseplus.core.player import ActivePlayer
from nparseplus.core.spells.spells_us import SpellBook
from nparseplus.core.timers import (
    MOB_TIMER_GROUP,
    RespawnTimerSnapshot,
    SelfCooldownSnapshot,
    SelfCounterSnapshot,
    TimersService,
    YouSpellSnapshot,
)

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
        log_clock: Callable[[], datetime | None] | None = None,
    ) -> None:
        self.player = player
        self.settings = settings
        self.timers = timers
        self.spells = spells
        self._request_save = request_save
        self._clock = clock
        # The last log-line timestamp (LogPipeline.last_entry_time). Export
        # measures seconds-left against it; see the module docstring.
        self._log_clock = log_clock
        self._restoring = False
        self._camped = False
        self._last_export_anchor: datetime | None = None
        bus.subscribe(BeforePlayerChangedEvent, self._on_before_player_changed)
        bus.subscribe(AfterPlayerChangedEvent, self._on_after_player_changed)
        bus.subscribe(CampEvent, self._on_camp)
        bus.subscribe(WelcomeEvent, self._on_welcome)
        timers.on_change.append(self._on_timers_changed)

    # -- export ------------------------------------------------------------------

    def _profile(self) -> PlayerInfo | None:
        server_key = self.player.server_key
        if server_key is None or not self.player.name:
            return None
        return get_player(self.settings, self.player.name, server_key)

    def _export_anchor(self) -> datetime | None:
        """The log's own clock, or None until a line has been seen."""
        return self._log_clock() if self._log_clock is not None else None

    def _on_timers_changed(self) -> None:
        if self._restoring or self._camped:
            return
        anchor = self._export_anchor()
        if anchor is not None and anchor == self._last_export_anchor:
            # The log has said nothing since the last export, so this change is
            # the wall clock draining rows — possibly out from under a client
            # that went linkdead. Leave the snapshot frozen where the log left
            # it. Harmless during play: the store is a full snapshot, so the
            # next line re-exports whatever happened in this second.
            return
        self.export_now()

    def export_now(self) -> None:
        """Snapshot the four stores into the active character's profile.

        A no-op mid-restore, and a no-op while camped: the camped snapshot was
        taken at camp time and the rows are already off screen, so every later
        caller (on_change, BeforePlayerChangedEvent, ``Backend.stop``) would only
        overwrite it with nothing.
        """
        if self._restoring or self._camped:
            return
        info = self._profile()
        if info is None:
            return
        # Buff seconds-left is measured against the log; the absolute-end
        # stores only use it to drop what has already run out, so the same
        # anchor is right for both.
        now = self._export_anchor() or self._clock()
        info.you_spells = [
            YouSpell(name=snap.name, seconds_left=snap.total_seconds_left)
            for snap in self.timers.export_you_spells(now)
        ]
        info.you_cooldowns = [
            SavedCooldown(
                name=snap.name,
                ends_at=snap.ends_at,
                total_duration_s=snap.total_duration_s,
                spell_name=snap.spell_name,
            )
            for snap in self.timers.export_self_cooldowns(now)
        ]
        info.you_counters = [
            SavedCounter(name=snap.name, count=snap.count, updated_at=snap.updated_at)
            for snap in self.timers.export_self_counters()
        ]
        info.respawn_timers = [
            SavedTimer(
                name=snap.name,
                ends_at=snap.ends_at,
                total_duration_s=snap.total_duration_s,
                window_ends_at=snap.window_ends_at,
                window_opened_at=snap.window_opened_at,
            )
            for snap in self.timers.export_respawn_timers(MOB_TIMER_GROUP, now)
        ]
        self._last_export_anchor = self._export_anchor()
        if self._request_save is not None:
            self._request_save()

    # -- camp / login ------------------------------------------------------------

    def _on_camp(self, _event: CampEvent) -> None:
        """Save this character's rows and take them off screen.

        Runs on the driver thread (CampParser resolves its delay on the tick),
        so touching TimersService here is safe.
        """
        if self._profile() is None:
            # Nowhere to save them, so nothing may be hidden either — there
            # would be no way back.
            return
        self.export_now()
        # Before the removal, or its on_change would export the empty set
        # straight over the snapshot above.
        self._camped = True
        self.timers.remove_self_rows()

    def _on_welcome(self, _event: WelcomeEvent) -> None:
        """Logged in: drop the previous session's rows and restore the stores."""
        info = self._profile()
        if info is None:
            return
        now = self._clock()
        self._restoring = True
        try:
            self.timers.remove_self_rows()
            self._restore_self_rows(info, now)
        finally:
            self._restoring = False
        # Only now: a restore that blew up must leave the snapshot alone rather
        # than let the next change overwrite it with what survived.
        self._camped = False
        # Re-sync: buffs are now re-anchored, and anything that came up while
        # away is gone.
        self.export_now()

    def _restore_self_rows(self, info: PlayerInfo, now: datetime) -> None:
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
        self.timers.restore_self_cooldowns(
            [
                SelfCooldownSnapshot(
                    name=item.name,
                    ends_at=item.ends_at,
                    total_duration_s=item.total_duration_s,
                    spell_name=item.spell_name,
                )
                for item in info.you_cooldowns
            ],
            now,
            self.spells,
        )
        self.timers.restore_self_counters(
            [
                SelfCounterSnapshot(name=item.name, count=item.count, updated_at=item.updated_at)
                for item in info.you_counters
            ],
            now,
        )

    # -- restore -----------------------------------------------------------------

    def _on_before_player_changed(self, _event: BeforePlayerChangedEvent) -> None:
        # Final export for the outgoing character: seconds-left must be
        # computed at the moment they left, not at the last row change.
        # A no-op while camped — that snapshot was taken at camp time.
        self.export_now()

    def _on_after_player_changed(self, _event: AfterPlayerChangedEvent) -> None:
        info = self._profile()
        if info is None:
            return
        now = self._clock()
        self._restoring = True
        try:
            self.timers.remove_self_rows()
            self.timers.remove_group(MOB_TIMER_GROUP)
            self._restore_self_rows(info, now)
            self.timers.restore_respawn_timers(
                [
                    RespawnTimerSnapshot(
                        name=item.name,
                        ends_at=item.ends_at,
                        total_duration_s=item.total_duration_s,
                        # Carried across as saved — a pop window that opened
                        # before the swap must not open again after it (#125).
                        window_ends_at=item.window_ends_at,
                        window_opened_at=item.window_opened_at,
                    )
                    for item in info.respawn_timers
                ],
                MOB_TIMER_GROUP,
                now,
            )
        finally:
            self._restoring = False
        # The incoming character owns the window now; whatever the outgoing one
        # left is already saved (camp, or the Before export above).
        self._camped = False
        # Re-sync the profile with what actually survived the restore.
        self.export_now()
