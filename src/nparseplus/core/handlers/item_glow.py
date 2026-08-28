"""Clicky items that cast a spell under a different name.

Port of EQTool's Services/Handlers/YourItemBeginsToGlowHandler.cs.

Some items cast a spell whose log message is identical to the spell a player
can memorize, but whose DURATION is the item's, not the spell's. A Pegasus
Feather Cloak prints "Your feet leave the ground." exactly like a cast
Levitate and lasts two minutes rather than ten, and no later line
distinguishes them — so the only signal is the click itself, one line
earlier: "Your Pegasus Feather Cloak begins to glow."

The handler answers that line by priming the casting state with the item's
spell, so ``YouFinishCastingParser`` matches the landing message against a
spell already known rather than guessing from the shared cast-message table.
That is the same route ``YouBeginCastingParser`` uses for a memorized cast,
which is why this needs no new machinery: it is one more way for the casting
state to be set.

``ITEM_SPELLS`` is the C#'s single ``if`` generalized to a table, because a
name-per-``if`` is how a list like this stops getting added to. The spell
names are looked up in the database rather than constructed, so an entry
naming a spell the loaded spells_us.txt does not have is skipped rather than
raising.
"""

from __future__ import annotations

from nparseplus.core.bus import EventBus
from nparseplus.core.events import YourItemBeginsToGlowEvent
from nparseplus.core.handlers.base import BaseHandler
from nparseplus.core.player import ActivePlayer
from nparseplus.core.spells.spells_us import SpellBook

#: Item name (as it appears in the glow line) -> the spell it casts.
#: ``Peggy Levitate`` is synthesized by ``load_spell_book``; it exists for
#: exactly this lookup and is reachable by no other route (#177).
ITEM_SPELLS: dict[str, str] = {
    "Pegasus Feather Cloak": "Peggy Levitate",
}


class ItemGlowHandler(BaseHandler):
    def __init__(self, bus: EventBus, player: ActivePlayer, spells: SpellBook) -> None:
        super().__init__(bus, player)
        self.spells = spells
        bus.subscribe(YourItemBeginsToGlowEvent, self._on_glow)

    def _on_glow(self, event: YourItemBeginsToGlowEvent) -> None:
        spell_name = ITEM_SPELLS.get(event.item_name)
        if spell_name is None:
            return
        spell = self.spells.spell_by_name(spell_name)
        if spell is None:
            return
        # Overwrites whatever the player had begun casting, and re-stamps the
        # clock from the click: the C# does both, and it is what makes the
        # click win over a Levitate the player started casting first.
        self.spells.casting.begin(spell, event.timestamp)
