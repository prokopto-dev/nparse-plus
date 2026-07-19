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

from collections.abc import Callable

from nparseplus.core.bus import EventBus
from nparseplus.core.ch_chain import CHChainService, parse_ch_cadence, parse_ch_message
from nparseplus.core.enums import CommsChannel
from nparseplus.core.events import (
    CommsEvent,
    CompleteHealCadenceEvent,
    CompleteHealEvent,
    OverlayEvent,
)
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
        ch_chain_tag: Callable[[], str] = lambda: "",
        cadence_enabled: Callable[[], bool] = lambda: False,
    ) -> None:
        super().__init__(bus, player)
        self.npcs = npcs
        # Mirrors the ChChainTagOverlay setting: when the provider returns a
        # non-empty tag, only calls prefixed with that raid tag are accepted.
        # A live provider (rather than a stored string) lets settings saves
        # take effect without a restart.
        self.ch_chain_tag = ch_chain_tag
        # Live provider for the opt-in ch_cadence_indicator setting (#15).
        self.cadence_enabled = cadence_enabled
        bus.subscribe(CommsEvent, self._on_comms)

    def _on_comms(self, event: CommsEvent) -> None:
        if event.channel in (CommsChannel.TELL, CommsChannel.SAY):
            return
        parsed = parse_ch_message(
            event.sender,
            event.content,
            event.timestamp,
            configured_tag=self.ch_chain_tag(),
            npcs=self.npcs,
            line=event.line,
            line_number=event.line_number,
        )
        if parsed is not None:
            self.bus.publish(parsed)
            return
        # A cadence callout ("healers to 4") is not a chain call, so it only
        # reaches here. Off by default; opt-in via ch_cadence_indicator (#15).
        if self.cadence_enabled():
            seconds = parse_ch_cadence(event.content)
            if seconds is not None:
                self.bus.publish(
                    CompleteHealCadenceEvent(
                        timestamp=event.timestamp,
                        line=event.line,
                        line_number=event.line_number,
                        seconds=seconds,
                    )
                )


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
