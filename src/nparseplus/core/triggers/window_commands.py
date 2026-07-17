"""Chat-driven window show/hide — nparseplus addition (nparse #42/#64).

The original nparse toggled its Maps/Spells windows from in-game macros;
EQTool has no equivalent. Watches CommsEvents for a message that is exactly::

    show_<window> | hide_<window> | toggle_<window>

e.g. ``/say toggle_maps`` or a macro line. Only messages *you* send count
(unlike PigTimer commands, which accept any sender — a groupmate should not
be able to blank your overlays). Publishes a WindowCommandEvent; the Qt side
(app.py) maps the window name onto the live window handles.
"""

from __future__ import annotations

import re

from nparseplus.core.bus import EventBus, Unsubscribe
from nparseplus.core.events import CommsEvent, WindowCommandEvent

WINDOW_NAMES = ("maps", "spells", "dps", "mobinfo", "console", "discord", "triggereditor")

_COMMAND_RE = re.compile(
    r"^(?P<action>show|hide|toggle)_(?P<window>" + "|".join(WINDOW_NAMES) + r")$"
)


class WindowChatCommands:
    """CommsEvent subscriber that turns self-sent chat commands into
    WindowCommandEvents."""

    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        self._unsubscribe: Unsubscribe | None = bus.subscribe(CommsEvent, self._on_comms)

    def close(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    def _on_comms(self, event: CommsEvent) -> None:
        if event.sender != "You":
            return
        match = _COMMAND_RE.match(event.content.strip())
        if match is None:
            return
        self.bus.publish(
            WindowCommandEvent(
                timestamp=event.timestamp,
                line=event.line,
                line_number=event.line_number,
                window=match.group("window"),
                action=match.group("action"),
            )
        )
