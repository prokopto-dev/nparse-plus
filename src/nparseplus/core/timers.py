"""TimersService — Qt-free registry of spell/timer/counter/roll rows.

Port of the row bookkeeping in EQTool's SpellWindowViewModel.cs (TryAdd
overloads, TryRemoveUnambiguousSpell*, UpdateSpells expiry,
ClearYouSpells/AddSavedYouSpells persistence). Rendering (colors,
visibility, WPF grouping) stays in the UI layer; this service only owns the
rows and notifies observers on change.

Raid grouping (EQTool's UpdateSpells / RaidModeEnabled — flipping player
buffs to group-by-spell when targets outnumber spells) lives in the pure
``group_rows_for_display`` helper below and is strictly opt-in
(``raid_group_by_spell``); targets are the headers by default. EQTool's
version desynced because a single *global* orientation flag drifted from
rows whose ``is_target_player`` was set after add (post-/who), stranding
stuck spell-headers. The redesign (#17) derives orientation per group on
every render from the current row set — nothing is persisted, so a target
recognized mid-fight just re-groups on the next tick.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timedelta
from typing import NamedTuple

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

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


def snap_to_second(when: datetime) -> datetime:
    """Put a countdown anchor on the whole-second grid, truncating.

    Rows built from a log line are already whole-second (the bracket timestamp
    has no sub-second component — see ``core/lineinfo``) but the wall-clock
    producers (trigger timers, PigParse roll/boat timers, restored self-buffs,
    event-overlay bars) land at an arbitrary fraction of a second. Left alone,
    each of those flips its displayed digit on its own phase, so the window
    steps raggedly instead of once per second.

    Truncating — not rounding to nearest — is what makes those producers behave
    exactly like a log-anchored row: the log timestamp already drops its
    fraction, so an N-second timer reads ``N`` on its first frame. Rounding up
    (which nearest does for anchors past the half-second) would open on ``N+1``,
    a second longer than the duration the user asked for. The cost is the same
    sub-second-early expiry every log-anchored row already has.

    NOT applied to CH lanes: those chips measure time since a specific caster's
    cast, so a global grid would only smear them.
    """
    return when.replace(microsecond=0)


def seconds_left(ends_at: datetime, now: datetime) -> int:
    """Whole seconds remaining, rounded up and clamped at zero.

    Ceiling (not truncation) is what makes a 30 s timer read ``30`` the instant
    it starts and vanish after showing ``1``; the alternative reads one low for
    its whole life and parks on ``0`` for the final second.
    """
    return max(0, math.ceil((ends_at - now).total_seconds()))


def has_pop_window(row: Row) -> bool:
    """True when ``row`` carries a variable respawn ("pop") window (#125).

    Duck-typed like ``fraction_remaining``: only ``TimerRow`` has the field
    today, and every caller here takes the ``Row`` union.
    """
    return getattr(row, "window_ends_at", None) is not None


def in_pop_window(row: Row, now: datetime) -> bool:
    """True once ``row``'s base countdown has run out and its window is open.

    Deliberately does NOT also require ``now < window_ends_at``: a row whose
    window has just closed renders at 00:00 for its last frame rather than
    snapping back to the phase-1 presentation on its way off the screen.

    This — never ``window_opened_at is not None`` — is what the UI asks. The
    driver ticks at 100 ms and the window repaints on its own timer, so the
    stamp and the frame's ``now`` disagree by up to a quarter of a second;
    deriving the phase from ``now`` keeps digits, bar and colour agreeing
    whichever side of ``tick()`` a frame lands on.
    """
    window_ends_at = getattr(row, "window_ends_at", None)
    if window_ends_at is None:
        return False
    ends_at = getattr(row, "ends_at", None)
    return ends_at is not None and ends_at <= now


def expires_at(row: Row) -> datetime | None:
    """When ``row`` actually leaves the window.

    The end of its pop window when it has one — a window row is *not* done at
    ``ends_at``, which is only when it becomes poppable — else its plain
    ``ends_at``. None for a row with no countdown (``CounterRow``).
    """
    window_ends_at = getattr(row, "window_ends_at", None)
    if window_ends_at is not None:
        return window_ends_at
    return getattr(row, "ends_at", None)


def countdown_target(row: Row, now: datetime) -> datetime | None:
    """The instant the row's displayed countdown is running to, right now.

    Phase 1 counts down to the window opening (``ends_at``), phase 2 to the
    latest possible pop (``window_ends_at``). Both the digits and the row sort
    order go through this: a phase-2 row's ``ends_at`` is in the past, so
    sorting on it would pin the row to the top of its section forever.
    """
    if in_pop_window(row, now):
        return getattr(row, "window_ends_at", None)
    return getattr(row, "ends_at", None)


def fraction_remaining(row: Row, now: datetime) -> float:
    """How much of ``row``'s duration is still to run, clamped to 0.0-1.0.

    1.0 for a row with no countdown (``CounterRow``) so callers can treat
    "no progress information" as "full". This is what drives both the
    progress-bar value and its color fade in the UI; keeping the definition
    here (and Qt-free) means the two can never disagree.

    Phase-aware for a pop window (#125): inside the window the bar measures
    the window itself (``window_ends_at - ends_at``), not the base respawn, so
    it is a true progress value in both phases instead of parking on empty for
    the twelve hours that matter most.
    """
    ends_at = getattr(row, "ends_at", None)
    if ends_at is None:
        return 1.0
    window_ends_at = getattr(row, "window_ends_at", None)
    if window_ends_at is not None and in_pop_window(row, now):
        total = max((window_ends_at - ends_at).total_seconds(), 0.001)
        remaining = max(0.0, (window_ends_at - now).total_seconds())
    else:
        total = max(getattr(row, "total_duration_s", 0.0), 0.001)
        remaining = max(0.0, (ends_at - now).total_seconds())
    return min(remaining / total, 1.0)


class BaseRow(BaseModel):
    """Common fields of one row in the spell/trigger window."""

    model_config = ConfigDict(validate_assignment=True)

    name: str
    group: str  # target group; YOU_GROUP for the player
    updated_at: datetime
    is_target_player: bool = True


class CountdownRow(BaseRow):
    """A row that counts down to ``ends_at``.

    The validator is the single choke point that puts every countdown on the
    same one-second grid, whatever produced it. ``validate_assignment`` is on,
    so it also covers the in-place restarts that bypass the ``add_*`` methods
    (``TriggerTimerSink.add_timer``, the shared-trigger restart in
    ``core/sharing``, the group reset in ``add_roll``).
    """

    ends_at: datetime
    total_duration_s: float

    @field_validator("ends_at")
    @classmethod
    def _snap_ends_at(cls, value: datetime) -> datetime:
        return snap_to_second(value)


class SpellRow(CountdownRow):
    """An active buff/debuff timer (SpellViewModel)."""

    spell: Spell
    detrimental: bool = False
    is_cooldown: bool = False
    # Post-expiration alerts (#16): when > 0, the row is NOT removed the moment
    # it expires — it lingers this many seconds past ``ends_at`` (flashing in
    # the UI as a rebuff/recast prompt), with ``expired_at`` stamped once at the
    # crossover. Opt-in and per-spell; 0 keeps the normal expire-and-drop.
    post_expiry_persist_s: float = 0.0
    expired_at: datetime | None = None


class TimerRow(CountdownRow):
    """A generic countdown (TimerViewModel: cooldowns, custom timers).

    Optionally carries a variable respawn ("pop") window (#125). Big mobs do
    not respawn on a fixed clock: after time-of-death a base time elapses
    (``ends_at``) and only then does the mob become poppable, at any moment
    until a latest-possible time (``window_ends_at``). Trakanon is TOD + 4.5
    days, then a 12-hour window. So for a window row ``ends_at`` is when the
    *window opens*, not when the row is done — ``expires_at`` is.

    A field rather than a ``WindowTimerRow`` subclass on purpose: the spell
    window keys widget reuse on ``type(row).__name__``, so a subclass would
    rebuild the row at the exact moment the user is watching it cross over.
    Same precedent as ``SpellRow.post_expiry_persist_s``.

    ``total_duration_s`` keeps its meaning — the base TOD-to-window-open
    duration. There is deliberately no ``window_duration_s``: it is
    ``window_ends_at - ends_at``, and a second stored duration could disagree
    with the snapped anchors.
    """

    # The latest possible pop; the row's real end. Snapped like ``ends_at``:
    # it is the phase-2 countdown anchor and is produced by wall-clock
    # arithmetic, which is exactly what ``snap_to_second`` exists for.
    window_ends_at: datetime | None = None
    # Stamped once at the crossover, like ``SpellRow.expired_at`` — and NOT
    # snapped, for the same reason: it is an observation of tick time, not an
    # anchor anything counts down to.
    window_opened_at: datetime | None = None

    @field_validator("window_ends_at")
    @classmethod
    def _snap_window_ends_at(cls, value: datetime | None) -> datetime | None:
        # Must accept and return None, or ``validate_assignment`` would raise
        # on clearing the window.
        return None if value is None else snap_to_second(value)

    @model_validator(mode="after")
    def _window_must_follow_the_base_end(self) -> TimerRow:
        # A pure comparison that raises and never assigns: ``validate_assignment``
        # re-runs this on every field set, so assigning here would recurse.
        if self.window_ends_at is not None and self.window_ends_at <= self.ends_at:
            raise ValueError("window_ends_at must be after ends_at")
        return self


class CounterRow(BaseRow):
    """A per-target cast/resist tally (CounterViewModel)."""

    count: int = 1


class RollRow(CountdownRow):
    """A /random result (RollViewModel); rolls in a group share their window."""

    roll: int
    max_roll: int


type Row = SpellRow | TimerRow | CounterRow | RollRow


class YouSpellSnapshot(BaseModel):
    """Persisted self-buff (EQTool Models.YouSpells) for camp/login restore."""

    model_config = ConfigDict(frozen=True)

    name: str
    total_seconds_left: int


class RespawnTimerSnapshot(BaseModel):
    """Persisted respawn/custom TimerRow (nparse #57). Respawns keep counting
    while camped, so the absolute (naive local) end time is stored.

    A pop-window row (#125) carries both of its window fields too, so camping
    mid-window brings the row back still in its window — and, because
    ``window_opened_at`` is preserved rather than re-stamped, without
    re-announcing an opening that already happened. Both are optional, so an
    older store loads unmigrated.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    ends_at: datetime
    total_duration_s: float
    window_ends_at: datetime | None = None
    window_opened_at: datetime | None = None


class SelfCooldownSnapshot(BaseModel):
    """Persisted YOU_GROUP reuse timer (#120).

    Lay-on-Hands, Harm Touch, mend, disciplines, spell-recast and memorize
    cooldowns run in the real world whether or not you are logged in, so —
    unlike a buff's seconds-left — the absolute (naive local) end is stored
    and anything that came up while away is dropped on restore.

    ``spell_name`` is the spell a recast row belongs to, so it is rebuilt as
    the ``SpellRow`` it was rather than a bare countdown; empty for the
    ability/mend/discipline ``TimerRow``s, which have no spell.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    ends_at: datetime
    total_duration_s: float
    spell_name: str = ""


class SelfCounterSnapshot(BaseModel):
    """Persisted YOU_GROUP tally (bard song counts) for camp/login restore.

    A counter has no end time; what runs in the real world is its idle
    expiry, so the last-updated stamp is stored and a counter whose
    ``COUNTER_IDLE_EXPIRY`` window elapsed while away is dropped on restore.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    count: int
    updated_at: datetime


class TimersService:
    def __init__(self) -> None:
        self._rows: list[Row] = []
        self.on_change: list[Callable[[], None]] = []
        # Called from tick() with the rows that just expired (nparseplus
        # extension — the C# UpdateSpells drops them silently).
        self.on_expired: list[Callable[[list[Row]], None]] = []
        # Called from tick() with the rows whose pop window just opened (#125).
        # Separate from on_expired because opening is not expiring: the row
        # stays on screen and keeps counting, to ``window_ends_at``.
        self.on_window_open: list[Callable[[list[Row]], None]] = []

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

    def remove_self_rows(self) -> int:
        """Drop the rows that belong to the logged-in character (#120).

        Exactly the three kinds ``export_you_spells`` /
        ``export_self_cooldowns`` / ``export_self_counters`` cover, so camping
        hides nothing it cannot bring back — and nothing outside YOU_GROUP,
        which is why boats, roll windows, custom/shared timers and mob
        respawns survive a camp untouched (they are world state, not this
        character's). ``RollRow``s never land in YOU_GROUP (they carry their
        own ``Random -- N`` group), so naming the three kinds costs nothing
        today and keeps a future YOU_GROUP row type from being destroyed with
        no restore path — the exact defect that makes ``clear_you_spells``
        unusable here.
        """
        before = len(self._rows)
        self._rows = [
            row
            for row in self._rows
            if not (row.group == YOU_GROUP and isinstance(row, SpellRow | TimerRow | CounterRow))
        ]
        removed = before - len(self._rows)
        if removed:
            self._notify()
        return removed

    def clear_all_other_spells(self) -> None:
        """ClearAllOtherSpells: drop player-target spell rows except your own.

        Wired to the spell-window "Clear other players' timers" context action.
        """
        self._rows = [
            row
            for row in self._rows
            if not (isinstance(row, SpellRow) and row.is_target_player and row.group != YOU_GROUP)
        ]
        self._notify()

    # -- time ------------------------------------------------------------------

    def tick(self, now: datetime) -> list[Row]:
        """Remove expired rows; returns them.

        A SpellRow with ``post_expiry_persist_s > 0`` is not dropped at
        ``ends_at``: it lingers (stamping ``expired_at`` once) as a post-expiry
        alert (#16) and is only removed once its persist window elapses. Such a
        row still counts as "just expired" on the tick it crosses ``ends_at``,
        so ``on_expired`` fires exactly once for it, as before.

        A TimerRow with a pop window (#125) is not dropped at ``ends_at``
        either: that is where its *window opens*. It stamps ``window_opened_at``
        once, fires ``on_window_open``, and keeps running until
        ``window_ends_at`` — only then is it expired and returned. A row
        created already past both times opens and expires on the same tick,
        open first.
        """
        just_expired: list[Row] = []
        just_opened: list[Row] = []
        drop: list[Row] = []
        changed = False
        for row in self._rows:
            if isinstance(row, CounterRow):
                if now - row.updated_at > COUNTER_IDLE_EXPIRY:
                    drop.append(row)
                    just_expired.append(row)
                continue
            if row.ends_at > now:
                continue
            # After the ends_at guard above, so this branch is provably inert
            # for every row before its base end; before the persist branch,
            # which it does not touch (no row has both).
            if isinstance(row, TimerRow) and row.window_ends_at is not None:
                if row.window_opened_at is None:
                    row.window_opened_at = now
                    just_opened.append(row)
                    changed = True
                if row.window_ends_at > now:
                    continue
                drop.append(row)
                just_expired.append(row)
                continue
            persist = getattr(row, "post_expiry_persist_s", 0.0)
            if persist > 0:
                if row.expired_at is None:
                    row.expired_at = now
                    just_expired.append(row)
                    changed = True
                if now - row.expired_at > timedelta(seconds=persist):
                    drop.append(row)
                continue
            drop.append(row)
            just_expired.append(row)

        for row in drop:
            self._rows.remove(row)

        if just_opened:
            for callback in list(self.on_window_open):
                callback(just_opened)
        if just_expired:
            for callback in list(self.on_expired):
                callback(just_expired)
        # Notify on any change: a fresh expiry, a persisted row finally
        # dropping, or a crossover that only stamped ``expired_at`` /
        # ``window_opened_at``.
        if drop or changed:
            self._notify()
        return just_expired

    # -- persistence (camp/login) -----------------------------------------------

    def export_you_spells(self, now: datetime) -> list[YouSpellSnapshot]:
        """Self-buffs with their remaining seconds (EQTool YouSpells save)."""
        out: list[YouSpellSnapshot] = []
        for row in self._rows:
            if isinstance(row, SpellRow) and row.group == YOU_GROUP and not row.is_cooldown:
                # Same rounding as the display, so a save/restore cycle keeps
                # the number the user was looking at instead of shedding a
                # second every time they camp.
                seconds = seconds_left(row.ends_at, now)
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

    def export_self_cooldowns(self, now: datetime) -> list[SelfCooldownSnapshot]:
        """Still-running YOU_GROUP reuse timers, with absolute ends (#120).

        Both shapes a self cooldown takes: the ability/mend/discipline/memorize
        ``TimerRow``s and the spell-recast ``SpellRow``s (``is_cooldown``),
        which ``export_you_spells`` deliberately skips because they are not
        buffs and must not come back frozen.
        """
        out: list[SelfCooldownSnapshot] = []
        for row in self._rows:
            if row.group != YOU_GROUP or isinstance(row, CounterRow) or row.ends_at <= now:
                continue
            if isinstance(row, TimerRow):
                out.append(
                    SelfCooldownSnapshot(
                        name=row.name,
                        ends_at=row.ends_at,
                        total_duration_s=row.total_duration_s,
                    )
                )
            elif isinstance(row, SpellRow) and row.is_cooldown:
                out.append(
                    SelfCooldownSnapshot(
                        name=row.name,
                        ends_at=row.ends_at,
                        total_duration_s=row.total_duration_s,
                        spell_name=row.spell.name,
                    )
                )
        return out

    def restore_self_cooldowns(
        self, saved: Sequence[SelfCooldownSnapshot], now: datetime, book: SpellBook
    ) -> None:
        """Rebuild saved YOU_GROUP cooldowns; anything that came up while away
        is dropped (same rule as ``restore_respawn_timers``)."""
        restored = False
        for item in saved:
            if item.ends_at <= now:
                continue
            spell = book.spell_by_name(item.spell_name) if item.spell_name else None
            row: Row
            if spell is not None:
                row = SpellRow(
                    name=item.name,
                    group=YOU_GROUP,
                    updated_at=now,
                    spell=spell,
                    ends_at=item.ends_at,
                    total_duration_s=item.total_duration_s,
                    detrimental=spell.is_detrimental,
                    is_cooldown=True,
                )
            else:
                row = TimerRow(
                    name=item.name,
                    group=YOU_GROUP,
                    updated_at=now,
                    ends_at=item.ends_at,
                    total_duration_s=item.total_duration_s,
                )
            self._rows.append(row)
            restored = True
        if restored:
            self._notify()

    def export_self_counters(self) -> list[SelfCounterSnapshot]:
        """YOU_GROUP tallies (bard song counts) with their last-updated stamp."""
        return [
            SelfCounterSnapshot(name=row.name, count=row.count, updated_at=row.updated_at)
            for row in self._rows
            if isinstance(row, CounterRow) and row.group == YOU_GROUP
        ]

    def restore_self_counters(self, saved: Sequence[SelfCounterSnapshot], now: datetime) -> None:
        """Rebuild saved YOU_GROUP tallies; one whose idle window elapsed while
        away is dropped, exactly as ``tick`` would have dropped it."""
        restored = False
        for item in saved:
            if now - item.updated_at > COUNTER_IDLE_EXPIRY:
                continue
            self._rows.append(
                CounterRow(
                    name=item.name,
                    group=YOU_GROUP,
                    updated_at=item.updated_at,
                    count=item.count,
                )
            )
            restored = True
        if restored:
            self._notify()

    def export_respawn_timers(self, group: str, now: datetime) -> list[RespawnTimerSnapshot]:
        """Still-running TimerRows of one group (respawn/custom timers).

        The filter is ``expires_at`` rather than ``ends_at`` so a row whose pop
        window is open — whose ``ends_at`` is by definition in the past — is
        saved rather than dropped at exactly the moment it matters most (#125).
        """
        out: list[RespawnTimerSnapshot] = []
        for row in self._rows:
            if not isinstance(row, TimerRow) or not _eq(row.group, group):
                continue
            end = expires_at(row)
            if end is None or end <= now:
                continue
            out.append(
                RespawnTimerSnapshot(
                    name=row.name,
                    ends_at=row.ends_at,
                    total_duration_s=row.total_duration_s,
                    window_ends_at=row.window_ends_at,
                    window_opened_at=row.window_opened_at,
                )
            )
        return out

    def restore_respawn_timers(
        self, saved: Sequence[RespawnTimerSnapshot], group: str, now: datetime
    ) -> None:
        """Rebuild saved TimerRows; anything that expired while away is dropped.

        A pop-window row (#125) is judged on its window's end, and comes back
        with ``window_opened_at`` exactly as saved — **never re-stamped**.
        ``TimerPersistenceHandler`` does ``remove_group`` + restore on every
        character swap, so re-stamping would re-fire ``on_window_open``, its
        bus event and its speech each time the player switched characters.
        """
        restored = False
        for item in saved:
            window_ends_at = item.window_ends_at
            window_opened_at = item.window_opened_at
            if window_ends_at is not None and snap_to_second(window_ends_at) <= snap_to_second(
                item.ends_at
            ):
                # settings.json is user-editable, and the row model rejects a
                # window that does not follow its base end. Degrade to a plain
                # timer rather than raise inside this unguarded loop, which
                # would abandon every later entry.
                window_ends_at = None
                window_opened_at = None
            if (window_ends_at or item.ends_at) <= now:
                continue
            self._rows.append(
                TimerRow(
                    name=item.name,
                    group=group,
                    updated_at=now,
                    ends_at=item.ends_at,
                    total_duration_s=item.total_duration_s,
                    window_ends_at=window_ends_at,
                    window_opened_at=window_opened_at,
                )
            )
            restored = True
        if restored:
            self._notify()


def _eq(a: str, b: str) -> bool:
    return a.casefold() == b.casefold()


# -- display grouping (raid-mode orientation, #17) ----------------------------


class DisplayGroup(NamedTuple):
    """One grouped section of the spell window, with its orientation.

    ``orientation == "target"`` (the default everywhere): ``header`` is a
    target group key (YOU_GROUP, an NPC/player name, or a built-in timer
    section) and each row's own ``name`` is the label — targets head, spells
    list. ``orientation == "spell"`` (raid mode, opt-in): ``header`` is a
    spell name and each row's ``group`` (the target) is the label — the spell
    heads, targets list.
    """

    header: str
    orientation: str
    rows: list[Row]


def _is_flip_candidate(row: Row) -> bool:
    """A beneficial buff on another player — the only rows raid mode flips."""
    return (
        isinstance(row, SpellRow)
        and row.is_target_player
        and row.group != YOU_GROUP
        and not row.detrimental
        and not row.is_cooldown
    )


def group_rows_for_display(
    rows: Sequence[Row], *, group_by_spell: bool = False
) -> list[DisplayGroup]:
    """Group ``rows`` into the spell window's ordered, oriented sections.

    Default (``group_by_spell=False``): every section is target-headed —
    YOU_GROUP first, then the other groups in casefold order (the built-in
    timer sections carry leading spaces so they sort where they always have).
    This reproduces the window's long-standing layout exactly.

    Opt-in raid mode (``group_by_spell=True``): when the beneficial buffs on
    OTHER players span more distinct targets than distinct spells, those buffs
    flip to spell-headed sections (one per spell, each listing its targets) so
    a raid-wide buff reads as a single spell over many people. Everything else
    — YOU_GROUP, NPC targets, the timer sections, detrimental/cooldown rows —
    stays target-headed. Section order becomes: YOU_GROUP, the spell-headed
    groups (alphabetical), then the remaining target groups (unchanged order).

    Orientation is recomputed from ``rows`` on every call and never persisted,
    which is what keeps a target recognized mid-fight (``is_target_player``
    flipped after the row was added) from stranding a stale header (#17). Rows
    within each section are returned in a deterministic order (targets by name,
    spell sections by target); the UI re-sorts for its live sort mode.
    """
    candidates = [r for r in rows if group_by_spell and _is_flip_candidate(r)]
    distinct_targets = {r.group for r in candidates}
    distinct_spells = {r.name for r in candidates}
    flip = bool(candidates) and len(distinct_targets) > len(distinct_spells)
    flipped_ids = {id(r) for r in candidates} if flip else set()

    target_groups: dict[str, list[Row]] = {}
    for row in rows:
        if id(row) in flipped_ids:
            continue
        target_groups.setdefault(row.group, []).append(row)

    result: list[DisplayGroup] = []
    if YOU_GROUP in target_groups:
        you = target_groups.pop(YOU_GROUP)
        result.append(DisplayGroup(YOU_GROUP, "target", _sorted_by_name(you)))

    if flip:
        spell_groups: dict[str, list[Row]] = {}
        for row in candidates:
            spell_groups.setdefault(row.name, []).append(row)
        for spell_name in sorted(spell_groups, key=str.casefold):
            members = sorted(spell_groups[spell_name], key=lambda r: r.group.casefold())
            result.append(DisplayGroup(spell_name, "spell", members))

    for group in sorted(target_groups, key=str.casefold):
        result.append(DisplayGroup(group, "target", _sorted_by_name(target_groups[group])))
    return result


def _sorted_by_name(rows: list[Row]) -> list[Row]:
    return sorted(rows, key=lambda r: r.name.casefold())
