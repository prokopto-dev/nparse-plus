"""RespawnExpiryNotifier — speak when a respawn timer runs out (eqtool #239).

nparseplus addition: the request is an open EQTool issue, nothing to port.
Watches TimersService.on_expired for "--Dead-- <victim>" rows in the shared
custom-timer group and announces the pop via the injected Speaker (opt-in,
``spellwindow.respawn_expiry_audio``). Runs on the driver thread — the
Speaker protocol implementations queue onto their own worker.
"""

from __future__ import annotations

import re

from nparseplus.config.settings import SpellWindowSettings
from nparseplus.core.handlers.spawn_timer import CUSTOM_TIMER_GROUP
from nparseplus.core.timers import Row, TimerRow, TimersService
from nparseplus.core.triggers.engine import Speaker

_DEAD_PREFIX = "--Dead-- "
# "--Dead-- a frost giant scout_3" -> duplicate-death suffix.
_DUP_SUFFIX = re.compile(r"_\d+$")


class RespawnExpiryNotifier:
    def __init__(
        self, timers: TimersService, speaker: Speaker | None, settings: SpellWindowSettings
    ) -> None:
        self.speaker = speaker
        self.settings = settings
        timers.on_expired.append(self._on_expired)

    def _on_expired(self, rows: list[Row]) -> None:
        if self.speaker is None or not self.settings.respawn_expiry_audio:
            return
        for row in rows:
            if (
                isinstance(row, TimerRow)
                and row.group == CUSTOM_TIMER_GROUP
                and row.name.startswith(_DEAD_PREFIX)
            ):
                victim = _DUP_SUFFIX.sub("", row.name[len(_DEAD_PREFIX) :])
                self.speaker.speak(f"{victim} spawn timer expired")
