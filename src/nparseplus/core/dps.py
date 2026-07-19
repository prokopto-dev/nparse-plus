"""DPS/fight engine — Qt-free port of EQTool's EntittyDPS + DPSWindowViewModel.

``FightEntity`` ports EntittyDPS.cs (per attacker/target damage list, the
12-second trailing window, best-12s window, highest hit). ``Fight`` groups
the entities attacking one target (the WPF grouping by TargetName).
``FightTracker`` ports the DPSWindowViewModel row lifecycle: TryAdd on
damage, TargetDied on slain, ShouldRemove staleness pruning, and the
session Best/Current/Last PlayerDamage stats maintained in UpdateDPS.

Deviations from EQTool are noted inline; the main one is that the first hit
of an entity is appended to its damage list (EQTool only seeded the totals),
so trailing damage decays correctly for one-hit entities.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from nparseplus.core.events import DamageEvent

# EQSpells.You — the active player's name in damage lines.
YOU = "You"

# The trailing-DPS window (EntittyDPS.UpdateDps).
TRAILING_WINDOW = timedelta(seconds=12)

# Rows are pruned this long after their last damage (DPSWindowViewModel.ShouldRemove).
STALE_AFTER_SECONDS = 40.0

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
    # Best damage done in any 12-second window (TotalTwelveSecondDamage).
    best_window_damage: int = 0
    # len(hits) at the last _update_best_window run. The best window is a pure
    # function of hits, so it is recomputed only when a hit is appended, not on
    # every per-tick refresh (which only advances `now`).
    _best_window_hits: int = field(default=0, repr=False)

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

    def update_trailing(self, now: datetime) -> None:
        """EntittyDPS.UpdateDps — recompute the 12s trailing/best windows.

        Frozen once the entity's target is dead, exactly like the C#.
        """
        if self.death_time is not None:
            return
        if not self.hits:
            return
        cutoff = now - TRAILING_WINDOW
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
        """Port of Update12SecondDmg: the max damage in any 12s span.

        The result depends only on ``self.hits`` (never on ``now``), and hits
        are append-only, so skip the O(n) rescan when no hit has been added
        since the last run — this keeps the per-tick refresh off the quadratic
        path while producing an identical value.
        """
        if len(self.hits) == self._best_window_hits:
            return
        self._best_window_hits = len(self.hits)
        span = self.hits[-1][0] - self.hits[0][0]
        if span < TRAILING_WINDOW:
            self.best_window_damage = max(self.best_window_damage, self.total_damage)
            return
        best = self.best_window_damage
        window_sum = 0
        left = 0
        for right, (t_right, damage) in enumerate(self.hits):
            window_sum += damage
            while t_right - self.hits[left][0] >= TRAILING_WINDOW:
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
        """EntittyDPS.DPS — trailing damage over the 12s window."""
        if self.trailing_damage > 0:
            return int(self.trailing_damage / TRAILING_WINDOW.total_seconds())
        return 0

    def is_stale(self, now: datetime) -> bool:
        """DPSWindowViewModel.ShouldRemove — 40s with no damage."""
        last = self.last_damage_time
        if last is None or last <= self.start_time:
            last = self.start_time
        return abs((now - last).total_seconds()) > STALE_AFTER_SECONDS


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

    def add_damage(
        self,
        attacker_name: str,
        timestamp: datetime,
        damage: int,
        level_guess: int | None = None,
    ) -> FightEntity:
        entity = self.entities.get(attacker_name.casefold())
        if entity is None:
            entity = FightEntity(
                attacker_name=attacker_name,
                target_name=self.target_name,
                start_time=timestamp,
            )
            self.entities[attacker_name.casefold()] = entity
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

    def prune_stale(self, now: datetime) -> int:
        """Drop entities with no damage for 40s. Returns count removed."""
        stale = [key for key, entity in self.entities.items() if entity.is_stale(now)]
        for key in stale:
            del self.entities[key]
        return len(stale)


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
    """Fight/row lifecycle — port of DPSWindowViewModel minus the WPF."""

    def __init__(self) -> None:
        self._fights: list[Fight] = []
        self.on_change: list[Callable[[], None]] = []
        # BestPlayerDamage persists per character in EQTool; in-memory here.
        self.best = PlayerDamage()
        self.current_session = PlayerDamage()
        self.last_session: PlayerDamage | None = None

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
        fight = self.active_fight(event.target_name)
        if fight is None:
            fight = Fight(target_name=event.target_name, start_time=event.timestamp)
            self._fights.append(fight)
        fight.add_damage(event.attacker_name, event.timestamp, event.damage_done, event.level_guess)
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
        """Prune stale rows, refresh trailing windows, roll session stats."""
        removed = 0
        for fight in self._fights:
            removed += fight.prune_stale(now)
            for entity in fight.entities.values():
                entity.update_trailing(now)
        before = len(self._fights)
        self._fights = [fight for fight in self._fights if fight.entities]
        removed += before - len(self._fights)
        self._update_session_stats(now)
        if self._fights or removed:
            self._notify()

    def _update_session_stats(self, now: datetime) -> None:
        """UpdateDPS session block: max-merge your entity into Best/Current."""
        for fight in self._fights:
            for entity in fight.entities.values():
                if entity.attacker_name != YOU:
                    continue
                if entity.total_seconds(now) <= SESSION_MIN_FIGHT_SECONDS:
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
