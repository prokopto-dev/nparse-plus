"""RootBreakHandler — warn when one of your roots breaks (#79).

1:1 port of EQTool's Services/Handlers/RootHasWornOffHandler.cs: a
``SpellWornOffOtherEvent`` whose spell name case-insensitively equals one of
eleven root spells speaks and overlays ``"<Spell> has worn off!"`` in red.
The input side already existed — ``core/parsers/spell_worn_off.py`` publishes
the event from ``^Your (?P<spell_name>[\\w ]+) spell has worn off\\.``, which
is your own root breaking on a mob: exactly the signal a CC'd add or a
root-parked raid mob wants.

Divergences from the C#:

- EQTool stores ``RootWarningAudio``/``RootWarningOverlay`` per character on
  ``PlayerInfo`` (both defaulting off). nParse+ keeps the comparable alert
  toggles global (bard count, buff fade, respawn expiry), so these follow the
  local convention and live on ``GeneralSettings``. They default **on**: a
  root break is a drop-everything moment and this is a parity gap, so the
  useful default is the one that alerts.
- The C# re-publishes the overlay with ``Reset=true`` after sleeping 3s on a
  worker thread. Overlay hold and reset are the UI layer's job here — see
  ``FTEHandler`` for the same divergence — so only the initial
  ``OverlayEvent`` is published and ``general.overlay_text_seconds`` owns how
  long it stays up.
"""

from __future__ import annotations

from collections.abc import Callable

from nparseplus.core.bus import EventBus
from nparseplus.core.events import OverlayEvent, SpellWornOffOtherEvent
from nparseplus.core.handlers.base import BaseHandler
from nparseplus.core.player import ActivePlayer
from nparseplus.core.triggers.engine import Speaker

#: RootHasWornOffHandler.RootSpells, in source order.
ROOT_SPELLS = (
    "Root",
    "Fetter",
    "Enstill",
    "Immobilize",
    "Paralyzing Earth",
    "Grasping Roots",
    "Ensnaring Roots",
    "Enveloping Roots",
    "Engulfing Roots",
    "Engorging Roots",
    "Entrapping Roots",
)

_ROOT_SPELLS_LOWER = frozenset(name.lower() for name in ROOT_SPELLS)


def is_root_spell(spell_name: str) -> bool:
    """Whether ``spell_name`` is one of the eleven roots (case-insensitive)."""
    return spell_name.lower() in _ROOT_SPELLS_LOWER


class RootBreakHandler(BaseHandler):
    def __init__(
        self,
        bus: EventBus,
        player: ActivePlayer,
        speaker: Speaker | None = None,
        overlay_enabled: Callable[[], bool] | None = None,
        audio_enabled: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__(bus, player)
        self.speaker = speaker
        # Read live so the settings window's Apply takes effect without a
        # restart, like the other alert toggles.
        self._overlay_enabled = overlay_enabled or (lambda: True)
        self._audio_enabled = audio_enabled or (lambda: True)
        bus.subscribe(SpellWornOffOtherEvent, self._on_spell_worn_off_other)

    def _on_spell_worn_off_other(self, event: SpellWornOffOtherEvent) -> None:
        if not is_root_spell(event.spell_name):
            return
        # The C# formats the name as the log wrote it, not the list entry.
        text = f"{event.spell_name} has worn off!"
        if self.speaker is not None and self._audio_enabled():
            self.speaker.speak(text)
        if self._overlay_enabled():
            self.bus.publish(OverlayEvent(text=text, foreground="Red"))
