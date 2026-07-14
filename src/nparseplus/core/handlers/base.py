"""BaseHandler — common dependencies for event handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nparseplus.core.bus import EventBus

if TYPE_CHECKING:
    from nparseplus.core.player import ActivePlayer


class BaseHandler:
    """Subclasses subscribe to bus events in ``__init__`` and stay alive for
    the app's lifetime (held by the composition container)."""

    def __init__(self, bus: EventBus, player: ActivePlayer) -> None:
        self.bus = bus
        self.player = player
