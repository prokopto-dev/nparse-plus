"""NetWorker — one daemon thread for fire-and-forget REST calls.

Handlers run on the driver thread and must never block on the network, so a
send/fetch is ``submit(fetch, apply)``: ``fetch`` runs here, and ``apply``
(if any) is wrapped in a closure and handed to ``deliver`` — in the app
that is ``SharingCoordinator.enqueue_inbound``, so results are applied back
on the driver thread (the bus/timers are not thread-safe).

Every task failure is logged and swallowed; the loop never dies.
"""

from __future__ import annotations

import functools
import logging
import threading
from collections.abc import Callable
from queue import SimpleQueue
from typing import Any

logger = logging.getLogger(__name__)

_STOP = object()


class NetWorker:
    def __init__(self, deliver: Callable[[Callable[[], None]], None]) -> None:
        self._deliver = deliver
        self._queue: SimpleQueue[Any] = SimpleQueue()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="net-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._queue.put(_STOP)
        self._thread.join(timeout=5.0)
        self._thread = None

    def submit(
        self,
        fetch: Callable[[], Any],
        apply: Callable[[Any], None] | None = None,
    ) -> None:
        """Run ``fetch`` on the worker; deliver ``apply(result)`` back."""
        self._queue.put((fetch, apply))

    def _run(self) -> None:
        while True:
            task = self._queue.get()
            if task is _STOP:
                return
            fetch, apply = task
            try:
                result = fetch()
            except Exception:
                logger.warning("net worker task failed", exc_info=True)
                continue
            if apply is not None:
                self._deliver(functools.partial(apply, result))


class ImmediateWorker:
    """Test double: runs the task synchronously on the calling thread."""

    def submit(
        self,
        fetch: Callable[[], Any],
        apply: Callable[[Any], None] | None = None,
    ) -> None:
        result = fetch()
        if apply is not None:
            apply(result)

    def start(self) -> None: ...

    def stop(self) -> None: ...
