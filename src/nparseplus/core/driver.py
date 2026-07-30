"""Worker thread that tails the active log file and feeds the pipeline.

Pure stdlib threading — no Qt. Watches the log directory for a newer
character log (character switch) every few seconds and re-attaches,
emitting player-changed events around the swap.

Tick callbacks registered through ``add_supervised_tick`` (plugins) are timed
and evicted if they repeatedly blow the budget — see ``TICK_BUDGET_S``. Ticks
appended straight to ``on_tick`` (the app's own services) are never timed and
never dropped: they are ours, and dropping e.g. ``TimersService.tick`` would
break the app more thoroughly than any stall.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from nparseplus.core.bus import EventBus
from nparseplus.core.events import AfterPlayerChangedEvent, BeforePlayerChangedEvent
from nparseplus.core.logfile import (
    LogTail,
    find_active_log,
    parse_log_filename,
    server_from_log_token,
)
from nparseplus.core.pipeline import LogPipeline
from nparseplus.core.player import ActivePlayer

logger = logging.getLogger(__name__)

POLL_INTERVAL_S = 0.1
LOG_SWITCH_CHECK_S = 3.0

# Everything the driver owns runs on one thread: log tailing, the parser
# chain, timer countdowns, the DPS window's fight tracker and the sharing
# coordinator's inbound drain. A tick that takes 250 ms therefore costs the
# whole app 250 ms — two and a half missed poll intervals, and a visible
# stutter in every countdown. That is the point where "slow" stops being a
# plugin's own problem, so it is the budget.
TICK_BUDGET_S = 0.25
# ...but one breach is not evidence of a bad plugin: a GC pause, a cold
# import inside the first call, or the machine waking from sleep can all
# stretch a single tick. Two CONSECUTIVE breaches means it is the callback,
# not the weather, and the tick is dropped for the rest of the session.
TICK_BREACH_LIMIT = 2


@dataclass
class _SupervisedTick:
    """Bookkeeping for one plugin-registered tick."""

    label: str
    on_dropped: Callable[[str], None] | None = None
    breaches: int = field(default=0)


class LogDriver:
    def __init__(
        self,
        log_dir: Path,
        pipeline: LogPipeline,
        bus: EventBus,
        player: ActivePlayer,
    ) -> None:
        self.log_dir = log_dir
        self._pipeline = pipeline
        self._bus = bus
        self._player = player
        self._tail: LogTail | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_switch_check = 0.0
        # Called every loop iteration on the driver thread (timer ticking etc.).
        self.on_tick: list[Callable[[datetime], None]] = []
        # Only the subset of on_tick that is under the time budget. Empty for
        # every user with no plugins, which is what keeps the timing path off
        # the hot loop entirely (see _run).
        self._supervised: dict[Callable[[datetime], None], _SupervisedTick] = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="log-driver", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def set_log_dir(self, log_dir: Path) -> None:
        self.log_dir = log_dir
        self._tail = None  # force re-discovery on next loop

    # -- tick registration ---------------------------------------------------

    def add_supervised_tick(
        self,
        fn: Callable[[datetime], None],
        *,
        label: str,
        on_dropped: Callable[[str], None] | None = None,
    ) -> None:
        """Register a tick that the driver may evict for being too slow.

        Used for third-party (plugin) callbacks; ``on_dropped`` is called on
        the driver thread with a human-readable reason so the owner can
        surface the fact. App-owned ticks append to ``on_tick`` directly and
        are never supervised.
        """
        self.on_tick.append(fn)
        self._supervised[fn] = _SupervisedTick(label=label, on_dropped=on_dropped)

    def remove_tick(self, fn: Callable[[datetime], None]) -> None:
        """Deregister a tick (already-dropped ones unregister silently)."""
        self._supervised.pop(fn, None)
        with contextlib.suppress(ValueError):
            self.on_tick.remove(fn)

    # -- internals -----------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._maybe_switch_log()
                if self._tail is not None:
                    for line in self._tail.poll():
                        self._pipeline.process(line)
                now = datetime.now()
                # No plugins => no supervision dict => the plain loop, with no
                # per-callback clock reads at all. This is the app's hottest
                # loop and the empty case is the common one; keep it free.
                if self._supervised:
                    self._run_supervised_ticks(now)
                else:
                    for tick in self.on_tick:
                        tick(now)
            except Exception:
                logger.exception("log driver iteration failed")
            self._stop.wait(POLL_INTERVAL_S)

    def _run_supervised_ticks(self, now: datetime) -> None:
        # Iterate a copy: a drop mutates on_tick mid-loop, and a plugin tick
        # is free to register or unwind another one while it runs.
        for tick in list(self.on_tick):
            watch = self._supervised.get(tick)
            if watch is None:
                tick(now)  # app-owned: unbudgeted, as before
                continue
            started = time.perf_counter()
            try:
                tick(now)
            finally:
                elapsed = time.perf_counter() - started
            self._record_tick_duration(tick, watch, elapsed)

    def _record_tick_duration(
        self,
        tick: Callable[[datetime], None],
        watch: _SupervisedTick,
        elapsed: float,
    ) -> None:
        if elapsed < TICK_BUDGET_S:
            watch.breaches = 0  # only CONSECUTIVE breaches count
            return
        watch.breaches += 1
        logger.warning(
            "tick %s took %.0f ms (budget %.0f ms) — breach %d of %d",
            watch.label,
            elapsed * 1000,
            TICK_BUDGET_S * 1000,
            watch.breaches,
            TICK_BREACH_LIMIT,
        )
        if watch.breaches < TICK_BREACH_LIMIT:
            return
        reason = (
            f"tick removed after {watch.breaches} consecutive runs over "
            f"{TICK_BUDGET_S * 1000:.0f} ms (last: {elapsed * 1000:.0f} ms)"
        )
        self.remove_tick(tick)
        logger.error("%s: %s — the rest of the app keeps running", watch.label, reason)
        if watch.on_dropped is not None:
            try:
                watch.on_dropped(reason)
            except Exception:
                logger.exception("drop notification for %s raised", watch.label)

    def _maybe_switch_log(self) -> None:
        now = time.monotonic()
        if self._tail is not None and now - self._last_switch_check < LOG_SWITCH_CHECK_S:
            return
        self._last_switch_check = now
        newest = find_active_log(self.log_dir)
        if newest is None or (self._tail is not None and newest == self._tail.path):
            return
        parsed = parse_log_filename(newest)
        if not parsed:
            return
        char_name, server_token = parsed
        ts = datetime.now()
        if self._player.is_configured:
            self._bus.publish(BeforePlayerChangedEvent(timestamp=ts))
        self._player.reset_for(char_name, server_from_log_token(server_token))
        self._tail = LogTail.attach(newest)
        self._bus.publish(AfterPlayerChangedEvent(timestamp=ts))
        logger.info("tailing %s (character: %s)", newest.name, char_name)
