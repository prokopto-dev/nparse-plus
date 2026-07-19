"""Test QtEventBridge — the sole driver→GUI thread crossing in the app.

Emitting on the same thread as the receiver makes the queued signal fire
synchronously, which is enough to assert the re-emission and detach contracts.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from nparseplus.core.bus import EventBus
from nparseplus.core.events import LineEvent
from nparseplus.ui.qtbridge import QtEventBridge

pytestmark = pytest.mark.qt

NOW = datetime(2026, 7, 14, 12, 0, 0)


def test_bridge_reemits_bus_events_as_a_qt_signal(qtbot) -> None:
    bus = EventBus()
    bridge = QtEventBridge(bus)
    received: list[object] = []
    bridge.event_received.connect(received.append)

    event = LineEvent(timestamp=NOW, line="You begin casting Clarity.", line_number=1)
    with qtbot.waitSignal(bridge.event_received, timeout=1000) as blocker:
        bus.publish(event)

    assert blocker.args == [event]
    assert received == [event]


def test_detach_stops_reemission(qtbot) -> None:
    bus = EventBus()
    bridge = QtEventBridge(bus)
    received: list[object] = []
    bridge.event_received.connect(received.append)

    bridge.detach()
    bus.publish(LineEvent(timestamp=NOW, line="You begin casting Clarity.", line_number=1))
    qtbot.wait(50)

    assert received == []
