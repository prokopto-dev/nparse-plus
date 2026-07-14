"""EventBus → Qt signal marshalling.

The parse pipeline runs on a worker thread; Qt widgets must only be touched
from the GUI thread. This bridge subscribes to the core bus firehose and
re-emits every event through a queued Qt signal, which Qt delivers on the
GUI thread. It is the ONLY thread-crossing mechanism in the app — the
Python analogue of EQTool's AppDispatcher.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from nparseplus.core.bus import EventBus


class QtEventBridge(QObject):
    """Connect widget slots to ``event_received`` (queued by default because
    the emit happens off the GUI thread)."""

    event_received = Signal(object)

    def __init__(self, bus: EventBus, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._unsubscribe = bus.subscribe_all(self._on_event)

    def _on_event(self, event: object) -> None:  # called on the parser thread
        self.event_received.emit(event)

    def detach(self) -> None:
        self._unsubscribe()
