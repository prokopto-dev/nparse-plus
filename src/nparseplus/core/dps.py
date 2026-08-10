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
``FIGHT_RETENTION_SECONDS``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from nparseplus.core.damagetypes import is_melee
from nparseplus.core.events import DamageEvent

# EQSpells.You — the active player's name in damage lines.
YOU = "You"

# The trailing-DPS window (EntittyDPS.UpdateDps).
TRAILING_WINDOW = timedelta(seconds=12)

# Melee swings only, by default: a melee meter is what the window is for, and
# folding spell damage in makes it lie in both directions — "<target> was hit
# by non-melee" carries no attacker, so the parser credits YOU for every
# proc and nuke in range, including other players'. Off means rows count
# everything the parser attributes, warts included.
MELEE_ONLY_DEFAULT = True

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
        if window is not None:
            self.trailing_window = window
        if self.death_time is not None:
            return
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
        on ``now``), and hits are append-only, so skip the O(n) rescan when
        neither has changed since the last run — this keeps the per-tick
        refresh off the quadratic path while producing an identical value.
        """
        window = self.trailing_window
        if len(self.hits) == self._best_window_hits and window == self._best_window_span:
            return
        if window != self._best_window_span:
            # A best measured over a different span is not comparable; start
            # the max-merge over rather than carry the old number forward.
            self.best_window_damage = 0
            self._best_window_span = window
        self._best_window_hits = len(self.hits)
        span = self.hits[-1][0] - self.hits[0][0]
        if span < window:
            self.best_window_damage = max(self.best_window_damage, self.total_damage)
            return
        best = self.best_window_damage
        window_sum = 0
        left = 0
        for right, (t_right, damage) in enumerate(self.hits):
            window_sum += damage
            while t_right - self.hits[left][0] >= window:
                window_sum -= self.hits[left][1]
                left += 1
            if right >= left:
                best = max(best, window_sum)
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
        entity.update_trailing(timestamp, trailing_window)
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

    The four tunables are plain attributes rather than constructor-only, so
    ``configure()`` can move them on a live tracker when the user applies the
    DPS settings page — the app builds one tracker per launch and it outlives
    every settings window.
    """

    def __init__(
        self,
        *,
        melee_only: bool = MELEE_ONLY_DEFAULT,
        fight_retention_s: float = FIGHT_RETENTION_SECONDS,
        trailing_window_s: float = TRAILING_WINDOW.total_seconds(),
        session_min_fight_s: float = SESSION_MIN_FIGHT_SECONDS,
    ) -> None:
        self._fights: list[Fight] = []
        self.on_change: list[Callable[[], None]] = []
        # BestPlayerDamage persists per character in EQTool; in-memory here.
        self.best = PlayerDamage()
        self.current_session = PlayerDamage()
        self.last_session: PlayerDamage | None = None
        self.melee_only = melee_only
        self.fight_retention_s = fight_retention_s
        self.trailing_window_s = trailing_window_s
        self.session_min_fight_s = session_min_fight_s

    def configure(
        self,
        *,
        melee_only: bool | None = None,
        fight_retention_s: float | None = None,
        trailing_window_s: float | None = None,
        session_min_fight_s: float | None = None,
    ) -> None:
        """Move the tunables on a running tracker (settings Apply).

        Only the averaging window needs anything beyond an assignment, and
        even that lands on the next tick, which re-stamps every live entity.
        Damage already recorded is not re-filtered: turning melee-only ON
        mid-fight stops counting new spell damage but does not retroactively
        subtract what is already in a row, because the hit list does not keep
        the damage type. Rows age out within the retention window anyway.
        """
        if melee_only is not None:
            self.melee_only = melee_only
        if fight_retention_s is not None:
            self.fight_retention_s = fight_retention_s
        if trailing_window_s is not None:
            self.trailing_window_s = trailing_window_s
        if session_min_fight_s is not None:
            self.session_min_fight_s = session_min_fight_s
        self._notify()

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

    # -- damage intake (DPSWindowViewModel.TryAdd) --------------------------------

    def add_damage(self, event: DamageEvent) -> None:
        # Charmed pets sharing an NPC's name make attacker == target; skip.
        if not event.attacker_name or event.attacker_name == event.target_name:
            return
        # Melee-only drops the event before it can open a fight: a lone nuke
        # on a mob nobody is meleeing should not create an empty group.
        if self.melee_only and not is_melee(event.damage_type):
            return
        fight = self.active_fight(event.target_name)
        if fight is None:
            fight = Fight(target_name=event.target_name, start_time=event.timestamp)
            self._fights.append(fight)
        fight.add_damage(
            event.attacker_name,
            event.timestamp,
            event.damage_done,
            event.level_guess,
            self.trailing_window,
        )
        # A level guess describes the attacker: apply it to every row where
        # that NPC is the *target* (TryAdd's trailing loop).
        if event.level_guess is not None:
            attacker = event.attacker_name.casefold()
            for other in self._fights:
                if other.target_name.casefold() == attacker:
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
        """UpdateDPS session block: max-merge your entity into Best/Current."""
        for fight in self._fights:
            for entity in fight.entities.values():
                if entity.attacker_name != YOU:
                    continue
                if entity.total_seconds(now) <= self.session_min_fight_s:
                    continue
                for stats in (self.best, self.current_session):
                    stats.observe(entity.trailing_dps, entity.total_damage, entity.highest_hit)

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
