"""Chat-driven window show/hide — nparseplus addition (nparse #42/#64).

The original nparse toggled its Maps/Spells windows from in-game macros;
EQTool has no equivalent. Watches CommsEvents for a message that is exactly::

    show_<window> | hide_<window> | toggle_<window>

e.g. ``/say toggle_maps`` or a macro line. Only messages *you* send count
(unlike PigTimer commands, which accept any sender — a groupmate should not
be able to blank your overlays). Publishes a WindowCommandEvent; the Qt side
(app.py) maps the window name onto the live window handles.

**The window name is not validated here (#50).** It used to be an alternation
over a frozen tuple of built-in names, which meant the Macro Editor and every
plugin window — whose command keys only exist at runtime — could never be
reached, however well the consumer side was wired. ``_apply_window_command``
already resolves through the live handle table and no-ops on a name it does
not know, so that decision belongs there and this only has to recognize the
shape. Nothing is starved by the wider match: this is a CommsEvent subscriber,
not a line parser, so it consumes nothing from the parser chain.

``pluginbootstrap._plugin_command_key`` sanitizes a plugin's command key with
``re.sub(r"\\W", "_", ...)``, so every generated key matches ``\\w+`` here by
construction. The two patterns must stay in agreement.
"""

from __future__ import annotations

import re

from nparseplus.core.bus import EventBus, Unsubscribe
from nparseplus.core.events import CommsEvent, WindowCommandEvent

_COMMAND_RE = re.compile(r"^(?P<action>show|hide|toggle)_(?P<window>\w+)$")


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
