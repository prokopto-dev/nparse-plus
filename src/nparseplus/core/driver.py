"""Worker thread that tails the active log file and feeds the pipeline.

Pure stdlib threading — no Qt. Watches the log directory for a newer
character log (character switch) every few seconds and re-attaches,
emitting player-changed events around the swap.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from nparseplus.core.bus import EventBus
from nparseplus.core.events import AfterPlayerChangedEvent, BeforePlayerChangedEvent
from nparseplus.core.logfile import LogTail, find_active_log, parse_log_filename
from nparseplus.core.pipeline import LogPipeline
from nparseplus.core.player import ActivePlayer

logger = logging.getLogger(__name__)

POLL_INTERVAL_S = 0.1
LOG_SWITCH_CHECK_S = 3.0


class LogDriver:
    def __init__(
        self,
        log_dir: Path,
        pipeline: LogPipeline,
        bus: EventBus,
        player: ActivePlayer,
        server_lookup: Callable[[str], object] | None = None,
    ) -> None:
        self.log_dir = log_dir
        self._pipeline = pipeline
        self._bus = bus
        self._player = player
        self._tail: LogTail | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_switch_check = 0.0

    @property
    def active_path(self) -> Path | None:
        return self._tail.path if self._tail else None

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

    # -- internals -----------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._maybe_switch_log()
                if self._tail is not None:
                    for line in self._tail.poll():
                        self._pipeline.process(line)
            except Exception:
                logger.exception("log driver iteration failed")
            self._stop.wait(POLL_INTERVAL_S)

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
        char_name, _server = parsed
        ts = datetime.now()
        if self._player.is_configured:
            self._bus.publish(BeforePlayerChangedEvent(timestamp=ts))
        self._player.reset_for(char_name, None)
        self._tail = LogTail.attach(newest)
        self._bus.publish(AfterPlayerChangedEvent(timestamp=ts))
        logger.info("tailing %s (character: %s)", newest.name, char_name)
