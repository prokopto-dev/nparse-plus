"""TimerWindowNotifier — put pop-window crossings on the bus (#125).

nparseplus addition; EQTool has no variable-respawn model to port. A
``TimerRow`` with a pop window opens at ``ends_at`` and expires at
``window_ends_at`` (see ``core.timers``), and ``TimersService`` reports both
through plain callback lists — it holds no bus reference and imports no
events, so this class is the bridge, exactly as ``RespawnExpiryNotifier`` is
for the speech.

Not folded into ``RespawnExpiryNotifier``: that one is speech, gated on
``spellwindow.respawn_expiry_audio`` *and* the ``--Dead-- `` name prefix. The
bus bridge has to be unconditional and name-agnostic — a plugin's window timer
in its own group is exactly the caller this seam exists for.

Runs on the driver thread, like every other publisher.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

from nparseplus.core.bus import EventBus
from nparseplus.core.events import TimerWindowClosedEvent, TimerWindowOpenedEvent
from nparseplus.core.timers import Row, TimerRow, TimersService


def _window_rows(rows: list[Row]) -> Iterator[tuple[TimerRow, datetime]]:
    """The rows carrying a pop window, paired with its close time.

    The same predicate as ``timers.has_pop_window``, spelled out so the ``Row``
    union narrows to the one type that has the fields.
    """
    for row in rows:
        if isinstance(row, TimerRow) and row.window_ends_at is not None:
            yield row, row.window_ends_at


class TimerWindowNotifier:
    def __init__(self, bus: EventBus, timers: TimersService) -> None:
        self.bus = bus
        timers.on_window_open.append(self._on_window_open)
        timers.on_expired.append(self._on_expired)

    def _on_window_open(self, rows: list[Row]) -> None:
        for row, closes_at in _window_rows(rows):
            self.bus.publish(
                TimerWindowOpenedEvent(
                    name=row.name,
                    group=row.group,
                    opens_at=row.ends_at,
                    closes_at=closes_at,
                    # The tick that observed the crossover; tick() has just
                    # stamped it, so the fallback is unreachable in practice.
                    opened_at=row.window_opened_at or row.ends_at,
                )
            )

    def _on_expired(self, rows: list[Row]) -> None:
        # on_expired carries every kind of expiry; only window rows are ours.
        for row, closes_at in _window_rows(rows):
            self.bus.publish(
                TimerWindowClosedEvent(
                    name=row.name,
                    group=row.group,
                    opens_at=row.ends_at,
                    closes_at=closes_at,
                    closed_at=closes_at,
                )
            )
