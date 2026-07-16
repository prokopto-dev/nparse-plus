"""QuakeHandler — earthquake (server-wide event) announcements.

Port of EQTool's Services/Handlers/QuakeHandler.cs. The C# handler's only
action is ``pigParseApi.SendQuake(server)`` (the server dedupes to one
quake per 2h) — the quake/ring-roll timers are rebuilt from the shared
roll-timer feed on the 5-minute API refresh (``handlers.api_timers``).
Locally we announce the quake via TTS/overlay so a solo install still gets
the warning.
"""

from __future__ import annotations

from nparseplus.core.bus import EventBus
from nparseplus.core.events import OverlayEvent, QuakeEvent
from nparseplus.core.handlers.base import BaseHandler
from nparseplus.core.pigparse import PigParseApi, SubmitFn
from nparseplus.core.player import ActivePlayer
from nparseplus.core.triggers.engine import Speaker

QUAKE_TEXT = "EARTHQUAKE"


class QuakeHandler(BaseHandler):
    def __init__(
        self,
        bus: EventBus,
        player: ActivePlayer,
        speaker: Speaker | None = None,
        api: PigParseApi | None = None,
        submit: SubmitFn | None = None,
    ) -> None:
        super().__init__(bus, player)
        self.speaker = speaker
        self.api = api
        self.submit = submit
        bus.subscribe(QuakeEvent, self._on_quake)

    def _on_quake(self, event: QuakeEvent) -> None:
        api, submit, server = self.api, self.submit, self.player.server
        if api is not None and submit is not None and server is not None:
            submit(lambda: api.send_quake(int(server)))
        if self.speaker is not None:
            self.speaker.speak("Earthquake")
        self.bus.publish(OverlayEvent(text=QUAKE_TEXT, foreground="Red"))
