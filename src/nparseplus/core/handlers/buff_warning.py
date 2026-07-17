"""BuffFadeWarner — pre-warn N seconds before a self-buff fades.

nparseplus addition (GINA-parity ask from the P99 EQTool thread; EQTool has
no native equivalent — its TriggerTimerEnding covers trigger timers only,
see core/triggers/engine.py). Runs on the driver tick: scans YOU_GROUP
beneficial SpellRows and, once per row instance, speaks "<buff> is fading"
and publishes an OverlayEvent. A recast replaces the row (new ends_at), so
the (name, ends_at) key re-arms automatically.
"""

from __future__ import annotations

from datetime import datetime

from nparseplus.config.settings import SpellWindowSettings
from nparseplus.core.bus import EventBus
from nparseplus.core.events import OverlayEvent
from nparseplus.core.timers import YOU_GROUP, SpellRow, TimersService
from nparseplus.core.triggers.engine import Speaker


class BuffFadeWarner:
    def __init__(
        self,
        bus: EventBus,
        timers: TimersService,
        speaker: Speaker | None,
        settings: SpellWindowSettings,
    ) -> None:
        self.bus = bus
        self.timers = timers
        self.speaker = speaker
        self.settings = settings
        self._fired: set[tuple[str, datetime]] = set()

    def tick(self, now: datetime) -> None:
        threshold = self.settings.buff_fade_warning_seconds
        if threshold <= 0:
            if self._fired:
                self._fired.clear()
            return
        active: set[tuple[str, datetime]] = set()
        for row in self.timers.rows_of(SpellRow):
            assert isinstance(row, SpellRow)
            if row.group != YOU_GROUP or row.is_cooldown or row.detrimental:
                continue
            key = (row.name, row.ends_at)
            active.add(key)
            remaining = (row.ends_at - now).total_seconds()
            if 0 < remaining <= threshold and key not in self._fired:
                self._fired.add(key)
                text = f"{row.name} is fading"
                if self.speaker is not None and self.settings.buff_fade_warning_audio:
                    self.speaker.speak(text)
                self.bus.publish(OverlayEvent(text=text, foreground="Yellow"))
        self._fired &= active
