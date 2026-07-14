"""Type-keyed synchronous event bus — the Python analogue of LogEvents.cs.

Handlers subscribe by event class; ``publish`` dispatches to subscribers of
the event's exact type (no MRO walking — mirrors EQTool's per-type C#
events). Dispatch runs inline on the caller's thread; the UI crosses threads
via ``nparseplus.ui.qtbridge``, never here.
"""

from __future__ import annotations

import contextlib
import logging
from collections import defaultdict
from collections.abc import Callable

logger = logging.getLogger(__name__)

type Unsubscribe = Callable[[], None]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[type, list[Callable]] = defaultdict(list)
        self._firehose: list[Callable] = []

    def subscribe[E](self, event_type: type[E], fn: Callable[[E], None]) -> Unsubscribe:
        self._subscribers[event_type].append(fn)

        def _unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._subscribers[event_type].remove(fn)

        return _unsubscribe

    def subscribe_all(self, fn: Callable[[object], None]) -> Unsubscribe:
        """Receive every published event (used by the Qt bridge and console)."""
        self._firehose.append(fn)

        def _unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._firehose.remove(fn)

        return _unsubscribe

    def publish(self, event: object) -> None:
        # list() so handlers may (un)subscribe during dispatch.
        for fn in list(self._subscribers.get(type(event), ())):
            try:
                fn(event)
            except Exception:
                logger.exception("event handler %r failed for %r", fn, type(event).__name__)
        for fn in list(self._firehose):
            try:
                fn(event)
            except Exception:
                logger.exception("firehose handler %r failed", fn)
