"""Complete Heal chain handlers.

- ``CompleteHealCommsHandler`` (port of Services/Parsing/
  CompleteHealCommsHandler.cs): scans non-tell/say CommsEvents for CH chain
  calls and publishes CompleteHealEvents.
- ``CompleteHealHandler`` (port of Services/Handlers/CompleteHealHandler.cs
  over Services/CHService.cs): keeps per-target chain state and warns —
  TTS plus an OverlayEvent — when the position right before yours calls CH,
  i.e. you are next.

The comms parser has already normalized the player's own messages to sender
"You", which is what the chain logic keys your position on.
"""

from __future__ import annotations

from nparseplus.core.bus import EventBus
from nparseplus.core.ch_chain import CHChainService, parse_ch_message
from nparseplus.core.enums import CommsChannel
from nparseplus.core.events import CommsEvent, CompleteHealEvent, OverlayEvent
from nparseplus.core.handlers.base import BaseHandler
from nparseplus.core.player import ActivePlayer
from nparseplus.core.triggers.engine import Speaker

CH_WARNING_TEXT = "CH Warning"


class CompleteHealCommsHandler(BaseHandler):
    """CommsEvent -> CompleteHealEvent (the 'TAG POSITION CH TARGET' scan)."""

    def __init__(
        self,
        bus: EventBus,
        player: ActivePlayer,
        npcs: frozenset[str] = frozenset(),
        ch_chain_tag: str = "",
    ) -> None:
        super().__init__(bus, player)
        self.npcs = npcs
        # Mirrors the ChChainTagOverlay setting: when set, only calls
        # prefixed with this raid tag are accepted.
        self.ch_chain_tag = ch_chain_tag
        bus.subscribe(CommsEvent, self._on_comms)

    def _on_comms(self, event: CommsEvent) -> None:
        if event.channel in (CommsChannel.TELL, CommsChannel.SAY):
            return
        parsed = parse_ch_message(
            event.sender,
            event.content,
            event.timestamp,
            configured_tag=self.ch_chain_tag,
            npcs=self.npcs,
            line=event.line,
            line_number=event.line_number,
        )
        if parsed is not None:
            self.bus.publish(parsed)


class CompleteHealHandler(BaseHandler):
    """CompleteHealEvent -> 'CH Warning' when your chain slot is next."""

    def __init__(
        self,
        bus: EventBus,
        player: ActivePlayer,
        speaker: Speaker | None = None,
        chains: CHChainService | None = None,
    ) -> None:
        super().__init__(bus, player)
        self.speaker = speaker
        self.chains = chains if chains is not None else CHChainService()
        bus.subscribe(CompleteHealEvent, self._on_complete_heal)

    def _on_complete_heal(self, event: CompleteHealEvent) -> None:
        if self.chains.observe(event):
            if self.speaker is not None:
                self.speaker.speak(CH_WARNING_TEXT)
            self.bus.publish(OverlayEvent(text=CH_WARNING_TEXT, foreground="Red"))
