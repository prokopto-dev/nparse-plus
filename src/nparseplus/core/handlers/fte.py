"""FTEHandler — first-to-engage announcements.

Port of EQTool's Services/Handlers/FTEHandler.cs: speaks and overlays
"<player> FTE <npc>", and starts the raid-rule timers for the mobs that
have them (the 97%/96% engage rules and Lodizal's 5-minute rule).

Divergences from the C#:
- The C# re-publishes the overlay with Reset=true after a 3s sleep on a
  worker thread; overlay reset scheduling is the UI layer's job here, so
  only the initial OverlayEvent is published.
- The 96% rule only applies on Green; without a server on the player it
  falls back to the 97% rule, like a C# player with no server set.

With the network layer available, the overlay waits for a PigParse
getbynames lookup and shows "<guild>" after the player's name when known —
same as the C# (which publishes exactly one overlay, decorated or plain).
"""

from __future__ import annotations

from datetime import timedelta

from nparseplus.core.bus import EventBus
from nparseplus.core.enums import Server
from nparseplus.core.events import FTEEvent, OverlayEvent
from nparseplus.core.handlers.base import BaseHandler
from nparseplus.core.handlers.spawn_timer import CUSTOM_TIMER_GROUP
from nparseplus.core.pigparse import PigParseApi, SubmitFn
from nparseplus.core.player import ActivePlayer
from nparseplus.core.timers import TimerRow, TimersService
from nparseplus.core.triggers.engine import Speaker

NINETY_SEVEN_PERCENT_MOBS = ("Zlandicar", "Dozekar the Cursed", "Lord Yelinak")
NINETY_SIX_PERCENT_MOBS = ("Dozekar the Cursed", "Lord Yelinak")

NINETY_SEVEN_RULE_SECONDS = 61
NINETY_SIX_RULE_SECONDS = 91
LODIZAL_RULE_SECONDS = 5 * 60


class FTEHandler(BaseHandler):
    def __init__(
        self,
        bus: EventBus,
        player: ActivePlayer,
        timers: TimersService,
        speaker: Speaker | None = None,
        api: PigParseApi | None = None,
        submit: SubmitFn | None = None,
    ) -> None:
        super().__init__(bus, player)
        self.timers = timers
        self.speaker = speaker
        self.api = api
        self.submit = submit
        bus.subscribe(FTEEvent, self._on_fte)

    def _on_fte(self, event: FTEEvent) -> None:
        if self.speaker is not None:
            self.speaker.speak(f"{event.fte_person} F T E {event.npc_name}")
        self._publish_overlay(event)

        if event.npc_name in NINETY_SEVEN_PERCENT_MOBS:
            rule, seconds = "--97% Rule--", NINETY_SEVEN_RULE_SECONDS
            if event.npc_name in NINETY_SIX_PERCENT_MOBS and self.player.server == Server.GREEN:
                rule, seconds = "--96% Rule--", NINETY_SIX_RULE_SECONDS
            self._add_timer(event, f"{rule} {event.npc_name}", seconds)
        if event.npc_name == "Lodizal":
            self._add_timer(event, f"--5 Minute Rule-- {event.npc_name}", LODIZAL_RULE_SECONDS)

    def _publish_overlay(self, event: FTEEvent) -> None:
        plain = f"{event.fte_person} FTE {event.npc_name}"
        api, submit, server = self.api, self.submit, self.player.server
        if api is None or submit is None or server is None:
            self.bus.publish(OverlayEvent(text=plain, foreground="Yellow"))
            return
        fte_person, npc_name = event.fte_person, event.npc_name

        def fetch() -> str:
            found = api.players_by_names([fte_person], int(server))
            if found:
                record = found[0]
                # C# formats a null guild as empty: "Name <> FTE ...".
                return f"{record.name} <{record.guild_name or ''}> FTE {npc_name}"
            return plain

        submit(fetch, lambda text: self.bus.publish(OverlayEvent(text=text, foreground="Yellow")))

    def _add_timer(self, event: FTEEvent, name: str, seconds: int) -> None:
        self.timers.add_timer(
            TimerRow(
                name=name,
                group=CUSTOM_TIMER_GROUP,
                updated_at=event.timestamp,
                ends_at=event.timestamp + timedelta(seconds=seconds),
                total_duration_s=float(seconds),
            )
        )
