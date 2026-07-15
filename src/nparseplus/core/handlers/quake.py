"""QuakeHandler — earthquake (server-wide event) announcements.

Port of EQTool's Services/Handlers/QuakeHandler.cs. The C# handler's only
action is ``pigParseApi.SendQuake(server)`` — the quake/ring-roll timers are
built from the server's shared roll-timer feed, not locally.

TODO(M3): send the quake to PigParse and rebuild the shared "Ring 8 Roll
Timer" rows from the server response (SpellWindowViewModel's RollTimerType
handling). Locally we announce the quake via TTS/overlay so a solo install
still gets the warning.
"""

from __future__ import annotations

from nparseplus.core.bus import EventBus
from nparseplus.core.events import OverlayEvent, QuakeEvent
from nparseplus.core.handlers.base import BaseHandler
from nparseplus.core.player import ActivePlayer
from nparseplus.core.triggers.engine import Speaker

QUAKE_TEXT = "EARTHQUAKE"


class QuakeHandler(BaseHandler):
    def __init__(self, bus: EventBus, player: ActivePlayer, speaker: Speaker | None = None) -> None:
        super().__init__(bus, player)
        self.speaker = speaker
        bus.subscribe(QuakeEvent, self._on_quake)

    def _on_quake(self, event: QuakeEvent) -> None:
        # TODO(M3): pigParseApi.SendQuake(player.server) equivalent.
        if self.speaker is not None:
            self.speaker.speak("Earthquake")
        self.bus.publish(OverlayEvent(text=QUAKE_TEXT, foreground="Red"))
