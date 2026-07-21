"""Bridge under raid load: the coalesced flush must deliver every event in
order while bounding the number of GUI-thread wake-ups well below the event
count."""

from __future__ import annotations

import pytest
from tests.stress_log import raid_lines

from nparseplus.audio.tts import NullSpeaker
from nparseplus.composition import build_backend
from nparseplus.config.settings import Settings
from nparseplus.core.events import LineEvent
from nparseplus.ui.qtbridge import QtEventBridge

pytestmark = pytest.mark.qt


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_raid_burst_is_coalesced_in_order(qtbot) -> None:
    backend = build_backend(Settings(), speaker=NullSpeaker())
    bridge = QtEventBridge(backend.bus)

    published: list[object] = []
    backend.bus.subscribe_all(published.append)
    received: list[object] = []
    bridge.event_received.connect(received.append)
    flushes: list[int] = []
    bridge.events_batch.connect(lambda events: flushes.append(len(events)))

    for raw in raid_lines(200):
        backend.pipeline.process(raw)
    bridge.flush_now()

    assert received == published  # nothing dropped, nothing reordered
    assert sum(flushes) == len(published)
    # The whole burst arrived while the GUI thread never re-entered the event
    # loop, so it must ride a handful of flushes — not one wake per event.
    assert len(flushes) < len(published) / 10
    line_events = [e for e in received if isinstance(e, LineEvent)]
    assert [e.line_number for e in line_events] == sorted(e.line_number for e in line_events)
