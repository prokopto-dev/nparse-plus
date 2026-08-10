"""AbilityCooldownHandler — the 72-minute knight ability reuse timers.

An nParse+ addition: EQTool has no Lay on Hands / Harm Touch timer, and these
are the longest reuse timers a P99 character owns, so forgetting one is
expensive. Both are real rows in spells_us.txt, so the cast messages and the
reuse length come from the spell data rather than from literals here (the
file's 30 s recast is corrected for both in ``spells_us._apply_fixups``).

The hard part is attribution, and it bounds what this handler may claim. The
log names the TARGET of these abilities, never the user:

    You feel a healing touch.            <- someone laid hands on you
    Bob feels a healing touch.           <- someone laid hands on Bob
    a froglok knight writhes ...agony.   <- something harm-touched that mob

So "did *I* do that?" is a guess, and a wrong guess strands a 72-minute row.
Two gates keep the guess honest, both chosen to under-detect rather than
over-detect:

* **Class.** Only a paladin gets a Lay on Hands row, only a shadow knight a
  Harm Touch row. Everyone else sees these lines constantly and never has the
  ability.
* **Direction.** You harm-touch mobs and lay hands on people, so a Harm Touch
  row needs an NPC-looking target and a Lay on Hands row needs a non-NPC one.
  That is what discards the two common impostors: NPC shadow knights harm-touch
  the tank on aggro, and NPC paladins lay hands on themselves at low health.
  The self-target form of Harm Touch ("You writhe in the grip of agony.") is
  never yours for the same reason — it means something harm-touched *you*.

What survives: another paladin or shadow knight in view using theirs on the
same kind of target. Nothing in the line distinguishes that case, so the row
can be dismissed like any other. A later "You can use the ability ... again in"
line corrects the countdown through DisciplineCooldownHandler.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from nparseplus.core.bus import EventBus
from nparseplus.core.enums import PlayerClass
from nparseplus.core.events import SpellCastOnOtherEvent, SpellCastOnYouEvent
from nparseplus.core.handlers.base import BaseHandler
from nparseplus.core.player import ActivePlayer
from nparseplus.core.spells.models import Spell
from nparseplus.core.spells.spells_us import SpellBook
from nparseplus.core.timers import YOU_GROUP, TimerRow, TimersService

LAY_ON_HANDS = "Lay on Hands"
HARM_TOUCH = "Harm Touch"

# The class that can actually use each ability.
_ABILITY_CLASS = {
    LAY_ON_HANDS: PlayerClass.PALADIN,
    HARM_TOUCH: PlayerClass.SHADOW_KNIGHT,
}

# EQ's own naming convention: mobs are "a rat" / "an orc pawn" / "the Ghoul
# Lord", players never are. Cheap, and it catches the unnamed mobs the master
# NPC list misses ("a froglok knight" is not in it).
_NPC_ARTICLES = ("a ", "an ", "the ")


def looks_like_npc(name: str, book: SpellBook) -> bool:
    """Best-effort NPC test for a cast-message target name.

    Deliberately biased towards "yes": a false NPC only costs a missed timer,
    while a false player invents one. ``is_npc`` alone is not enough in either
    direction — it misses unnamed mobs and it matches plenty of ordinary player
    names, since the master list is every NPC name in the game.
    """
    stripped = name.strip()
    if not stripped:
        return False
    return stripped.casefold().startswith(_NPC_ARTICLES) or book.is_npc(stripped)


class AbilityCooldownHandler(BaseHandler):
    def __init__(
        self, bus: EventBus, player: ActivePlayer, spells: SpellBook, timers: TimersService
    ) -> None:
        super().__init__(bus, player)
        self.spells = spells
        self.timers = timers
        bus.subscribe(SpellCastOnYouEvent, self._on_cast_on_you)
        bus.subscribe(SpellCastOnOtherEvent, self._on_cast_on_other)

    def _on_cast_on_you(self, event: SpellCastOnYouEvent) -> None:
        # Only Lay on Hands: you can be the target of your own heal, but being
        # the target of a Harm Touch means someone used one *on* you.
        if event.spell.name == LAY_ON_HANDS:
            self._start(event.spell, event.timestamp)

    def _on_cast_on_other(self, event: SpellCastOnOtherEvent) -> None:
        target_is_npc = looks_like_npc(event.target_name, self.spells)
        for spell in event.spells:
            if spell.name == LAY_ON_HANDS and not target_is_npc:
                self._start(spell, event.timestamp)
                return
            if spell.name == HARM_TOUCH and target_is_npc:
                self._start(spell, event.timestamp)
                return

    def _start(self, spell: Spell, timestamp: datetime) -> None:
        if self.player.player_class is not _ABILITY_CLASS[spell.name]:
            return
        seconds = spell.recast_time_ms / 1000.0
        if seconds <= 0:
            return
        self.timers.add_timer(
            TimerRow(
                name=spell.name,
                group=YOU_GROUP,
                updated_at=timestamp,
                ends_at=timestamp + timedelta(seconds=seconds),
                total_duration_s=seconds,
            )
        )
