"""TimersService — Qt-free registry of spell/timer/counter/roll rows.

Port of the row bookkeeping in EQTool's SpellWindowViewModel.cs (TryAdd
overloads, TryRemoveUnambiguousSpell*, UpdateSpells expiry, adaptive
grouping, ClearYouSpells/AddSavedYouSpells persistence). Rendering (colors,
visibility, WPF grouping) stays in the UI layer; this service only owns the
rows and notifies observers on change.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict

from nparseplus.core.enums import PlayerClass
from nparseplus.core.spells.durations import get_duration_seconds
from nparseplus.core.spells.models import Spell
from nparseplus.core.spells.spells_us import SPACE_YOU, SpellBook

# The self-target group constant (EQSpells.SpaceYou).
YOU_GROUP = SPACE_YOU

# The three built-in timer sections. Their string values carry leading
# spaces ONLY to order the headers: spellwindow sorts groups casefold with
# YOU first, and strips the spaces for display. Casefold order of the three
# is Custom < Mob < Roll (c<m<r); all sort before "Boats" (no leading space),
# roughly where the old single "  Custom Timer" section sat.
#
# TRIGGER_TIMER_GROUP holds trigger-engine, chat-command, AND shared remote
# timers (EQTool CustomTimer.TargetName is per-timer; we use one shared
# section). It displays as "Custom Timers" — renamed from the old "Timers"
# value when the single respawn "Custom Timer" section split into Mob/Roll/
# Custom.
TRIGGER_TIMER_GROUP = "  Custom Timers"

# Mob respawn ("--Dead-- <victim>"), Sirran, and FTE-rule countdowns.
# Persistence and respawn-expiry announcements follow this group.
MOB_TIMER_GROUP = "  Mob Timers"

# Server roll windows (Ring 8 / Scout Charisa) from the PigParse API.
ROLL_TIMER_GROUP = "  Roll Timers"

# Counters are dropped when not refreshed for this long (UpdateSpells).
COUNTER_IDLE_EXPIRY = timedelta(minutes=10)


class BaseRow(BaseModel):
    """Common fields of one row in the spell/trigger window."""

    model_config = ConfigDict(validate_assignment=True)

    name: str
    group: str  # target group; YOU_GROUP for the player
    updated_at: datetime
    is_target_player: bool = True


class SpellRow(BaseRow):
    """An active buff/debuff timer (SpellViewModel)."""

    spell: Spell
    ends_at: datetime
    total_duration_s: float
    detrimental: bool = False
    is_cooldown: bool = False


class TimerRow(BaseRow):
    """A generic countdown (TimerViewModel: cooldowns, custom timers)."""

    ends_at: datetime
    total_duration_s: float


class CounterRow(BaseRow):
    """A per-target cast/resist tally (CounterViewModel)."""

    count: int = 1


class RollRow(BaseRow):
    """A /random result (RollViewModel); rolls in a group share their window."""

    roll: int
    max_roll: int
    ends_at: datetime
    total_duration_s: float


type Row = SpellRow | TimerRow | CounterRow | RollRow


class YouSpellSnapshot(BaseModel):
    """Persisted self-buff (EQTool Models.YouSpells) for camp/login restore."""

    model_config = ConfigDict(frozen=True)

    name: str
    total_seconds_left: int


class RespawnTimerSnapshot(BaseModel):
    """Persisted respawn/custom TimerRow (nparse #57). Respawns keep counting
    while camped, so the absolute (naive local) end time is stored."""

    model_config = ConfigDict(frozen=True)

    name: str
    ends_at: datetime
    total_duration_s: float


class TimersService:
    def __init__(self) -> None:
        self._rows: list[Row] = []
        self.on_change: list[Callable[[], None]] = []
        # Called from tick() with the rows that just expired (nparseplus
        # extension — the C# UpdateSpells drops them silently).
        self.on_expired: list[Callable[[list[Row]], None]] = []
        # Adaptive grouping state (SpellWindowViewModel.PCSpellsGroupedByTarget).
        self._pc_grouped_by_target = False
        # Gate for _adaptive_regroup (EQTool RaidModeEnabled); composition
        # points this at Settings.spellwindow.raid_mode_auto.
        self.raid_mode_provider: Callable[[], bool] = lambda: True

    # -- observation ---------------------------------------------------------

    def _notify(self) -> None:
        for callback in list(self.on_change):
            callback()

    def snapshot(self) -> list[Row]:
        return list(self._rows)

    def rows_of(self, row_type: type) -> list[Row]:
        return [row for row in self._rows if isinstance(row, row_type)]

    def find(self, name: str, group: str | None = None) -> Row | None:
        for row in self._rows:
            if _eq(row.name, name) and (group is None or _eq(row.group, group)):
                return row
        return None

    # -- adds (TryAdd overloads) ----------------------------------------------

    def add_spell(self, row: SpellRow, overwrite: bool = True) -> SpellRow:
        if row.is_target_player and self._pc_grouped_by_target and row.group != YOU_GROUP:
            row.name, row.group = row.group, row.name
        if overwrite:
            existing = next(
                (
                    r
                    for r in self._rows
                    if isinstance(r, SpellRow) and _eq(r.name, row.name) and _eq(r.group, row.group)
                ),
                None,
            )
            if existing is not None:
                self._rows.remove(existing)
        self._rows.append(row)
        self._notify()
        return row

    def add_timer(self, row: TimerRow, allow_duplicates: bool = False) -> TimerRow:
        if not allow_duplicates:
            existing = next(
                (
                    r
                    for r in self._rows
                    if isinstance(r, TimerRow) and _eq(r.name, row.name) and _eq(r.group, row.group)
                ),
                None,
            )
            if existing is not None:
                self._rows.remove(existing)
        self._rows.append(row)
        self._notify()
        return row

    def add_counter(self, row: CounterRow) -> CounterRow:
        """Increment an existing (name, group) counter or start one at ``count``."""
        existing = next(
            (
                r
                for r in self._rows
                if isinstance(r, CounterRow) and _eq(r.name, row.name) and _eq(r.group, row.group)
            ),
            None,
        )
        if existing is not None:
            existing.count += 1
            existing.updated_at = row.updated_at
            self._notify()
            return existing
        self._rows.append(row)
        self._notify()
        return row

    def add_roll(self, row: RollRow) -> RollRow:
        """Add a roll; every roll in the same group has its window reset."""
        for other in self._rows:
            if isinstance(other, RollRow) and _eq(other.group, row.group):
                other.ends_at = row.ends_at
        self._rows.append(row)
        self._notify()
        return row

    # -- removals --------------------------------------------------------------

    def remove_row(self, row: Row) -> bool:
        """Remove one specific row (identity match). Returns True if present."""
        try:
            self._rows.remove(row)
        except ValueError:
            return False
        self._notify()
        return True

    def try_remove_unambiguous_self(self, spell_names: Iterable[str]) -> bool:
        """Remove the single YOU_GROUP row matching any name (else do nothing)."""
        names = [n.casefold() for n in spell_names]
        if not names:
            return False
        matches = [
            row for row in self._rows if row.name.casefold() in names and row.group == YOU_GROUP
        ]
        if len(matches) == 1:
            self._rows.remove(matches[0])
            self._notify()
            return True
        return False

    def try_remove_unambiguous_other(self, spell_names: str | Iterable[str]) -> bool:
        """Remove a non-self row when exactly one matches by name (then group)."""
        names = [spell_names] if isinstance(spell_names, str) else list(spell_names)
        names = [n.casefold() for n in names if n and not n.isspace()]
        if not names:
            return False
        removed = False
        matches = [
            row for row in self._rows if row.name.casefold() in names and row.group != YOU_GROUP
        ]
        if len(matches) == 1:
            self._rows.remove(matches[0])
            removed = True
        matches = [row for row in self._rows if row.group.casefold() in names]
        if len(matches) == 1:
            self._rows.remove(matches[0])
            removed = True
        if removed:
            self._notify()
        return removed

    def remove_group(self, group: str) -> int:
        """Drop every row for a target (e.g. on slain). Returns count removed."""
        before = len(self._rows)
        self._rows = [row for row in self._rows if not _eq(row.group, group)]
        removed = before - len(self._rows)
        if removed:
            self._notify()
        return removed

    def clear_all(self) -> int:
        """Drop every row (manual reset from the overlay). Returns count."""
        removed = len(self._rows)
        if removed:
            self._rows = []
            self._notify()
        return removed

    def clear_you_spells(self) -> None:
        self._rows = [row for row in self._rows if row.group != YOU_GROUP]
        self._notify()

    def clear_all_other_spells(self) -> None:
        """ClearAllOtherSpells: drop player-target spell rows except your own."""
        self._pc_grouped_by_target = False
        self._rows = [
            row
            for row in self._rows
            if not (isinstance(row, SpellRow) and row.is_target_player and row.group != YOU_GROUP)
        ]
        self._notify()

    # -- time ------------------------------------------------------------------

    def tick(self, now: datetime) -> list[Row]:
        """Remove expired rows; returns them. Also runs adaptive regrouping."""

        def _is_expired(row: Row) -> bool:
            if isinstance(row, CounterRow):
                return now - row.updated_at > COUNTER_IDLE_EXPIRY
            return row.ends_at <= now

        expired: list[Row] = [row for row in self._rows if _is_expired(row)]
        for row in expired:
            self._rows.remove(row)

        self._adaptive_regroup()
        if expired:
            for callback in list(self.on_expired):
                callback(expired)
            self._notify()
        return expired

    def _adaptive_regroup(self) -> None:
        """UpdateSpells: if player-buff rows span more targets than distinct
        spells, flip to grouping the window by spell instead of by target."""
        player_spells = [
            row
            for row in self._rows
            if isinstance(row, SpellRow) and row.is_target_player and row.group != YOU_GROUP
        ]
        if not self.raid_mode_provider():
            # Raid mode off: restore per-target grouping if we had flipped.
            if self._pc_grouped_by_target:
                for row in player_spells:
                    row.name, row.group = row.group, row.name
                self._pc_grouped_by_target = False
            return
        groups = {row.group for row in player_spells}
        names = {row.name for row in player_spells}
        if len(groups) > len(names):
            for row in player_spells:
                row.name, row.group = row.group, row.name
            self._pc_grouped_by_target = not self._pc_grouped_by_target

    # -- persistence (camp/login) -----------------------------------------------

    def export_you_spells(self, now: datetime) -> list[YouSpellSnapshot]:
        """Self-buffs with their remaining seconds (EQTool YouSpells save)."""
        out: list[YouSpellSnapshot] = []
        for row in self._rows:
            if isinstance(row, SpellRow) and row.group == YOU_GROUP and not row.is_cooldown:
                seconds = int((row.ends_at - now).total_seconds())
                if seconds > 0:
                    out.append(YouSpellSnapshot(name=row.name, total_seconds_left=seconds))
        return out

    def restore_you_spells(
        self,
        saved: Sequence[YouSpellSnapshot],
        now: datetime,
        book: SpellBook,
        player_class: PlayerClass | None = None,
        player_level: int | None = None,
    ) -> None:
        """AddSavedYouSpells: rebuild self-buff rows with saved remaining time."""
        for item in saved:
            spell = book.spell_by_name(item.name)
            if spell is None:
                continue
            duration = get_duration_seconds(spell, player_class, player_level)
            self._rows.append(
                SpellRow(
                    name=spell.name,
                    group=YOU_GROUP,
                    updated_at=now,
                    spell=spell,
                    ends_at=now + timedelta(seconds=item.total_seconds_left),
                    total_duration_s=float(duration),
                    detrimental=spell.is_detrimental,
                )
            )
        if saved:
            self._notify()

    def export_respawn_timers(self, group: str, now: datetime) -> list[RespawnTimerSnapshot]:
        """Still-running TimerRows of one group (respawn/custom timers)."""
        return [
            RespawnTimerSnapshot(
                name=row.name, ends_at=row.ends_at, total_duration_s=row.total_duration_s
            )
            for row in self._rows
            if isinstance(row, TimerRow) and _eq(row.group, group) and row.ends_at > now
        ]

    def restore_respawn_timers(
        self, saved: Sequence[RespawnTimerSnapshot], group: str, now: datetime
    ) -> None:
        """Rebuild saved TimerRows; anything that expired while away is dropped."""
        restored = False
        for item in saved:
            if item.ends_at <= now:
                continue
            self._rows.append(
                TimerRow(
                    name=item.name,
                    group=group,
                    updated_at=now,
                    ends_at=item.ends_at,
                    total_duration_s=item.total_duration_s,
                )
            )
            restored = True
        if restored:
            self._notify()


def _eq(a: str, b: str) -> bool:
    return a.casefold() == b.casefold()
