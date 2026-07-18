"""SpawnTimerHandler — respawn timers from kill confirmations.

Port of EQTool's Services/Handlers/SlainHandler.cs: correlates SlainEvent,
FactionEvent, and ExpGainedEvent streams into ConfirmedDeathEvents and adds
"--Dead-- <victim>" respawn TimerRows using the zone spawn-time database.

Correlation semantics (from the C#):

- A SlainEvent for anyone but You is a confirmed death, published at once.
- Faction hits and exp gains are *guesses*: they are held until the next
  log line that is not part of the same burst. If that burst immediately
  followed a real SlainEvent the guess is discarded (the kill was already
  emitted); otherwise a "Faction Slain"/"Exp Slain" ConfirmedDeathEvent is
  published so a kill without a slain message still starts a timer.
- Within a faction burst, seeing the first faction line repeat, or a 7th
  faction line, closes the previous kill and starts a new one; a second exp
  message likewise closes the previous exp kill.

Spawn timers are only created for victims present in the master NPC list
(never for players), except the Faction/Exp Slain guesses which always get
one. Kills of the listed Plane of Sky bosses also start the 15-minute
"--Sirran the Lunatic--" timer.

TODO(M3): EQTool's ZoneActivityTrackingService also shares "Kael faction mob
engaged" pulls (DamageEvent target in ZoneDatabase.kael_faction_mobs,
rate-limited to one send per 15s) over SignalR; the send is network-only and
is left to the M3 sharing layer.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from nparseplus.core.bus import EventBus
from nparseplus.core.events import (
    BeforePlayerChangedEvent,
    ConfirmedDeathEvent,
    ExpGainedEvent,
    FactionEvent,
    LineEvent,
    SlainEvent,
)
from nparseplus.core.handlers.base import BaseHandler
from nparseplus.core.player import ActivePlayer
from nparseplus.core.timers import MOB_TIMER_GROUP, TimerRow, TimersService
from nparseplus.core.zones import ZoneDatabase

# Plane of Sky bosses whose death starts the Sirran the Lunatic respawn.
POS_BOSSES = frozenset(
    {
        "a Thunder Spirit Princess",
        "Protector of Sky",
        "Gorgalosk",
        "Keeper of Souls",
        "The Spiroc Lord",
        "Bazzt Zzzt",
        "Sister of the Spire",
    }
)

SIRRAN_TIMER_NAME = "--Sirran the Lunatic-- "
SIRRAN_TIMER_SECONDS = 15 * 60

FACTION_SLAIN = "Faction Slain"
EXP_SLAIN = "Exp Slain"
YOU = "You"

# A faction burst is closed after this many lines (FactionMessages.Count == 6).
_MAX_FACTION_MESSAGES = 6
_NO_LINE = -100  # sentinel line number that can never match


class SpawnTimerHandler(BaseHandler):
    def __init__(
        self,
        bus: EventBus,
        player: ActivePlayer,
        timers: TimersService,
        zones: ZoneDatabase,
        npcs: frozenset[str] = frozenset(),
        timer_recast: Callable[[], str] | None = None,
    ) -> None:
        super().__init__(bus, player)
        self.timers = timers
        self.zones = zones
        self.npcs = npcs
        # Per-character PlayerInfo.TimerRecastSetting (see SpellTimerHandler).
        self.timer_recast = timer_recast or (lambda: "RestartCurrentTimer")
        self._victim = ""
        self._killer = ""
        self._faction_messages: list[str] = []
        self._exp_message = False
        self._line_number = _NO_LINE
        self._already_emitted = False
        bus.subscribe(SlainEvent, self._on_slain)
        bus.subscribe(FactionEvent, self._on_faction)
        bus.subscribe(ExpGainedEvent, self._on_exp_gained)
        bus.subscribe(BeforePlayerChangedEvent, self._on_player_changed)
        bus.subscribe(LineEvent, self._on_line)

    # -- correlation ----------------------------------------------------------

    def _reset(self) -> None:
        self._line_number = _NO_LINE
        self._victim = self._killer = ""
        self._faction_messages.clear()
        self._exp_message = False
        self._already_emitted = False

    def _on_line(self, event: LineEvent) -> None:
        if event.line_number == self._line_number:
            return
        if event.line_number - 1 == self._line_number:
            if self._already_emitted:
                self._reset()
            elif self._exp_message or self._faction_messages:
                self._do_event(event.timestamp, event.line, event.line_number, guess=True)
                self._reset()

    def _on_player_changed(self, event: BeforePlayerChangedEvent) -> None:
        self._reset()

    def _on_exp_gained(self, event: ExpGainedEvent) -> None:
        self._victim = EXP_SLAIN
        self._killer = YOU
        if self._exp_message:
            self._do_event(event.timestamp, event.line, event.line_number, guess=True)
            self._reset()
            self._already_emitted = True
        self._victim = EXP_SLAIN
        self._killer = YOU
        self._line_number = event.line_number
        self._exp_message = True

    def _on_faction(self, event: FactionEvent) -> None:
        self._line_number = event.line_number
        self._victim = FACTION_SLAIN
        self._killer = YOU
        if (self._faction_messages and self._faction_messages[0] == event.line) or len(
            self._faction_messages
        ) == _MAX_FACTION_MESSAGES:
            self._do_event(event.timestamp, event.line, event.line_number, guess=True)
            self._reset()
        self._line_number = event.line_number
        self._victim = FACTION_SLAIN
        self._killer = YOU
        self._faction_messages.append(event.line)

    def _on_slain(self, event: SlainEvent) -> None:
        if event.victim == YOU:
            return
        self._victim = event.victim
        self._killer = event.killer
        self._line_number = event.line_number
        self._do_event(event.timestamp, event.line, event.line_number, guess=False)
        self._already_emitted = True

    # -- output ----------------------------------------------------------------

    def _do_event(self, timestamp: datetime, line: str, line_number: int, guess: bool) -> None:
        victim, killer = self._victim, self._killer
        self.bus.publish(
            ConfirmedDeathEvent(
                timestamp=timestamp,
                line=line,
                line_number=line_number,
                victim=victim,
                killer=killer,
            )
        )
        is_npc = victim.casefold() in self.npcs
        is_player = victim in self.player.known_players
        if is_player or (not is_npc and not guess):
            return

        spawn_seconds = self.zones.spawn_time(victim, self.player.zone)
        name = f"--Dead-- {victim}"
        # The victim's buff rows die with it — but only under RestartCurrentTimer;
        # with StartNewTimer another same-named mob may own stacked rows in the
        # same group, so the C# SlainHandler leaves them to expire naturally.
        if self.timer_recast() == "RestartCurrentTimer":
            self.timers.remove_group(f" {victim}" if is_npc else victim)
        name = self._unique_dead_name(name)
        self.timers.add_timer(
            TimerRow(
                name=name,
                group=MOB_TIMER_GROUP,
                updated_at=timestamp,
                ends_at=timestamp + timedelta(seconds=spawn_seconds),
                total_duration_s=float(spawn_seconds),
            ),
            allow_duplicates=True,
        )
        if any(boss.casefold() == victim.casefold() for boss in POS_BOSSES):
            self.timers.add_timer(
                TimerRow(
                    name=SIRRAN_TIMER_NAME,
                    group=MOB_TIMER_GROUP,
                    updated_at=timestamp,
                    ends_at=timestamp + timedelta(seconds=SIRRAN_TIMER_SECONDS),
                    total_duration_s=float(SIRRAN_TIMER_SECONDS),
                )
            )

    def _unique_dead_name(self, base: str) -> str:
        """Smallest-free-number suffix for a "--Dead-- <victim>" row.

        Bare ``base`` when no active row owns it; else the smallest ``N >= 1``
        such that ``f"{base}_{N}"`` is free (scan bounded at 999). Expired rows
        are dropped by TimersService.tick(), so freed names/suffixes are reused
        naturally. Diverges from the C# (a monotonic session counter): freed
        suffixes there climb forever and are never reused. First collision now
        yields ``_1`` (was ``_2``).
        """
        if self.timers.find(base, MOB_TIMER_GROUP) is None:
            return base
        for n in range(1, 1000):
            candidate = f"{base}_{n}"
            if self.timers.find(candidate, MOB_TIMER_GROUP) is None:
                return candidate
        return f"{base}_999"
