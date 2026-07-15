"""FTEHandler — first-to-engage announcements.

Port of EQTool's Services/Handlers/FTEHandler.cs: speaks and overlays
"<player> FTE <npc>", and starts the raid-rule timers for the mobs that
have them (the 97%/96% engage rules and Lodizal's 5-minute rule).

Divergences from the C#:
- The PigParse lookup that decorates the overlay with the FTE player's guild
  is network-only. TODO(M3): restore ``<guild>`` decoration via PigParseApi.
- The C# re-publishes the overlay with Reset=true after a 3s sleep on a
  worker thread; overlay reset scheduling is the UI layer's job here, so
  only the initial OverlayEvent is published.
- The 96% rule only applies on Green; without a server on the player it
  falls back to the 97% rule, like a C# player with no server set.
"""

from __future__ import annotations

from datetime import timedelta

from nparseplus.core.bus import EventBus
from nparseplus.core.enums import Server
from nparseplus.core.events import FTEEvent, OverlayEvent
from nparseplus.core.handlers.base import BaseHandler
from nparseplus.core.handlers.spawn_timer import CUSTOM_TIMER_GROUP
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
    ) -> None:
        super().__init__(bus, player)
        self.timers = timers
        self.speaker = speaker
        bus.subscribe(FTEEvent, self._on_fte)

    def _on_fte(self, event: FTEEvent) -> None:
        if self.speaker is not None:
            self.speaker.speak(f"{event.fte_person} F T E {event.npc_name}")
        # TODO(M3): decorate with the FTE player's guild via PigParseApi.
        self.bus.publish(
            OverlayEvent(text=f"{event.fte_person} FTE {event.npc_name}", foreground="Yellow")
        )

        if event.npc_name in NINETY_SEVEN_PERCENT_MOBS:
            rule, seconds = "--97% Rule--", NINETY_SEVEN_RULE_SECONDS
            if event.npc_name in NINETY_SIX_PERCENT_MOBS and self.player.server == Server.GREEN:
                rule, seconds = "--96% Rule--", NINETY_SIX_RULE_SECONDS
            self._add_timer(event, f"{rule} {event.npc_name}", seconds)
        if event.npc_name == "Lodizal":
            self._add_timer(event, f"--5 Minute Rule-- {event.npc_name}", LODIZAL_RULE_SECONDS)

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
