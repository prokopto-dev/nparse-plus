"""DPS/fight engine — Qt-free port of EQTool's EntittyDPS + DPSWindowViewModel.

``FightEntity`` ports EntittyDPS.cs (per attacker/target damage list, the
12-second trailing window, best-12s window, highest hit). ``Fight`` groups
the entities attacking one target (the WPF grouping by TargetName).
``FightTracker`` ports the DPSWindowViewModel row lifecycle: TryAdd on
damage, TargetDied on slain, ShouldRemove staleness pruning, and the
session Best/Current/Last PlayerDamage stats maintained in UpdateDPS.

Deviations from EQTool are noted inline; the main one is that the first hit
of an entity is appended to its damage list (EQTool only seeded the totals),
so trailing damage decays correctly for one-hit entities. The second is that
staleness retires a *fight*, never an individual attacker — see
``FIGHT_RETENTION_SECONDS``. The best window is also carried between hits
rather than re-swept per hit as Update12SecondDmg does — the same number, at
a cost that does not grow with the fight (see ``_update_best_window``).

Two more are whole features the C# does not have, both marked DEVIATION at
the code that implements them: non-melee damage is *attributed* rather than
blanket-credited to you (``_attribute``, #80), and your pet's row is
recognised as yours and folded into the session footer
(``_is_your_pet`` / ``_update_session_stats``, #81).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Literal

from nparseplus.core.damagetypes import is_melee
from nparseplus.core.events import DamageEvent

# EQSpells.You — the active player's name in damage lines.
YOU = "You"

# The trailing-DPS window (EntittyDPS.UpdateDps).
TRAILING_WINDOW = timedelta(seconds=12)

#: What a row is allowed to count (settings: ``dps.damage_sources``).
#:
#: ``melee``       - weapon and fist damage only. What ``melee_only`` meant.
#: ``melee+mine``  - melee, plus non-melee credited to you by ``_attribute``.
#: ``all``         - melee plus every non-melee line, the ones that cannot be
#:                   attributed parked on ``UNATTRIBUTED_SPELL_ATTACKER``.
DamageSources = Literal["melee", "melee+mine", "all"]
DAMAGE_SOURCES: tuple[DamageSources, ...] = ("melee", "melee+mine", "all")

# The default for a FRESH install counts your own spell damage and nobody
# else's. Melee-only was the 2.2 default for one reason — "<target> was hit
# by non-melee" names no attacker, so counting it meant crediting YOU for
# every nuke in range — and that reason is what ``_attribute`` removes. A
# player who never casts sees exactly the melee-only behavior under this
# mode, because nothing ever arms the credit window.
#
# An EXISTING settings.json is deliberately NOT moved here: its `melee_only`
# migrates literally, so upgrading never changes what someone's number means
# (see DpsSettings._fold_in_legacy_melee_only).
DEFAULT_DAMAGE_SOURCES: DamageSources = "melee+mine"

# How long after one of your own casts a "was hit by non-melee" line still
# counts as yours. See ``_attribute`` for what this can and cannot prove.
SPELL_CREDIT_WINDOW_SECONDS = 2.0

# Where non-melee damage goes when it cannot be tied to one of your casts,
# under the "all" mode. Parenthesised because EQ names cannot contain
# brackets, so it can never collide with a real attacker.
UNATTRIBUTED_SPELL_ATTACKER = "(spell damage)"

#: Human phrasing for each mode — one wording for the settings page, the
#: window tooltip and the docs. Qt-free so the window stays a renderer.
DAMAGE_SOURCE_LABELS: dict[str, str] = {
    "melee": "melee only",
    "melee+mine": "melee + my spells",
    "all": "all damage",
}

#: The short marker the DPS window shows in its title bar. A mode that is
#: EXCLUDING damage has to say so, or a caster reads a zero and concludes the
#: meter is broken rather than filtered (#80).
DAMAGE_SOURCE_MARKS: dict[str, str] = {
    "melee": "MELEE",
    "melee+mine": "MELEE + MINE",
    "all": "ALL",
}

# The pet stays an INDEPENDENT row by default; merging its damage into your
# session Best/Now/Last footer is opt-in.
#
# DIVERGES from #81, which asked for this on. How to count a pet is a
# genuine difference of opinion — a magician reasonably reads the pet as
# part of their output, and someone comparing numbers with a parse
# reasonably does not — and a meter should not answer that question for its
# user by changing what their headline number means on upgrade. The pet's
# row is still MARKED as yours whatever this says: naming whose pet that is
# is identification, not measurement.
COUNT_PET_DAMAGE_DEFAULT = False

# A fight is retired this long after the last damage against its target, from
# ANY attacker.
#
# DEVIATION from EQTool (DPSWindowViewModel.ShouldRemove), deliberate: the C#
# aged out each EntittyDPS on its own last hit, so an attacker who opened with
# a stun and then went to healing vanished from the list 40s later while the
# mob was still up — the meter dropped rows mid-fight and under-reported who
# was actually on the target. Attackers are never pruned individually here;
# everything that has landed on a target stays in that target's group for as
# long as the group exists, and the group ages out as a unit once the target
# stops taking damage.
FIGHT_RETENTION_SECONDS = 40.0

# Session stats only consider your entity once the fight ran this long
# (DPSWindowViewModel.UpdateDPS: ``TotalSeconds > 20``).
SESSION_MIN_FIGHT_SECONDS = 20

# Hits at or above this are bogus (environment/scripted) and never become the
# session highest hit (PlayerDamage.HighestHit setter).
BOGUS_HIT_THRESHOLD = 32000


@dataclass
class FightEntity:
    """One attacker's damage against one target (EntittyDPS)."""

    attacker_name: str
    target_name: str
    start_time: datetime
    level: int | None = None
    death_time: datetime | None = None
    # This attacker was your pet at the time it was landing these hits (#81).
    # Recorded per HIT and sticky for the life of the entity, because the
    # live pet name cannot answer the question the session footer asks: a pet
    # that dies, is reclaimed, or is replaced mid-fight clears
    # ``FightTracker.pet_name``, and keying the footer off that name alone
    # dropped everything the pet had already done from the combined reading.
    # The row flag stays keyed to the CURRENT name (see ``_is_your_pet``) —
    # "that row is my pet" and "that damage was mine" are different claims.
    #
    # Scoped to one fight, which bounds the one case it gets wrong: a charm
    # that breaks mid-fight and keeps hitting the same target goes on
    # counting until the group retires. A broken charm turns on you, so in
    # practice its next hits open an entity in a different fight.
    was_your_pet: bool = False
    # Append-only for the life of the fight, and deliberately not pruned. Two
    # readers need it: the trailing sum (the newest window's worth) and the
    # rescan a window change forces, which has to re-measure the WHOLE fight
    # under the new span — capping the list would quietly turn that number
    # into "best since you touched the setting". Nothing else walks it, so the
    # cost of keeping it is memory (~112 bytes a hit; ~1.4 MB for the
    # two-hour camp in #86) rather than time.
    hits: list[tuple[datetime, int]] = field(default_factory=list)
    total_damage: int = 0
    highest_hit: int = 0
    trailing_damage: int = 0
    # Best damage done in any one trailing window (TotalTwelveSecondDamage).
    best_window_damage: int = 0
    # The averaging window this entity's numbers are computed over. Carried
    # per entity rather than read from the module constant so the tracker can
    # widen or narrow it from settings; the tracker re-stamps it on every
    # tick, so a change takes effect on live fights without a restart.
    trailing_window: timedelta = TRAILING_WINDOW
    # len(hits) at the last _update_best_window run. The best window is a pure
    # function of hits, so it is recomputed only when a hit is appended, not on
    # every per-tick refresh (which only advances `now`).
    _best_window_hits: int = field(default=0, repr=False)
    # The window `best_window_damage` was accumulated under. Changing the
    # window invalidates it: a best-in-6s is not comparable to a best-in-12s,
    # and the max-merge below would otherwise keep the stale larger number
    # forever.
    _best_window_span: timedelta | None = field(default=None, repr=False)
    # The sliding window that ends at the newest hit, carried between calls:
    # `_best_left` indexes the oldest hit still inside it and `_best_sum` is
    # its damage. Appending a hit advances both by however many hits fell out
    # the back, which is what makes the update amortised O(1) instead of a
    # rescan (#86).
    _best_left: int = field(default=0, repr=False)
    _best_sum: int = field(default=0, repr=False)

    def add_damage(self, timestamp: datetime, damage: int) -> None:
        """EntittyDPS.AddDamage — record one hit (misses arrive as 0)."""
        self.hits.append((timestamp, damage))
        self.total_damage += damage
        if damage > self.highest_hit:
            self.highest_hit = damage
        self.update_trailing(timestamp)

    def update_level(self, level: int | None) -> None:
        """EntittyDPS.Level setter — a level guess only ever raises."""
        if level is not None and (self.level is None or level > self.level):
            self.level = level

    def update_trailing(self, now: datetime, window: timedelta | None = None) -> None:
        """EntittyDPS.UpdateDps — recompute the trailing/best windows.

        Frozen once the entity's target is dead, exactly like the C#.
        """
        if self.death_time is not None:
            # A slain row is frozen, and that has to include its DIVISOR.
            # Adopting a new window here without recomputing `trailing_damage`
            # (which the freeze forbids) would divide the old numerator by the
            # new denominator, so a finished fight's dps would jump the moment
            # someone touched the setting.
            return
        if window is not None:
            self.trailing_window = window
        if not self.hits:
            return
        cutoff = now - self.trailing_window
        # Hits are appended in non-decreasing time order (the best-window
        # two-pointer relies on this too), so the in-window hits are a suffix:
        # sum from the newest and stop at the first hit older than the cutoff.
        total = 0
        for t, d in reversed(self.hits):
            if t < cutoff:
                break
            total += d
        self.trailing_damage = total
        self._update_best_window()

    def _update_best_window(self) -> None:
        """Port of Update12SecondDmg: the max damage in any one-window span.

        The result depends only on ``self.hits`` and the window width (never
        on ``now``), and hits are append-only, so skip the work entirely when
        neither has changed since the last run — this keeps the per-tick
        refresh off the recompute path while producing an identical value.

        When a hit HAS been appended, only the window ending at that hit is a
        new candidate: every earlier one was scored when its own hit arrived
        and is already merged into ``best_window_damage``. So the sweep is
        resumed from ``_best_left``/``_best_sum`` rather than restarted, which
        is what makes recording a fight linear instead of quadratic (#86 — the
        rescan-per-hit cost 93% of all per-line time in a raid corpus, on the
        driver thread, whether or not the DPS window was ever opened).
        """
        window = self.trailing_window
        count = len(self.hits)
        if count == self._best_window_hits and window == self._best_window_span:
            return
        if window != self._best_window_span or count != self._best_window_hits + 1:
            # Either the span moved (a best measured over a different one is
            # not comparable, so the max-merge starts over) or the hit list
            # moved by something other than one append and the carried
            # left/sum no longer describe it. Both are rare; re-measure.
            self._rescan_best_window()
            return
        timestamp, damage = self.hits[-1]
        window_sum = self._best_sum + damage
        left = self._best_left
        # `left < count - 1` keeps the pointer at or behind the new hit: a
        # zero or negative window would otherwise walk it off the end. The
        # old rescan had no such guard and raised IndexError there.
        while left < count - 1 and timestamp - self.hits[left][0] >= window:
            window_sum -= self.hits[left][1]
            left += 1
        self._best_left = left
        self._best_sum = window_sum
        self._best_window_hits = count
        if window_sum > self.best_window_damage:
            self.best_window_damage = window_sum

    def _rescan_best_window(self) -> None:
        """Re-measure the best window over the whole hit list — O(n).

        This is Update12SecondDmg as the C# writes it (and as this file did
        until #86), kept for the paths the incremental update cannot serve.
        Only a window change reaches it in practice — rare, and it re-measures
        the whole fight on purpose, so the number stays "best of this fight"
        rather than "best since the setting moved". It also re-establishes the
        carried left/sum so the incremental updates can resume from it.
        """
        window = self.trailing_window
        if window != self._best_window_span:
            # A best measured over a different span is not comparable; start
            # the max-merge over rather than carry the old number forward.
            self.best_window_damage = 0
            self._best_window_span = window
        self._best_window_hits = len(self.hits)
        best = self.best_window_damage
        window_sum = 0
        left = 0
        for right, (timestamp, damage) in enumerate(self.hits):
            window_sum += damage
            while left < right and timestamp - self.hits[left][0] >= window:
                window_sum -= self.hits[left][1]
                left += 1
            if window_sum > best:
                best = window_sum
        self._best_left = left
        self._best_sum = window_sum
        self.best_window_damage = best

    @property
    def last_damage_time(self) -> datetime | None:
        return self.hits[-1][0] if self.hits else None

    def total_seconds(self, now: datetime) -> int:
        """EntittyDPS.TotalSeconds — fight length, frozen at death."""
        end = self.death_time if self.death_time is not None else now
        return int((end - self.start_time).total_seconds())

    def total_dps(self, now: datetime) -> int:
        """EntittyDPS.TotalDPS — total damage over the whole fight."""
        seconds = self.total_seconds(now)
        if self.total_damage > 0 and seconds > 0:
            return int(self.total_damage / seconds)
        return 0

    @property
    def trailing_dps(self) -> int:
        """EntittyDPS.DPS — trailing damage over the averaging window.

        Always the full window, never elapsed time: a burst of 400 damage two
        seconds into a fight reads as 33, not 200. That is the C# behavior and
        it is why the window is configurable — a shorter one is more
        responsive, a longer one steadier.
        """
        seconds = self.trailing_window.total_seconds()
        if self.trailing_damage > 0 and seconds > 0:
            return int(self.trailing_damage / seconds)
        return 0


@dataclass
class Fight:
    """One target's fight — every attacker's entity, keyed by attacker."""

    target_name: str
    start_time: datetime
    death_time: datetime | None = None
    entities: dict[str, FightEntity] = field(default_factory=dict)

    @property
    def is_dead(self) -> bool:
        return self.death_time is not None

    @property
    def target_total_damage(self) -> int:
        """Sum over the target's group (UpdateDPS group totals)."""
        return sum(e.total_damage for e in self.entities.values())

    @property
    def last_damage_time(self) -> datetime | None:
        """The most recent hit on this target from any attacker."""
        times = [
            entity.last_damage_time
            for entity in self.entities.values()
            if entity.last_damage_time is not None
        ]
        return max(times) if times else None

    def add_damage(
        self,
        attacker_name: str,
        timestamp: datetime,
        damage: int,
        level_guess: int | None = None,
        trailing_window: timedelta = TRAILING_WINDOW,
    ) -> FightEntity:
        entity = self.entities.get(attacker_name.casefold())
        if entity is None:
            entity = FightEntity(
                attacker_name=attacker_name,
                target_name=self.target_name,
                start_time=timestamp,
                trailing_window=trailing_window,
            )
            self.entities[attacker_name.casefold()] = entity
        elif entity.death_time is None:
            # Live rows follow the configured window; a slain one keeps the
            # window it froze under (see FightEntity.update_trailing).
            entity.trailing_window = trailing_window
        entity.add_damage(timestamp, damage)
        entity.update_level(level_guess)
        return entity

    def mark_dead(self, when: datetime) -> None:
        """DPSWindowViewModel.TargetDied — freeze every entity's numbers."""
        if self.death_time is None:
            self.death_time = when
        for entity in self.entities.values():
            if entity.death_time is None:
                entity.death_time = when

    def is_stale(self, now: datetime, retention_s: float = FIGHT_RETENTION_SECONDS) -> bool:
        """No damage against this target for the retention window.

        Keyed on the whole group's last hit, so one attacker still swinging
        keeps every other attacker's row on screen. Unlike the per-entity
        check this replaces, a last-damage time in the *future* (a log line
        stamped ahead of the wall clock) is not treated as stale.

        ``retention_s <= 0`` disables retirement entirely — rows then leave
        only on zone, camp or clear.
        """
        if retention_s <= 0:
            return False
        last = self.last_damage_time
        if last is None or last <= self.start_time:
            last = self.start_time
        return (now - last).total_seconds() > retention_s


@dataclass(frozen=True)
class FightRow:
    """One UI row: an attacker's line under a target group (snapshot)."""

    target_name: str
    attacker_name: str
    level: int | None
    is_dead: bool
    is_your_damage: bool
    # Your pet's row (#81). Kept separate from ``is_your_damage`` rather than
    # folded into it: the window styles both as yours, but the two are not the
    # same claim, and the session footer merges them on different terms.
    is_your_pet: bool
    total_damage: int
    target_total_damage: int
    percent_of_total: int
    trailing_damage: int
    dps: int
    total_dps: int
    highest_hit: int
    total_seconds: int


@dataclass
class PlayerDamage:
    """Session damage stats (Models/PlayerInfo.cs PlayerDamage)."""

    highest_dps: int = 0
    total_damage: int = 0
    highest_hit: int = 0

    def observe(self, dps: int, total_damage: int, highest_hit: int) -> None:
        """Max-merge one reading of your entity (UpdateDPS session block)."""
        self.highest_dps = max(self.highest_dps, dps)
        self.total_damage = max(self.total_damage, total_damage)
        # PlayerDamage.HighestHit setter: >= 32000 readings are bogus.
        if self.highest_hit >= BOGUS_HIT_THRESHOLD:
            self.highest_hit = 0
        if highest_hit < BOGUS_HIT_THRESHOLD:
            self.highest_hit = max(self.highest_hit, highest_hit)


@dataclass(frozen=True)
class SessionSummary:
    """The Best/Current/Last rows at the top of the DPS window."""

    best: PlayerDamage
    current_session: PlayerDamage
    last_session: PlayerDamage | None


class FightTracker:
    """Fight/row lifecycle — port of DPSWindowViewModel minus the WPF.

    The tunables are plain attributes rather than constructor-only, so
    ``configure()`` can move them on a live tracker when the user applies the
    DPS settings page — the app builds one tracker per launch and it outlives
    every settings window.

    Two pieces of state come from outside and are pushed in rather than read:
    ``pet_name`` (the handler follows ``core.pets.PlayerPet``) and the spell
    credit window (``note_your_cast``). Keeping the coupling in the handler
    leaves this a value-in/value-out object that no test needs a bus to
    exercise, and stops ``core.dps`` owning state it would have to keep in
    sync with the pet incident rules.
    """

    def __init__(
        self,
        *,
        damage_sources: DamageSources = DEFAULT_DAMAGE_SOURCES,
        fight_retention_s: float = FIGHT_RETENTION_SECONDS,
        trailing_window_s: float = TRAILING_WINDOW.total_seconds(),
        session_min_fight_s: float = SESSION_MIN_FIGHT_SECONDS,
        spell_credit_window_s: float = SPELL_CREDIT_WINDOW_SECONDS,
        count_pet_damage: bool = COUNT_PET_DAMAGE_DEFAULT,
    ) -> None:
        self._fights: list[Fight] = []
        self.on_change: list[Callable[[], None]] = []
        # BestPlayerDamage persists per character in EQTool; in-memory here.
        self.best = PlayerDamage()
        self.current_session = PlayerDamage()
        self.last_session: PlayerDamage | None = None
        self.damage_sources = damage_sources
        self.fight_retention_s = fight_retention_s
        self.trailing_window_s = trailing_window_s
        self.session_min_fight_s = session_min_fight_s
        self.spell_credit_window_s = spell_credit_window_s
        self.count_pet_damage = count_pet_damage
        #: Your current pet's name, pushed by DpsHandler; "" when you have none.
        self.pet_name = ""
        #: When your most recent cast landed, or is expected to. The credit
        #: DEADLINE is derived from this on every read rather than stored, so
        #: moving ``spell_credit_window_s`` reaches a cast that is already
        #: armed — the same reason ``tick()`` re-stamps the trailing window on
        #: live entities rather than only on new ones. Storing the deadline
        #: baked the window in at arming time, so tightening it mid-raid did
        #: nothing until the next cast, which is exactly when a user reaches
        #: for that setting.
        self._cast_landed_at: datetime | None = None

    def configure(
        self,
        *,
        damage_sources: DamageSources | None = None,
        fight_retention_s: float | None = None,
        trailing_window_s: float | None = None,
        session_min_fight_s: float | None = None,
        spell_credit_window_s: float | None = None,
        count_pet_damage: bool | None = None,
    ) -> None:
        """Move the tunables on a running tracker (settings Apply).

        Every knob here is either read at the point of use or re-stamped, so
        all of them reach work already in flight: the averaging window lands
        on the next tick (which re-stamps every live entity) and the spell
        credit window is derived from ``_cast_landed_at`` on every read, so
        narrowing it retires a cast that is already armed.

        Damage already recorded is not re-filtered: narrowing the damage
        sources mid-fight stops counting new spell damage but does not
        retroactively subtract what is already in a row, because the hit list
        does not keep the damage type. Rows age out within the retention
        window anyway.

        Changing a rule that decides what the session footer MEASURED clears
        the session aggregates — see ``_measurement_rules``.
        """
        before = self._measurement_rules()
        if damage_sources is not None:
            self.damage_sources = damage_sources
        if fight_retention_s is not None:
            self.fight_retention_s = fight_retention_s
        if trailing_window_s is not None:
            self.trailing_window_s = trailing_window_s
        if session_min_fight_s is not None:
            self.session_min_fight_s = session_min_fight_s
        if spell_credit_window_s is not None:
            self.spell_credit_window_s = spell_credit_window_s
        if count_pet_damage is not None:
            self.count_pet_damage = count_pet_damage
        if self._measurement_rules() != before:
            self.reset_session_stats()
        self._notify()

    def _measurement_rules(self) -> tuple[object, ...]:
        """The knobs that change what a session reading MEANS.

        ``fight_retention_s`` is deliberately absent: it decides how long a
        row is displayed, never the value of any reading. The credit window
        and the pet toggle are both here because both change whose damage the
        footer is a reading OF.
        """
        return (
            self.damage_sources,
            self.trailing_window_s,
            self.session_min_fight_s,
            self.spell_credit_window_s,
            self.count_pet_damage,
        )

    def reset_session_stats(self) -> None:
        """Drop Best/Now, keeping ``last_session``.

        The footer aggregates are max-merged, so nothing can be recomputed
        from them — the readings they were built from are gone, and pruned
        fights with them. Once the measuring rules move, the retained maxima
        describe an experiment no longer being run: a best-dps taken over a
        12s window is not comparable to one over 4s (the same reason
        ``best_window_damage`` is invalidated), a best taken while spell
        damage counted is unreachable once the sources narrow to melee, and a
        best from a 6s fight should not survive raising the minimum fight
        length past it. Resetting is the only honest option.

        ``last_session`` is untouched: the user moved it aside deliberately
        with ``end_session()``, so it is a record, not a live measurement.
        """
        self.best = PlayerDamage()
        self.current_session = PlayerDamage()

    @property
    def trailing_window(self) -> timedelta:
        return timedelta(seconds=self.trailing_window_s)

    # -- observation -----------------------------------------------------------

    def _notify(self) -> None:
        for callback in list(self.on_change):
            callback()

    @property
    def fights(self) -> list[Fight]:
        return list(self._fights)

    def active_fight(self, target_name: str) -> Fight | None:
        """The live (not-dead) fight against a target, if any."""
        wanted = target_name.casefold()
        for fight in self._fights:
            if not fight.is_dead and fight.target_name.casefold() == wanted:
                return fight
        return None

    # -- your own casts (the spell-credit window) ---------------------------------

    def note_your_cast(self, when: datetime, cast_time_s: float = 0.0) -> None:
        """Arm the window in which non-melee damage counts as yours (#80).

        Called by ``DpsHandler`` for your own casts: once when the cast
        BEGINS, carrying the spell's cast time, and again if the completion
        line is recognised. Both are needed, and the union is what makes this
        robust:

        - The begin line is the only one guaranteed to precede the damage.
          The client prints the spell's landing message and the "was hit by
          non-melee" line in the same log second, and the log's timestamps
          have one-second resolution, so which of the two the pipeline sees
          first cannot be relied on.
        - The completion line is the tighter signal and covers a cast whose
          beginning was never seen (the app attached to the log mid-cast).

        The armed span therefore runs from the cast's start to the end of its
        cast time plus ``spell_credit_window_s``. Only the landing moment is
        stored (see ``credit_deadline``); arming only ever moves it forward,
        so a chain-caster stays armed for as long as they keep casting —
        which is exactly when their nukes are landing.
        """
        landed = when + timedelta(seconds=max(0.0, cast_time_s))
        if self._cast_landed_at is None or landed > self._cast_landed_at:
            self._cast_landed_at = landed

    def cancel_your_cast(self) -> None:
        """Disarm the credit window — what it was armed for never landed.

        The counterpart to ``note_your_cast``. Arming from the begin line
        means the window is held open for the whole cast time, so a cast
        interrupted a second in would otherwise stay armed for the rest of
        it plus the credit window, handing every nuke that lands in that span
        to you. Deliberately NOT ``clear()``: an interruption says nothing
        about the fights on screen.

        Unconditional, because ``YourSpellInterruptedEvent`` carries no
        spell. It cannot cost a previous cast its tail credit: you cannot
        begin a second cast until the first has resolved, so anything the
        first was going to do has already been decided.
        """
        self._cast_landed_at = None

    @property
    def credit_deadline(self) -> datetime | None:
        """The last moment a non-melee line still counts as your spell.

        Derived rather than stored, so ``spell_credit_window_s`` applies to a
        cast that is ALREADY armed. Storing it froze the window at arming
        time: tightening the setting mid-raid — the one situation it exists
        for — changed nothing until the next cast, and widening it kept
        dropping hits the user had just asked to include.
        """
        if self._cast_landed_at is None:
            return None
        return self._cast_landed_at + timedelta(seconds=self.spell_credit_window_s)

    def _within_cast_credit(self, when: datetime) -> bool:
        """Whether ``when`` falls inside the armed span (inclusive).

        No lower bound is needed: events reach the tracker in log order, so
        damage that predates a cast has already been decided by the time that
        cast arms anything.
        """
        deadline = self.credit_deadline
        return deadline is not None and when <= deadline

    # -- attribution ------------------------------------------------------------

    def _attribute(self, event: DamageEvent) -> str | None:
        """Who a damage event counts for, or ``None`` to drop it.

        DEVIATION from EQTool, deliberate (#80). ``DamageParser.cs`` credits
        "You" for every ``<target> was hit by non-melee for N points`` line —
        that line names no attacker at all, so a 1:1 port had to pick someone
        — and this port inherited it. The consequence was a meter with no
        setting under which a caster's number was both present and true:
        melee-only dropped every point of spell damage, and counting it
        credited you with other players' nukes and opened fights on mobs you
        never touched.

        The parser is left alone (2.2 established that it stays a faithful
        record of what the log said, for triggers and plugins); the decision
        lives here, where the filter already did. The one signal available is
        your own casting, so a non-melee line inside the credit window of one
        of your casts is treated as yours, and one arriving cold is not.

        What that cannot do, stated rather than hidden:

        - Damage shields and weapon procs are non-melee and follow no cast of
          yours, so they land in the unattributed bucket. That is a real
          under-count of a tank's output.
        - Two casters landing nukes in the same window are indistinguishable.
          This is a large improvement over "always You", not a proof.
        - P99 does not log DoT ticks at all (the message is a 2003 addition
          and was removed as non-classic), so damage over time can never
          appear in any mode, whatever the attribution does.
        """
        if is_melee(event.damage_type):
            return event.attacker_name
        mode = self.damage_sources
        if mode == "melee":
            # Dropped before it can open a fight: a lone nuke on a mob nobody
            # is meleeing should not create a group under a melee meter.
            return None
        if event.attacker_name == YOU:
            if self._within_cast_credit(event.timestamp):
                return YOU
            # Not provably yours. Under "all" it still belongs to the target's
            # group — the percentages are wrong without it — so it goes to a
            # pseudo-attacker that says out loud that nobody claimed it.
            return UNATTRIBUTED_SPELL_ATTACKER if mode == "all" else None
        # A non-melee event that already names an attacker did not come from
        # the "was hit by non-melee" line (nothing in the app publishes one
        # today; a plugin might). Take it at its word under "all"; under
        # "melee + mine" it is neither melee nor mine.
        return event.attacker_name if mode == "all" else None

    def set_pet_name(self, name: str) -> None:
        """Follow ``core.pets.PlayerPet`` (pushed by DpsHandler, #81).

        Notifies only on a real change: the pet state also fires on every
        rank guess, which happens on every pet hit.
        """
        if name == self.pet_name:
            return
        self.pet_name = name
        self._notify()

    def _is_your_pet(self, attacker_name: str, target_name: str) -> bool:
        """Whether this attacker is the pet you own RIGHT NOW.

        Two callers, both wanting the present tense: the row flag (a row
        stops being "my pet" the moment the pet dies) and the ownership stamp
        in ``add_damage``, which asks at the one moment the answer is certain.
        What the session footer needs is the past tense, and that is
        ``FightEntity.was_your_pet``.

        Casefolded, like every other name comparison in the module. A charmed
        pet can share an NPC's name, which is why ``add_damage`` refuses an
        event whose attacker equals its target; the flag refuses the same
        case, so a mob that happens to be named like your charm is never
        painted as yours. Another player's pet of the same name still is —
        the log gives nothing that separates them.
        """
        if not self.pet_name:
            return False
        pet = self.pet_name.casefold()
        return attacker_name.casefold() == pet and target_name.casefold() != pet

    # -- damage intake (DPSWindowViewModel.TryAdd) --------------------------------

    def add_damage(self, event: DamageEvent) -> None:
        # Charmed pets sharing an NPC's name make attacker == target; skip.
        if not event.attacker_name or event.attacker_name == event.target_name:
            return
        attacker = self._attribute(event)
        if attacker is None:
            return
        fight = self.active_fight(event.target_name)
        if fight is None:
            fight = Fight(target_name=event.target_name, start_time=event.timestamp)
            self._fights.append(fight)
        entity = fight.add_damage(
            attacker,
            event.timestamp,
            event.damage_done,
            event.level_guess,
            self.trailing_window,
        )
        # Stamp ownership as the damage lands, not when the footer is read:
        # by then the pet may be dead, reclaimed or replaced.
        if not entity.was_your_pet and self._is_your_pet(attacker, fight.target_name):
            entity.was_your_pet = True
        # A level guess describes the attacker: apply it to every row where
        # that NPC is the *target* (TryAdd's trailing loop).
        if event.level_guess is not None:
            folded = attacker.casefold()
            for other in self._fights:
                if other.target_name.casefold() == folded:
                    for entity in other.entities.values():
                        entity.update_level(event.level_guess)
        self._notify()

    # -- fight end (DPSWindowViewModel.TargetDied) ---------------------------------

    def end_fight(self, victim: str, when: datetime) -> bool:
        """Mark every fight against ``victim`` dead. Returns True if any was."""
        if not victim or victim.isspace():
            return False
        wanted = victim.casefold()
        ended = False
        for fight in self._fights:
            if fight.target_name.casefold() == wanted and not fight.is_dead:
                fight.mark_dead(when)
                ended = True
        if ended:
            self._update_session_stats(when)
            self._notify()
        return ended

    def clear(self, update_stats_at: datetime | None = None) -> None:
        """Drop all fights (zone change / camp / player death)."""
        if update_stats_at is not None:
            self._update_session_stats(update_stats_at)
        # Zoning, camping and dying all cancel whatever you were casting, so
        # the credit window must not survive them and hand the first nuke on
        # the other side to you.
        self.cancel_your_cast()
        if self._fights:
            self._fights.clear()
            self._notify()

    # -- periodic update (DPSWindowViewModel.UpdateDPS) -----------------------------

    def tick(self, now: datetime) -> None:
        """Retire stale fights, refresh trailing windows, roll session stats."""
        window = self.trailing_window
        for fight in self._fights:
            for entity in fight.entities.values():
                # Re-stamping the window here is what makes a settings change
                # reach fights that are already running.
                entity.update_trailing(now, window)
        before = len(self._fights)
        # Whole groups only — an attacker is never dropped out from under a
        # fight that is still being fought (see FIGHT_RETENTION_SECONDS).
        self._fights = [
            fight
            for fight in self._fights
            if fight.entities and not fight.is_stale(now, self.fight_retention_s)
        ]
        removed = before - len(self._fights)
        self._update_session_stats(now)
        if self._fights or removed:
            self._notify()

    def _update_session_stats(self, now: datetime) -> None:
        """UpdateDPS session block: max-merge your reading into Best/Current.

        DEVIATION from EQTool, deliberate (#81): the reading is you AND your
        pet, not just the entity named You. EQTool's PetHandler used pet
        damage only to guess the pet's rank from its max melee hit and never
        for attribution, so a magician's pet — often most of their output —
        reached the footer nowhere at all. Summing is what makes the footer
        the player's combined number; the pet keeps its own row because
        whether the pet is holding up is information a mage wants, and
        merging the rows would make ``highest_hit`` and the per-row dps
        meaningless.

        ``highest_hit`` stays yours alone: it reads as your own crit, and the
        pet's biggest swing is not that. The fight-length gate takes the
        longer of the two, so a pet that opened 30s before you joined carries
        the pair past the minimum.

        Pets are selected by ``FightEntity.was_your_pet`` — ownership stamped
        when each hit landed — NOT by the current ``pet_name``. A pet that
        dies, is reclaimed or is replaced part-way through a fight clears
        that name, and asking it here would have thrown away everything the
        pet had already contributed to a fight still running. More than one
        entity can qualify, which is the resummon case and is correct: both
        pets were yours.
        """
        you_key = YOU.casefold()
        for fight in self._fights:
            yours = fight.entities.get(you_key)
            mine = [yours] if yours is not None else []
            if self.count_pet_damage:
                mine.extend(
                    entity
                    for key, entity in fight.entities.items()
                    if key != you_key and entity.was_your_pet
                )
            if not mine:
                continue
            if max(entity.total_seconds(now) for entity in mine) <= self.session_min_fight_s:
                continue
            dps = sum(entity.trailing_dps for entity in mine)
            total = sum(entity.total_damage for entity in mine)
            highest = yours.highest_hit if yours is not None else 0
            for stats in (self.best, self.current_session):
                stats.observe(dps, total, highest)

    # -- session stats (DPSMeter session buttons) -----------------------------------

    def session_summary(self) -> SessionSummary:
        return SessionSummary(
            best=replace(self.best),
            current_session=replace(self.current_session),
            last_session=replace(self.last_session) if self.last_session else None,
        )

    def end_session(self) -> None:
        """MoveCurrentToLastSession: current -> last, start a fresh current."""
        self.last_session = self.current_session
        self.current_session = PlayerDamage()
        self._notify()

    def remove_last_session(self) -> None:
        self.last_session = None
        self._notify()

    # -- UI snapshot ------------------------------------------------------------

    def snapshot(self, now: datetime) -> list[FightRow]:
        """Rows grouped by target (fight order), sorted by damage desc."""
        rows: list[FightRow] = []
        for fight in self._fights:
            target_total = fight.target_total_damage
            entities = sorted(fight.entities.values(), key=lambda e: e.total_damage, reverse=True)
            for entity in entities:
                percent = int(entity.total_damage / target_total * 100.0) if target_total > 0 else 0
                rows.append(
                    FightRow(
                        target_name=fight.target_name,
                        attacker_name=entity.attacker_name,
                        level=entity.level,
                        is_dead=fight.is_dead,
                        is_your_damage=entity.attacker_name == YOU,
                        is_your_pet=self._is_your_pet(entity.attacker_name, fight.target_name),
                        total_damage=entity.total_damage,
                        target_total_damage=target_total,
                        percent_of_total=percent,
                        trailing_damage=entity.trailing_damage,
                        dps=entity.trailing_dps,
                        total_dps=entity.total_dps(now),
                        highest_hit=entity.highest_hit,
                        total_seconds=entity.total_seconds(now),
                    )
                )
        return rows
