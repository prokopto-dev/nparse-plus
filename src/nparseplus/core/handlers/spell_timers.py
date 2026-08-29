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
- Root-type spells always refresh the running timer even under
  TimerRecast=StartNewTimer (eqtool #213; the C# stacks roots like any other
  detrimental, which just orphans the previous root's timer).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from nparseplus.config.settings import SpellWindowSettings
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
from nparseplus.core.spells.durations import (
    base_timer_duration_seconds,
    npc_grace_seconds,
)
from nparseplus.core.spells.matching import (
    log_candidates,
    match_closest_level_to_spell,
    other_matches,
)
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

# Discipline cooldown scaling: name -> (base seconds, min level, seconds range).
# These six are the whole of SpellHandlerService.Handle's scaled set, and the
# rest of the ~50 disciplines in spells_us.txt deliberately are NOT here: their
# reuse is flat, so recast_time_ms is the game's own figure and a table row
# would only be a place for it to rot. A discipline whose reuse we get wrong is
# corrected live anyway by DisciplineCooldownParser, which reads the client's
# authoritative "You can use the ability X again in N minute(s) M seconds."
#
# Five of the six carry a base equal to their own recast_time_ms, which is what
# makes the sixth a transcription bug rather than a quirk of the game.
# DELIBERATE DIVERGENCE: EQTool gives Innerflame `baseseconds = 60 * 60` while
# naming 30 in its own `secondsrange = (30 - 26) * 60`, and the block directly
# above it (Voiddance) is `60 * 60` with `(60 - 54) * 60` — i.e. the 60 is
# copied down from Voiddance. spells_us.txt settles it: Innerflame's recast is
# 1800000 ms = 30 min, not 60. Ported literally it showed a monk double the
# real cooldown, so the base is corrected to 30 min here (#187).
_DISCIPLINE_COOLDOWNS = {
    "Evasive Discipline": (15 * 60.0, 52, (15 - 7) * 60.0),
    "Defensive Discipline": (15 * 60.0, 55, (15 - 10) * 60.0),
    "Precision Discipline": (30 * 60.0, 57, (30 - 27) * 60.0),
    "Stonestance Discipline": (12 * 60.0, 51, (12 - 4) * 60.0),
    "Voiddance Discipline": (60 * 60.0, 54, (60 - 54) * 60.0),
    "Innerflame Discipline": (30 * 60.0, 56, (30 - 26) * 60.0),
}

_CHARM_BREAK_LINE = "Your charm spell has worn off."

# Deliberate divergence from EQTool: root spells always restart the current
# timer, even with TimerRecast=StartNewTimer (eqtool #213). Only one root
# sticks per mob, so a stacked row would just be a dead leftover. Name-based
# because the spell model carries no SPA effect data.
ROOT_SPELLS = frozenset(
    {
        "Root",
        "Grasping Roots",
        "Ensnaring Roots",
        "Engulfing Roots",
        "Enveloping Roots",
        "Enstill",
        "Immobilize",
        "Paralyzing Earth",
        "Fetter",
    }
)


class SpellTimerHandler(BaseHandler):
    def __init__(
        self,
        bus: EventBus,
        player: ActivePlayer,
        spells: SpellBook,
        timers: TimersService,
        counter_lists: CounterLists | None = None,
        spell_settings: SpellWindowSettings | None = None,
        timer_recast: Callable[[], str] | None = None,
    ) -> None:
        super().__init__(bus, player)
        self.spells = spells
        self.timers = timers
        self.counters = counter_lists or load_counter_lists()
        # Live shared object (composition passes settings.spellwindow).
        self.spell_settings = (
            spell_settings if spell_settings is not None else SpellWindowSettings()
        )
        # Per-character PlayerInfo.TimerRecastSetting (composition resolves the
        # active profile; C# reads activePlayer?.Player?.TimerRecastSetting).
        self.timer_recast = timer_recast or (lambda: "RestartCurrentTimer")
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
                self.handle_spell(
                    casting.spell, YOU_GROUP, delta_offset_ms, event.timestamp, own_cast=True
                )
            casting.clear()

    def _on_finish_casting(self, event: YouFinishCastingEvent) -> None:
        self.handle_spell(event.spell, event.target_name, 0, event.timestamp, own_cast=True)
        self.spells.casting.clear()

    def _on_cast_on_you(self, event: SpellCastOnYouEvent) -> None:
        self.handle_spell(event.spell, YOU_GROUP, 0, event.timestamp, event.alternatives)

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
                self.handle_spell(spell, target, 0, event.timestamp, own_cast=True)
                return

        # Guess Spells off: an ambiguous line (several candidates) creates no
        # timer — only exact single-candidate matches. (nparseplus option;
        # EQTool's best-guess is always on.)
        if not self.spell_settings.best_guess_spells and len(event.spells) > 1:
            return
        # A third party's cast on a third party: the player is only watching,
        # so their class says nothing about which candidate this was and the
        # matcher stays in bystander mode (#177).
        candidates = list(event.spells)
        spell = match_closest_level_to_spell(
            candidates, self.player.player_class, self.player.level
        )
        log_candidates("cast on other", event.line, candidates, spell)
        if spell is not None:
            self.handle_spell(
                spell, event.target_name, 0, event.timestamp, other_matches(candidates, spell)
            )

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
        self,
        spell: Spell,
        target_name: str,
        delay_offset_ms: int,
        timestamp: datetime,
        alternatives: Sequence[Spell] = (),
        *,
        own_cast: bool = False,
    ) -> None:
        """``alternatives`` are the same-message spells the matcher passed over
        (#177) — carried onto the row so the Timers window can offer them as a
        correction. Empty for every unambiguous cast, and deliberately not put
        on the cooldown rows below: a cooldown is keyed to the recast the
        player actually started, and relabelling one would misreport the gem."""
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

        # Shared with TimersService.respell_row so a corrected guess counts down
        # exactly as it would have had the matcher named this spell (#177).
        duration = timedelta(
            seconds=base_timer_duration_seconds(
                spell,
                self.player.player_class,
                self.player.level,
                delay_offset_ms,
                own_cast=own_cast,
            )
        )

        if "Discipline" in spell.name and target_name == YOU_GROUP:
            self.bus.publish(
                TimerBarEvent(name=spell.name, total_seconds=int(duration.total_seconds()))
            )
        if duration.total_seconds() <= 0:
            return

        # SpellHandlerService: skip the overwrite only for a detrimental spell
        # on an NPC under TimerRecast=StartNewTimer (stacked DoTs on several
        # same-named mobs each keep their own row).
        overwrite = True
        duration += timedelta(seconds=npc_grace_seconds(spell, on_npc=is_npc))
        if (
            is_npc
            and spell.is_detrimental
            and self.timer_recast() == "StartNewTimer"
            and spell.name not in ROOT_SPELLS
        ):
            overwrite = False

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
                post_expiry_persist_s=self._post_expiry_persist_s(spell),
                alternatives=[s for s in alternatives if self._is_correctable(s)],
            ),
            overwrite=overwrite,
        )

    def _is_correctable(self, spell: Spell) -> bool:
        """Whether a countdown row may be relabelled as ``spell`` (#177).

        A correction rebuilds one ``SpellRow`` in place, so it can only offer
        candidates this method would ALSO have answered with a plain countdown.
        A counter spell returns above as a ``CounterRow`` (a tally, no
        countdown at all), and a reuse-timer spell or a discipline puts a
        second cooldown row beside the buff — neither of which a relabel
        creates. Offering them would hand the user a row of the wrong kind
        with no tally and no cooldown, which is worse than the mis-guess.

        The duration gate is the same one below, on the same
        ``base_timer_duration_seconds`` and deliberately BEFORE the NPC grace
        tick: a spell whose duration works out to zero creates no row at all
        here, so a correction onto one would invent a countdown the cast never
        produced — a 0-second row on a player, or a phantom 6-second one on an
        NPC where the grace tick is all that is left. 1221 such candidates sit
        inside 309 ambiguous lists in the bundled data ("has been mesmerized."
        carries four), so this is the common case, not a corner.

        Only the menu is narrowed; the matcher's own guess is untouched, and
        such a spell still gets its proper handling when it is the guess.
        """
        if self.counters.needs_count(spell.name):
            return False
        if spell.name.endswith("Discipline"):
            return False
        if any(spell.name.casefold() == n.casefold() for n in SPELLS_THAT_NEED_TIMERS):
            return False
        base_s = base_timer_duration_seconds(spell, self.player.player_class, self.player.level)
        return base_s > 0

    def _post_expiry_persist_s(self, spell: Spell) -> float:
        """Seconds a just-expired row lingers as a rebuff prompt (#16). 0 unless
        the opt-in is on and this spell is in the per-spell allowlist."""
        sw = self.spell_settings
        if not sw.post_expiry_flash_enabled:
            return 0.0
        if spell.name.casefold() in {n.casefold() for n in sw.post_expiry_flash_spells}:
            return float(sw.post_expiry_flash_seconds)
        return 0.0

    def _discipline_cooldown_seconds(self, spell: Spell, delay_offset_ms: int) -> int:
        base = int((spell.recast_time_ms + delay_offset_ms) / 1000.0)
        scaling = _DISCIPLINE_COOLDOWNS.get(spell.name)
        if scaling is None:
            return base
        base_seconds, min_level, seconds_range = scaling
        # DELIBERATE DIVERGENCE from EQTool (SpellHandlerService.Handle runs this
        # arithmetic on a raw activePlayer.Player.Level with no bound). The table
        # has exactly two anchors — the cooldown at min_level and the cooldown at
        # 60 — so the formula only interpolates inside that range. Outside it the
        # (level - min_level) term keeps going: below min_level it is negative and
        # the cooldown GROWS (an unset level of 0 turned Stonestance's 12 min into
        # 57 min, and level 25 into the ~35 min of the #187 report), above 60 it
        # runs past the endpoint into negative seconds. Clamp to the anchors.
        #
        # An unknown level therefore reads as min_level, which yields base_seconds
        # — the same figure the unscaled path takes straight from recast_time_ms,
        # and the conservative one: you cannot own the discipline below min_level.
        level = self.player.level if self.player.level is not None else min_level
        level = max(min_level, min(level, 60))
        per_level = seconds_range / (60 - min_level)
        return int(base_seconds - ((level - min_level) * per_level))
