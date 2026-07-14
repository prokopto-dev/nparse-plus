"""SpellTimerHandler — drives TimersService from the spell events.

Consolidated port of EQTool's spell handlers (Services/Handlers):
YouBeginCastingHandler, YouFinishCastingHandler, SpellCastOnYouHandler,
SpellCastOnOtherHandler, SpellWornOffSelfHandler, SpellWornOffOtherHandler,
YouForgetHandler, YouHaveFinishedMemorizingHandler, ResistHandler, and the
row-creating core SpellHandlerService.Handle.

Known divergences from EQTool:
- ResistHandler consulted FightHistory for the current target; fight history
  is not ported yet, so resists increment the first matching counter row and
  never create one.
- The TimerRecast=StartNewTimer setting (stacked detrimental timers on NPCs)
  is not ported; timers always refresh/overwrite.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from nparseplus.core.bus import EventBus
from nparseplus.core.events import (
    LineEvent,
    ResistSpellEvent,
    SpellCastOnOtherEvent,
    SpellCastOnYouEvent,
    SpellWornOffOtherEvent,
    SpellWornOffSelfEvent,
    TimerBarEvent,
    YouBeginCastingEvent,
    YouFinishCastingEvent,
    YouForgetEvent,
    YouHaveFinishedMemorizingEvent,
)
from nparseplus.core.handlers.base import BaseHandler
from nparseplus.core.player import ActivePlayer
from nparseplus.core.spells.counters import CounterLists, load_counter_lists
from nparseplus.core.spells.durations import get_duration_seconds
from nparseplus.core.spells.matching import match_closest_level_to_spell
from nparseplus.core.spells.models import Spell
from nparseplus.core.spells.spells_us import (
    MINIMUM_RECAST_FOR_YOU_COOLDOWN_TIMER_S,
    SpellBook,
)
from nparseplus.core.timers import (
    YOU_GROUP,
    CounterRow,
    SpellRow,
    TimerRow,
    TimersService,
)

# Spells with long recast times that get a cooldown row (SpellHandlerService).
SPELLS_THAT_NEED_TIMERS = frozenset(
    {
        "Dictate",
        "Divine Aura",
        "Divine Barrier",
        "Harmshield",
        "Quivering Veil of Xarn",
        "Harvest",
        "Boon of the Garou",
        "Theft of Thought",
    }
)

DA_SPELLS = frozenset({"Divine Aura", "Divine Barrier", "Harmshield", "Quivering Veil of Xarn"})

# All the charm spells (SpellHandlerService.AllCharmSpells).
ALL_CHARM_SPELLS = (
    "Dictate",
    "Charm",
    "Beguile",
    "Cajoling Whispers",
    "Allure",
    "Boltran`s Agacerie",
    "Befriend Animal",
    "Charm Animals",
    "Beguile Plants",
    "Beguile Animals",
    "Allure of the Wild",
    "Call of Karana",
    "Tunare's Request",
    "Dominate Undead",
    "Beguile Undead",
    "Cajole Undead",
    "Thrall of Bones",
    "Enslave Death",
)

# Self spells that never print a completion line (YouFinishCastingHandler).
_SELF_SPELLS_WITHOUT_COMPLETION_MESSAGE = frozenset(
    {
        "Harmshield",
        "Divine Aura",
        "Dictate",
        "Harmony",
        "Charm",
        "Beguile",
        "Cajoling Whispers",
        "Allure",
        "Boltran`s Agacerie",
    }
)

# Fixed duration overrides for disciplines (SpellHandlerService.Handle).
_DISCIPLINE_DURATION_OVERRIDES_S = {
    "Voiddance Discipline": 8,
    "Weapon Shield Discipline": 20,
    "Deftdance Discipline": 15,
    "Furious Discipline": 9,
    "Defensive Discipline": 180,
    "Evasive Discipline": 180,
    "Nimble Discipline": 12,
    "Puretone Discipline": 240,
}

# Discipline cooldown scaling: name -> (base seconds, min level, seconds range).
# Values transcribed literally from SpellHandlerService.Handle, including the
# Innerflame quirk (base 60min but a 30->26min scaling range).
_DISCIPLINE_COOLDOWNS = {
    "Evasive Discipline": (15 * 60.0, 52, (15 - 7) * 60.0),
    "Defensive Discipline": (15 * 60.0, 55, (15 - 10) * 60.0),
    "Precision Discipline": (30 * 60.0, 57, (30 - 27) * 60.0),
    "Stonestance Discipline": (12 * 60.0, 51, (12 - 4) * 60.0),
    "Voiddance Discipline": (60 * 60.0, 54, (60 - 54) * 60.0),
    "Innerflame Discipline": (60 * 60.0, 56, (30 - 26) * 60.0),
}

_CHARM_BREAK_LINE = "Your charm spell has worn off."


class SpellTimerHandler(BaseHandler):
    def __init__(
        self,
        bus: EventBus,
        player: ActivePlayer,
        spells: SpellBook,
        timers: TimersService,
        counter_lists: CounterLists | None = None,
    ) -> None:
        super().__init__(bus, player)
        self.spells = spells
        self.timers = timers
        self.counters = counter_lists or load_counter_lists()
        bus.subscribe(YouBeginCastingEvent, self._on_begin_casting)
        bus.subscribe(YouFinishCastingEvent, self._on_finish_casting)
        bus.subscribe(SpellCastOnYouEvent, self._on_cast_on_you)
        bus.subscribe(SpellCastOnOtherEvent, self._on_cast_on_other)
        bus.subscribe(SpellWornOffSelfEvent, self._on_worn_off_self)
        bus.subscribe(SpellWornOffOtherEvent, self._on_worn_off_other)
        bus.subscribe(YouForgetEvent, self._on_forget)
        bus.subscribe(YouHaveFinishedMemorizingEvent, self._on_memorized)
        bus.subscribe(ResistSpellEvent, self._on_resist)
        bus.subscribe(LineEvent, self._on_line)

    # -- casting state (YouBeginCastingHandler / YouFinishCastingHandler) -----

    def _on_begin_casting(self, event: YouBeginCastingEvent) -> None:
        self.spells.casting.begin(event.spell, event.timestamp)

    def _on_line(self, event: LineEvent) -> None:
        """Expire the casting state; also fire self-only spells that never
        print a completion message (charms, Harmshield, ...)."""
        casting = self.spells.casting
        if casting.spell is None or casting.started_at is None:
            return
        elapsed_ms = (event.timestamp - casting.started_at).total_seconds() * 1000.0
        if elapsed_ms > casting.spell.cast_time_ms + 1000:
            delta_offset_ms = int(casting.spell.cast_time_ms - elapsed_ms)
            if casting.spell.name in _SELF_SPELLS_WITHOUT_COMPLETION_MESSAGE:
                self.handle_spell(casting.spell, YOU_GROUP, delta_offset_ms, event.timestamp)
            casting.clear()

    def _on_finish_casting(self, event: YouFinishCastingEvent) -> None:
        self.handle_spell(event.spell, event.target_name, 0, event.timestamp)
        self.spells.casting.clear()

    def _on_cast_on_you(self, event: SpellCastOnYouEvent) -> None:
        self.handle_spell(event.spell, YOU_GROUP, 0, event.timestamp)

    def _on_cast_on_other(self, event: SpellCastOnOtherEvent) -> None:
        casting = self.spells.casting
        spell: Spell | None = None
        if casting.spell is not None and casting.started_at is not None:
            elapsed_ms = (event.timestamp - casting.started_at).total_seconds() * 1000.0
            if elapsed_ms >= casting.spell.cast_time_ms - 600 and any(
                s.name == casting.spell.name for s in event.spells
            ):
                spell = casting.spell
                casting.clear()
                target = event.target_name
                if spell.name.casefold() in ("theft of thought", "dictate"):
                    target = YOU_GROUP
                self.handle_spell(spell, target, 0, event.timestamp)
                return

        spell = match_closest_level_to_spell(
            list(event.spells), self.player.player_class, self.player.level
        )
        if spell is not None:
            self.handle_spell(spell, event.target_name, 0, event.timestamp)

    # -- removals ---------------------------------------------------------------

    def _on_worn_off_self(self, event: SpellWornOffSelfEvent) -> None:
        self.timers.try_remove_unambiguous_self(event.spell_names)

    def _on_worn_off_other(self, event: SpellWornOffOtherEvent) -> None:
        if self.counters.needs_count(event.spell_name):
            return
        self.timers.try_remove_unambiguous_other(event.spell_name)
        if event.line == _CHARM_BREAK_LINE:
            self.timers.try_remove_unambiguous_other(ALL_CHARM_SPELLS)
            self.timers.try_remove_unambiguous_self(ALL_CHARM_SPELLS)

    def _on_forget(self, event: YouForgetEvent) -> None:
        spell = self.spells.spell_by_name(event.spell_name)
        if spell is None:
            return
        if spell.recast_time_ms / 1000.0 < MINIMUM_RECAST_FOR_YOU_COOLDOWN_TIMER_S:
            return
        self.timers.try_remove_unambiguous_self([f"{event.spell_name} Cooldown"])

    def _on_memorized(self, event: YouHaveFinishedMemorizingEvent) -> None:
        if event.spell_name not in SPELLS_THAT_NEED_TIMERS:
            return
        spell = self.spells.spell_by_name(event.spell_name)
        if spell is None:
            return
        seconds = spell.recast_time_ms // 1000
        self.timers.add_timer(
            TimerRow(
                name=f"{event.spell_name} Cooldown",
                group=YOU_GROUP,
                updated_at=event.timestamp,
                ends_at=event.timestamp + timedelta(seconds=seconds),
                total_duration_s=float(seconds),
            )
        )

    def _on_resist(self, event: ResistSpellEvent) -> None:
        if event.spell.name not in self.counters.spells_that_need_counts:
            return
        # EQTool asks FightHistory for the current target here; without it we
        # bump the first matching counter and never create a new one.
        row = next(
            (
                r
                for r in self.timers.rows_of(CounterRow)
                if isinstance(r, CounterRow) and r.name == event.spell.name
            ),
            None,
        )
        if row is not None:
            row.count += 1
            row.updated_at = event.timestamp

    # -- row creation (SpellHandlerService.Handle) --------------------------------

    def handle_spell(
        self, spell: Spell, target_name: str, delay_offset_ms: int, timestamp: datetime
    ) -> None:
        group_name = target_name
        is_npc = self.spells.is_npc(group_name.strip())
        if is_npc:
            group_name = " " + group_name.strip()

        if any(spell.name.casefold() == s.casefold() for s in SPELLS_THAT_NEED_TIMERS):
            cooldown_s = int((spell.recast_time_ms + delay_offset_ms) / 1000.0)
            cooldown_group = YOU_GROUP if spell.name == "Theft of Thought" else target_name
            self.timers.add_spell(
                SpellRow(
                    name=f"{spell.name} Cooldown",
                    group=cooldown_group,
                    updated_at=timestamp,
                    is_target_player=not is_npc,
                    spell=spell,
                    ends_at=timestamp + timedelta(seconds=cooldown_s),
                    total_duration_s=float(cooldown_s),
                    is_cooldown=True,
                )
            )
            if any(spell.name.casefold() == s.casefold() for s in DA_SPELLS):
                self.bus.publish(TimerBarEvent(name="DA Timer", total_seconds=18))
        elif spell.name.endswith("Discipline"):
            cooldown_s = self._discipline_cooldown_seconds(spell, delay_offset_ms)
            self.timers.add_spell(
                SpellRow(
                    name=f"{spell.name} Cooldown",
                    group=target_name,
                    updated_at=timestamp,
                    is_target_player=True,
                    spell=spell,
                    ends_at=timestamp + timedelta(seconds=cooldown_s),
                    total_duration_s=float(cooldown_s),
                    is_cooldown=True,
                )
            )

        if self.counters.needs_count(spell.name):
            self.timers.add_counter(
                CounterRow(
                    name=spell.name,
                    group=group_name,
                    updated_at=timestamp,
                    is_target_player=not is_npc,
                )
            )
            return

        duration = timedelta(
            seconds=get_duration_seconds(spell, self.player.player_class, self.player.level)
        )
        override = _DISCIPLINE_DURATION_OVERRIDES_S.get(spell.name)
        if override is not None:
            duration = timedelta(seconds=override)
        duration += timedelta(milliseconds=delay_offset_ms)

        if "Discipline" in spell.name and target_name == YOU_GROUP:
            self.bus.publish(
                TimerBarEvent(name=spell.name, total_seconds=int(duration.total_seconds()))
            )
        if duration.total_seconds() <= 0:
            return

        if is_npc and spell.is_detrimental:
            # Extra tick so the row outlives the "spell has worn off" line.
            duration += timedelta(seconds=6)

        self.timers.add_spell(
            SpellRow(
                name=spell.name,
                group=group_name,
                updated_at=timestamp,
                is_target_player=not is_npc,
                spell=spell,
                ends_at=timestamp + duration,
                total_duration_s=duration.total_seconds(),
                detrimental=spell.is_detrimental,
            )
        )

    def _discipline_cooldown_seconds(self, spell: Spell, delay_offset_ms: int) -> int:
        base = int((spell.recast_time_ms + delay_offset_ms) / 1000.0)
        scaling = _DISCIPLINE_COOLDOWNS.get(spell.name)
        if scaling is None:
            return base
        base_seconds, min_level, seconds_range = scaling
        level = self.player.level if self.player.level is not None else 0
        per_level = seconds_range / (60 - min_level)
        return int(base_seconds - ((level - min_level) * per_level))
