"""EventBus → Qt signal marshalling.

The parse pipeline runs on a worker thread; Qt widgets must only be touched
from the GUI thread. This bridge subscribes to the core bus firehose and
re-emits every event on the GUI thread. It is the ONLY thread-crossing
mechanism in the app — the Python analogue of EQTool's AppDispatcher.

Events are buffered and delivered in one coalesced flush per GUI-thread
wake-up instead of one queued Qt event apiece: under heavy combat the
driver publishes hundreds of events per second, and the per-event queue
round-trips were themselves a GUI stutter source. Batching is naturally
load-adaptive — when the GUI thread is idle a flush fires immediately
(latency ≈ one event-loop turn), and when it is busy everything that
arrives in the meantime rides the next flush. Order is always preserved.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, Signal

from nparseplus.core.bus import EventBus


class QtEventBridge(QObject):
    """Connect widget slots to ``event_received`` (delivered on the GUI
    thread, one call per event, in publish order), or to ``events_batch``
    (one call per flush with the ordered event list) for bulk consumers
    like the console."""

    event_received = Signal(object)
    events_batch = Signal(list)
    # Internal wake-up: emitted (at most once per pending flush) from the
    # parser thread, delivered queued on the GUI thread.
    _flush_requested = Signal()

    def __init__(self, bus: EventBus, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._lock = threading.Lock()
        self._buffer: list[object] = []
        self._flush_scheduled = False
        self._flush_requested.connect(self._flush, Qt.ConnectionType.QueuedConnection)
        self._unsubscribe = bus.subscribe_all(self._on_event)

    def _on_event(self, event: object) -> None:  # called on the parser thread
        with self._lock:
            self._buffer.append(event)
            schedule = not self._flush_scheduled
            self._flush_scheduled = True
        if schedule:
            self._flush_requested.emit()

    def _flush(self) -> None:  # GUI thread
        with self._lock:
            events = self._buffer
            self._buffer = []
            self._flush_scheduled = False
        if not events:
            return
        self.events_batch.emit(list(events))
        for event in events:
            self.event_received.emit(event)

    def flush_now(self) -> None:
        """Deliver any buffered events immediately (tests, shutdown)."""
        self._flush()

    def detach(self) -> None:
        self._unsubscribe()
        # Drain the tail so nothing published before detach is dropped.
        self._flush()
