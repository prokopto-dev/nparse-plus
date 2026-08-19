"""Worker thread that tails the active log file and feeds the pipeline.

Pure stdlib threading — no Qt. Watches the log directory for a newer
character log (character switch) every few seconds and re-attaches,
emitting player-changed events around the swap.

Registration changes (parsers, ticks) may arrive from any thread — a plugin
enabled from Settings is the case that matters — so they are routed through
``submit_to_driver`` and applied on this thread between lines. See that
method; it is the one seam.

Tick callbacks registered through ``add_supervised_tick`` (plugins) are timed
and evicted if they repeatedly blow the budget — see ``TICK_BUDGET_S``. Ticks
appended straight to ``on_tick`` (the app's own services) are never timed and
never dropped: they are ours, and dropping e.g. ``TimersService.tick`` would
break the app more thoroughly than any stall.
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from queue import Empty, SimpleQueue

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


def _same_file(a: Path, b: Path) -> bool:
    """Same log, however each side spelled the path (Windows: same case)."""
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


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
        # Registration changes from other threads, applied at one fixed point
        # per loop iteration (see submit_to_driver).
        self._inbox: SimpleQueue[tuple[str, Callable[[], None]]] = SimpleQueue()
        # The driver owns the pipeline's chain the way it owns on_tick, so it
        # hands the pipeline the same inbox rather than leaving a second way
        # to mutate the chain from the GUI thread.
        pipeline.set_command_sink(self.submit_to_driver)

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

    def note_log_rotated(self, path: Path) -> None:
        """Our log archiver just emptied ``path`` — read it from the top.

        **Driver thread only**, which is the point: ``core.logarchive``
        truncates from its tick so that emptying the log and this reset are
        one step with no poll in between. Detection cannot cover this on its
        own — an emptied log the client refills to our read offset before the
        next poll is not smaller, and EQ's repeated identical lines are not
        different in content either. The tail keeps those checks as the
        backstop for rotations nobody tells us about.
        """
        if self._tail is not None and _same_file(self._tail.path, path):
            self._tail.restart()
            logger.info("%s was archived — reading it from the top", path.name)

    # -- the driver-thread command inbox -------------------------------------

    def is_running(self) -> bool:
        """True while the driver thread owns the log — i.e. commands queue."""
        return self._thread is not None and self._thread.is_alive()

    def submit_to_driver(self, fn: Callable[[], None], *, label: str) -> None:
        """Run ``fn`` on the driver thread at the next loop boundary.

        The seam every registration change goes through. The parser chain and
        the tick list belong to this thread: ``LogPipeline.process`` iterates
        the live chain with an early ``break``, so a removal from the GUI
        thread mid-line shifts the index and silently skips a parser for that
        line. Commands are drained at ONE point per iteration — after the
        log-switch check, before a single line is read — so a change lands
        between lines and never inside one. Same shape as
        ``SharingCoordinator.enqueue_inbound``: any thread enqueues, only the
        driver runs it.

        With the loop not running there is nothing to race with, so ``fn``
        runs immediately on the calling thread. That is what keeps startup
        wiring (plugins are activated before ``Backend.start``) and shutdown
        unwind (``backend.stop`` has already joined the thread) synchronous,
        as they were before this seam existed.

        ``label`` names the change in the log if ``fn`` raises; a raising
        command is that command's problem and never the loop's.
        """
        if not self.is_running():
            self._invoke(fn, label)
            return
        self._inbox.put((label, fn))

    def _drain_commands(self) -> None:
        while True:
            try:
                label, fn = self._inbox.get_nowait()
            except Empty:
                return
            self._invoke(fn, label)

    @staticmethod
    def _invoke(fn: Callable[[], None], label: str) -> None:
        try:
            fn()
        except Exception:
            logger.exception("driver command %s failed", label)

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

        Callable from any thread: the registration itself is routed through
        ``submit_to_driver``, so a plugin enabled mid-session joins the tick
        list between lines rather than during one.
        """

        def register() -> None:
            self.on_tick.append(fn)
            self._supervised[fn] = _SupervisedTick(label=label, on_dropped=on_dropped)

        self.submit_to_driver(register, label=f"register tick {label}")

    def remove_tick(self, fn: Callable[[datetime], None]) -> None:
        """Deregister a tick from any thread (already-dropped ones: no-op)."""
        self.submit_to_driver(lambda: self._remove_tick_now(fn), label="deregister tick")

    def _remove_tick_now(self, fn: Callable[[datetime], None]) -> None:
        """Driver-thread removal, applied immediately.

        The supervisor calls this rather than ``remove_tick``: an eviction
        decided mid-loop has to take effect for THIS iteration, and a queued
        removal would let the tick run — and breach — once more first. Safe
        mid-loop because ``_run_supervised_ticks`` iterates a copy.
        """
        self._supervised.pop(fn, None)
        with contextlib.suppress(ValueError):
            self.on_tick.remove(fn)

    # -- internals -----------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._iterate()
            except Exception:
                logger.exception("log driver iteration failed")
            self._stop.wait(POLL_INTERVAL_S)

    def _iterate(self) -> None:
        """One pass of the loop. Split out so tests can run exactly one."""
        self._maybe_switch_log()
        # THE drain point, and the reason it is here: after the log-switch
        # check so a command sees the tail it will affect, and before any
        # line is read so a chain change is applied between lines, never
        # between the parsers of one line.
        self._drain_commands()
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
            # Copied for the same reason _run_supervised_ticks copies: a tick
            # is free to mutate on_tick (app-owned ticks append to it
            # directly), and mutating a list mid-iteration shifts the index
            # and skips the neighbour. Copying a handful of bound methods is
            # not what the branch above is protecting against — the
            # per-callback clock reads are.
            for tick in list(self.on_tick):
                tick(now)

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
        self._remove_tick_now(tick)
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
