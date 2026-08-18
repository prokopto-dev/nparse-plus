"""RespawnExpiryNotifier — speak when a respawn timer runs out (eqtool #239).

nparseplus addition: the request is an open EQTool issue, nothing to port.
Watches TimersService.on_expired for "--Dead-- <victim>" rows in the Mob
Timers group and announces the pop via the injected Speaker (opt-in,
``spellwindow.respawn_expiry_audio``). Runs on the driver thread — the
Speaker protocol implementations queue onto their own worker.

A row with a variable respawn ("pop") window (#125) expires when its window
*closes*, which is the least useful moment to be told about — so it is also
announced when the window opens, on the same setting and the same name gate.
"""

from __future__ import annotations

import re

from nparseplus.config.settings import SpellWindowSettings
from nparseplus.core.timers import (
    MOB_TIMER_GROUP,
    Row,
    TimerRow,
    TimersService,
    series_label,
)
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
        timers.on_window_open.append(self._on_window_open)

    def _on_expired(self, rows: list[Row]) -> None:
        self._announce(rows, "spawn timer", "expired")

    def _on_window_open(self, rows: list[Row]) -> None:
        self._announce(rows, "spawn window", "open")

    def _announce(self, rows: list[Row], subject: str, verb: str) -> None:
        speaker = self.speaker
        if speaker is None or not self.settings.respawn_expiry_audio:
            return
        for row in rows:
            if (
                isinstance(row, TimerRow)
                and row.group == MOB_TIMER_GROUP
                and row.name.startswith(_DEAD_PREFIX)
            ):
                victim = _DUP_SUFFIX.sub("", row.name[len(_DEAD_PREFIX) :])
                # "Lodizal spawn window 2 of 3 open" — with several candidate
                # windows a bare announcement cannot say which chance just
                # came up, or how many are left after it (#125). The subject
                # and verb are split precisely so the label lands between
                # them; a lone window keeps its original wording exactly.
                which = series_label(row)
                middle = f"{subject} {which}" if which else subject
                speaker.speak(f"{victim} {middle} {verb}")
