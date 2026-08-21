"""DpsPersistenceHandler — the lifetime Best row survives a restart (#83).

EQTool carries ``BestPlayerDamage`` on ``Models/PlayerInfo.cs`` — the player
profile, saved with settings — and max-merges into it in
``DPSWindowViewModel.UpdateDPS``. ``core.dps`` did the merge and kept the
result in memory, so "best" reset every launch, which makes it a second copy
of "this session" rather than a record.

Per character, which is the granularity EQTool chose and the only one that
means anything: a level 60 rogue's best hit says nothing about your level 12
cleric. That makes ``Before/AfterPlayerChangedEvent`` the seam —
``PlayerProfileHandler`` and ``TimerPersistenceHandler`` already hang the rest
of the per-character state on the same pair.

**A stored best carries what it is a reading OF.** ``reset_session_stats``
drops the live best whenever a counting knob moves, because a best-dps
averaged over 12 s is not comparable to one over 4 s and a best taken while
spell damage counted is unreachable once the sources narrow to melee. Storing
the number alone would have let a restart bring the incomparable reading back,
and — the case a live reset cannot reach at all — would have left every
character who was not logged in when the knob moved holding a best measured
under the old rules. So the record carries ``measurement_rules_key()`` and a
record whose fingerprint disagrees with the current rules is dropped on
restore and overwritten with the empty one.

``last_session`` is deliberately NOT persisted: it is explicitly a
within-session record that the user moved aside themselves, not a lifetime
one.

The restore assumes nothing else mutates the tracker between
``AfterPlayerChangedEvent`` firing — at which point ``ActivePlayer`` already
names the INCOMING character — and ``_load`` running, or an ``on_change``
would land in that window and write the outgoing character's best into the
incoming one's profile. That holds because ``DpsHandler`` clears the meter on
``BeforePlayerChangedEvent`` rather than After, which is the other half of
this pair and is commented as such there.

The bus half runs on the driver thread. **``FightTracker.on_change`` does
not, always**: this is its only subscriber, and two callers fire it from the
GUI thread — ``Backend.apply_dps_settings`` (settings Apply, the seam that
makes a counting rule change clear the best) and the DPS window's session
controls. Tolerated rather than routed through ``LogDriver.submit_to_driver``,
for reasons that are specific and worth stating rather than assuming:

* Every write on that path REBINDS an attribute (``self.best = PlayerDamage()``),
  never read-modify-writes one, so there is nothing to tear. A
  ``_update_session_stats`` already holding the old object writes into one
  that has been discarded — the reset stands — and one that arrives after the
  rebind merges into the new object, which is a fight legitimately setting a
  new best.
* ``export_now`` is likewise a rebind of ``info.best_damage`` plus one of
  ``_last``. Two threads racing it cost at worst one stale reading, which the
  next damage line re-exports.
* The window already reads live tracker state from the GUI thread every
  500 ms (``snapshot``), so this is not a new crossing — and an inbox for
  three scalar writes, while that read stays direct, would buy nothing.

Persistence itself goes through ``request_save`` — the app's DebouncedSaver,
which is thread-safe and coalesces bursts — so it is safe from either thread.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from nparseplus.config.settings import SavedPlayerDamage, get_player
from nparseplus.core.bus import EventBus
from nparseplus.core.dps import FightTracker, PlayerDamage
from nparseplus.core.events import AfterPlayerChangedEvent, BeforePlayerChangedEvent
from nparseplus.core.player import ActivePlayer

if TYPE_CHECKING:
    from nparseplus.config.settings import PlayerInfo, Settings


class DpsPersistenceHandler:
    def __init__(
        self,
        bus: EventBus,
        player: ActivePlayer,
        settings: Settings,
        tracker: FightTracker,
        request_save: Callable[[], None] | None = None,
    ) -> None:
        self.player = player
        self.settings = settings
        self.tracker = tracker
        self._request_save = request_save
        self._restoring = False
        #: The last reading written out, so the hot path is three integer
        #: comparisons. ``request_save`` arms a fresh ``threading.Timer`` on
        #: every call and ``on_change`` fires on every damage line, so an
        #: unguarded export would start a thread per hit.
        self._last: tuple[int, int, int] | None = None
        bus.subscribe(BeforePlayerChangedEvent, self._on_before_player_changed)
        bus.subscribe(AfterPlayerChangedEvent, self._on_after_player_changed)
        tracker.on_change.append(self._on_tracker_changed)

    # -- export ------------------------------------------------------------------

    def _profile(self) -> PlayerInfo | None:
        server_key = self.player.server_key
        if server_key is None or not self.player.name:
            return None
        return get_player(self.settings, self.player.name, server_key)

    @staticmethod
    def _reading(best: PlayerDamage) -> tuple[int, int, int]:
        return (best.highest_dps, best.total_damage, best.highest_hit)

    def _on_tracker_changed(self) -> None:
        """Export when the best actually moved — most changes are not it.

        The fingerprint is deliberately not part of this comparison, and it
        costs nothing to leave out: the only thing that changes the rules is
        ``configure()``, which resets a non-empty best to zero on its way
        through, and a reset IS a change of reading. A rules change that finds
        the best already empty writes nothing, leaving a stale fingerprint
        beside a zero reading — which the restore drops either way, arriving
        at the same zero.
        """
        if self._restoring:
            return
        reading = self._reading(self.tracker.best)
        if reading == self._last:
            return
        self.export_now()

    def export_now(self) -> None:
        """Write the live best into the active character's profile."""
        if self._restoring:
            return
        info = self._profile()
        if info is None:
            return
        best = self.tracker.best
        info.best_damage = SavedPlayerDamage(
            highest_dps=best.highest_dps,
            total_damage=best.total_damage,
            highest_hit=best.highest_hit,
            measurement_rules=self.tracker.measurement_rules_key(),
        )
        self._last = self._reading(best)
        if self._request_save is not None:
            self._request_save()

    # -- restore -----------------------------------------------------------------

    def _on_before_player_changed(self, _event: BeforePlayerChangedEvent) -> None:
        # Belt and braces: every change already exported itself, but the
        # outgoing character's profile is only reachable while they are still
        # the active one.
        self.export_now()

    def _on_after_player_changed(self, _event: AfterPlayerChangedEvent) -> None:
        info = self._profile()
        if info is None:
            # No profile to read from, and the previous character's record must
            # not stay on screen as if it were this one's.
            self._load(PlayerDamage())
            return
        saved = info.best_damage
        rules = self.tracker.measurement_rules_key()
        if saved is None or saved.measurement_rules != rules:
            # Never measured, or measured under rules that are no longer the
            # ones in force. Either way there is nothing comparable to show.
            self._load(PlayerDamage())
        else:
            self._load(
                PlayerDamage(
                    highest_dps=saved.highest_dps,
                    total_damage=saved.total_damage,
                    highest_hit=saved.highest_hit,
                )
            )
        # Re-sync: this stamps the current fingerprint onto a record that was
        # dropped, so the incomparable reading is gone from disk too.
        self.export_now()

    def _load(self, best: PlayerDamage) -> None:
        self._restoring = True
        try:
            self.tracker.load_best(best)
        finally:
            self._restoring = False
        self._last = self._reading(best)
