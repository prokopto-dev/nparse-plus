"""Death-loop detection state (port of EQTool DeathLoopHandler.cs internals).

A death loop is N of your own deaths inside a rolling window with no
intervening signs of life (melee, casting, or chat). The bus-facing handler
lives in ``nparseplus.core.handlers.death_loop``; this service is pure state
so it can be tested with fixed clocks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

DEFAULT_DEATH_LOOP_DEATHS = 4
DEFAULT_DEATH_LOOP_SECONDS = 120


@dataclass
class DeathLoopService:
    deaths_threshold: int = DEFAULT_DEATH_LOOP_DEATHS
    window_seconds: float = DEFAULT_DEATH_LOOP_SECONDS
    # Oldest first; timestamps roll off once outside the window.
    _death_timestamps: list[datetime] = field(default_factory=list)

    @property
    def death_count(self) -> int:
        return len(self._death_timestamps)

    def is_death_looping(self) -> bool:
        return len(self._death_timestamps) >= self.deaths_threshold

    def prune(self, now: datetime) -> None:
        """Roll old deaths off the front of the window (UpdateDeathList)."""
        while self._death_timestamps:
            elapsed = (now - self._death_timestamps[0]).total_seconds()
            if elapsed > self.window_seconds:
                self._death_timestamps.pop(0)
            else:
                break

    def record_death(self, timestamp: datetime) -> bool:
        """Add one of our deaths; True when the loop threshold is reached."""
        self.prune(timestamp)
        self._death_timestamps.append(timestamp)
        return self.is_death_looping()

    def record_activity(self) -> None:
        """Any sign of life (melee/cast/chat) clears the tracked deaths."""
        self._death_timestamps.clear()
