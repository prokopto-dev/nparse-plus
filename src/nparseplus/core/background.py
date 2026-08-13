"""One-at-a-time off-thread work for driver-tick services (Qt-free).

The driver thread tails the log, runs the parser chain and ticks every
countdown in the app, so anything slow on it stutters all of them —
``core.driver`` puts the number at 250 ms and evicts *plugin* ticks that
exceed it. App-owned ticks are never metered, which makes it our own job not
to hand them blocking work: spawning ``pgrep`` (17.6 ms mean, 5 s worst
case) or copying a 100 MB log are both well past a budget a plugin would be
dropped for.

``BackgroundJob`` is the seam. The tick decides *whether* work is due —
cheap, on the driver thread — and submits it; the job runs it on a one-shot
daemon thread and refuses to start a second run while one is in flight, so a
slow sweep can never pile up behind itself.

**What may run here:** filesystem and subprocess work only. The bus and
TimersService are not thread-safe and must never be touched off the driver
thread (see the thread-crossing rule in CLAUDE.md); anything with a result
the app must act on goes through an inbox, not through this.

Tests inject ``spawn=run_inline`` to keep the work synchronous.
"""

from __future__ import annotations

import functools
import logging
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)

#: How a job starts its work: ``spawn(thread_name, work)``.
Spawn = Callable[[str, Callable[[], None]], None]


def spawn_thread(name: str, work: Callable[[], None]) -> None:
    """The default spawn: a one-shot daemon thread."""
    threading.Thread(target=work, name=name, daemon=True).start()


def run_inline(_name: str, work: Callable[[], None]) -> None:
    """Test spawn: run the work on the calling thread."""
    work()


class BackgroundJob:
    """Runs submitted work off the caller's thread, one run at a time."""

    def __init__(self, name: str, *, spawn: Spawn | None = None) -> None:
        self.name = name
        self._spawn = spawn or spawn_thread
        self._lock = threading.Lock()
        self._idle = threading.Event()
        self._idle.set()

    @property
    def running(self) -> bool:
        """True while a submitted run is in flight."""
        return not self._idle.is_set()

    def submit(self, work: Callable[[], None]) -> bool:
        """Start ``work`` off-thread; False if a run is already in flight.

        Never raises: a spawn failure is logged and reported as False, so a
        driver tick cannot be taken down by one.
        """
        with self._lock:
            if not self._idle.is_set():
                return False
            self._idle.clear()
        try:
            self._spawn(self.name, functools.partial(self._run, work))
        except Exception:
            logger.exception("could not start background job %s", self.name)
            self._idle.set()
            return False
        return True

    def wait(self, timeout: float = 5.0) -> bool:
        """Block until nothing is in flight. True if the job went idle."""
        return self._idle.wait(timeout)

    def _run(self, work: Callable[[], None]) -> None:
        try:
            work()
        except Exception:  # pragma: no cover - defensive; callers log their own
            logger.exception("background job %s failed", self.name)
        finally:
            self._idle.set()
