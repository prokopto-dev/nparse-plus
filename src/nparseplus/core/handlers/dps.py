"""DpsHandler — feeds the FightTracker from bus events.

Port of the event wiring in EQTool's UI/DPSMeter.xaml.cs (DamageEvent ->
TryAdd, SlainEvent -> TargetDied) plus ConfirmedDeathEvent from
SlainHandler for the exp/faction-confirmed kills the slain line misses.

EQTool never cleared the DPS window on zoning or camping — rows simply aged
out via ShouldRemove. nparseplus clears active fights on zone change, camp,
the loading screen, and a character switch instead, folding your stats into
the session totals first so nothing is lost.

The character switch is the one with a second reason: a fight left on screen
across it is the *previous* character's, and every later ``end_fight`` or
``tick`` would re-merge that character's damage into the incoming one's
session — a bleed the per-character Best (#83) exists to prevent. It hangs on
``BeforePlayerChangedEvent`` rather than After, so the fold happens while the
outgoing character is still the active one — which is also what lets
``DpsPersistenceHandler`` restore on After without racing anything.

Two subscriptions have no EQTool counterpart, and both exist so the tracker
can stay a value-in/value-out object (see ``FightTracker``): your own casts
arm its spell-credit window (#80), and the pet state it already maintains in
``core.pets`` is mirrored onto it (#81).

One publication has no counterpart either. EQTool decided "is this victim
worth copying the parse for" inline in ``LogParser_DeathEvent`` and copied
from the same method, which WPF let it do because there was no thread to
cross. Here the write is the GUI's and the decision is not: it turns on the
zone the kill happened IN, and the Qt bridge delivers a coalesced batch some
time after the driver parsed it — by which point ``player.zone`` may name a
zone reached after the kill, or after a character switch. So the question is
answered here, on the driver thread, where the zone is unambiguous and
ordered, and the answer travels as ``NotableKillEvent``. The window is left
with the two things that really are the UI's: whether the user asked for
automatic copies, and the clipboard.
"""

from __future__ import annotations

from nparseplus.core.bus import EventBus
from nparseplus.core.dps import FightTracker, fight_parse
from nparseplus.core.events import (
    BeforePlayerChangedEvent,
    CampEvent,
    ConfirmedDeathEvent,
    DamageEvent,
    LoadingPleaseWaitEvent,
    NotableKillEvent,
    SlainEvent,
    YouBeginCastingEvent,
    YouFinishCastingEvent,
    YourSpellInterruptedEvent,
    YouZonedEvent,
)
from nparseplus.core.handlers.base import BaseHandler
from nparseplus.core.pets import PlayerPet
from nparseplus.core.player import ActivePlayer
from nparseplus.core.zones import ZoneDatabase

# Pseudo-victims SlainHandler emits for exp/faction-only confirmations; they
# never name a fight target.
_PSEUDO_VICTIMS = frozenset({"exp slain", "faction slain"})


class DpsHandler(BaseHandler):
    def __init__(
        self,
        bus: EventBus,
        player: ActivePlayer,
        tracker: FightTracker,
        player_pet: PlayerPet | None = None,
        zones: ZoneDatabase | None = None,
    ) -> None:
        super().__init__(bus, player)
        self.tracker = tracker
        self.player_pet = player_pet
        # Only for the notable-kill question; None simply never asks it.
        self.zones = zones
        bus.subscribe(DamageEvent, self._on_damage)
        bus.subscribe(SlainEvent, self._on_slain)
        bus.subscribe(ConfirmedDeathEvent, self._on_confirmed_death)
        bus.subscribe(YouZonedEvent, self._on_zoned)
        bus.subscribe(LoadingPleaseWaitEvent, self._on_loading)
        bus.subscribe(CampEvent, self._on_camp)
        bus.subscribe(BeforePlayerChangedEvent, self._on_player_changed)
        bus.subscribe(YouBeginCastingEvent, self._on_begin_casting)
        bus.subscribe(YouFinishCastingEvent, self._on_finish_casting)
        bus.subscribe(YourSpellInterruptedEvent, self._on_cast_interrupted)
        if player_pet is not None:
            # PetHandler already owns the CREATION/LEADER/RECLAIMED/DEATH
            # rules and the resets on zone, camp, charm break and your own
            # death; following its state is what keeps those from being
            # written twice and drifting.
            player_pet.on_change.append(self._sync_pet_name)
            self._sync_pet_name()

    def _sync_pet_name(self) -> None:
        if self.player_pet is not None:
            self.tracker.set_pet_name(self.player_pet.pet_name)

    def _on_begin_casting(self, event: YouBeginCastingEvent) -> None:
        # Only a detrimental spell can be the source of a damage line, so a
        # cleric chain-healing through a raid never arms the window and never
        # collects someone else's nuke. A detrimental spell that does no
        # damage (a root, a snare) does arm it — the spell file says what a
        # spell is FOR, not what it does.
        if event.spell.is_detrimental:
            self.tracker.note_your_cast(event.timestamp, event.spell.cast_time_ms / 1000.0)

    def _on_finish_casting(self, event: YouFinishCastingEvent) -> None:
        if event.spell.is_detrimental:
            self.tracker.note_your_cast(event.timestamp)

    def _on_cast_interrupted(self, event: YourSpellInterruptedEvent) -> None:
        # "Your spell is interrupted." — the cast the begin line armed the
        # window for never landed, so nothing that follows is yours.
        #
        # A resist is deliberately NOT treated this way. A full resist does
        # print "Your target resisted the <spell> spell.", but a PARTIAL one
        # prints nothing and still does damage, so disarming on the resist
        # event risks discarding a caster's real damage — the exact failure
        # this whole attribution path exists to fix — to close a window that
        # is at most the credit window wide.
        self.tracker.cancel_your_cast()

    def _on_damage(self, event: DamageEvent) -> None:
        self.tracker.add_damage(event)

    def _on_slain(self, event: SlainEvent) -> None:
        # Before end_fight, the order LogParser_DeathEvent uses. Neither the
        # answer nor the parse depends on it — the fight is only marked dead,
        # never removed — but there is no reason to diverge.
        self._announce_notable_kill(event)
        # Your own death arrives as victim == "You": EQTool just froze the
        # fights targeting You (TargetDied), same as any other victim.
        self.tracker.end_fight(event.victim, event.timestamp)

    def _announce_notable_kill(self, event: SlainEvent) -> None:
        """State that this kill is worth copying, and what the parse says (#78).

        Both halves are answered HERE, on the driver thread, one statement
        after the line that produced the kill — the only moment at which either
        is unambiguous.

        The zone, because ``player.zone`` keeps moving while the Qt bridge
        holds the batch. The parse, because the meter itself does: zoning
        clears it (``_on_zoned``), so a boss killed on the way out of the zone
        has no rows left by the time the GUI drains, and a window that
        formatted then would copy nothing. Formatting at the kill also dates
        the numbers to the kill — ``total_dps`` and ``total_seconds`` come out
        against the log's own clock rather than whenever the GUI happened to
        wake up.

        Before ``end_fight``, which is EQTool's order too; the group is only
        marked dead, never removed, so the rows are the same either way.

        Deliberately only ``SlainEvent``, not ``ConfirmedDeathEvent``: EQTool
        hangs the copy on the slain line alone, and the exp/faction
        confirmations that event exists for name no zone-notable NPC.
        """
        if self.zones is None:
            return
        zone = self.player.zone
        if not self.zones.is_notable_kill(event.victim, zone):
            return
        parse = fight_parse(self.tracker.snapshot(event.timestamp), event.victim)
        if not parse:
            # Notable, but nothing landed on it that this meter counted, so
            # there is no parse to put on anyone's clipboard.
            return
        self.bus.publish(
            NotableKillEvent(timestamp=event.timestamp, victim=event.victim, zone=zone, parse=parse)
        )

    def _on_confirmed_death(self, event: ConfirmedDeathEvent) -> None:
        if event.victim.casefold() in _PSEUDO_VICTIMS:
            return
        self.tracker.end_fight(event.victim, event.timestamp)

    def _on_zoned(self, event: YouZonedEvent) -> None:
        self.tracker.clear(update_stats_at=event.timestamp)

    def _on_loading(self, event: LoadingPleaseWaitEvent) -> None:
        self.tracker.clear(update_stats_at=event.timestamp)

    def _on_camp(self, event: CampEvent) -> None:
        self.tracker.clear(update_stats_at=event.timestamp)

    def _on_player_changed(self, event: BeforePlayerChangedEvent) -> None:
        # Still the outgoing character here, so their last fight folds into
        # their own session stats before the rows go — see the module docstring.
        self.tracker.clear(update_stats_at=event.timestamp)
