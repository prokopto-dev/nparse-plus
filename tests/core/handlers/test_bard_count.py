"""BardCountHandler — swarm/AOE hit-and-resist session summaries.

Driven with direct LineEvents (fixed timestamps) so session windows are
exact; the pipeline's LineEvent firehose delivers the same payloads live.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

import pytest
from tests.core.handlers.conftest import T0, EventCollector, FakeSpeaker

from nparseplus.core.bus import EventBus
from nparseplus.core.events import CommsEvent, LineEvent, OverlayEvent
from nparseplus.core.handlers.bard_count import BardCountHandler
from nparseplus.core.player import ActivePlayer
from nparseplus.core.timers import CounterRow, TimersService


class BardHarness:
    def __init__(self, enabled: Callable[[], bool] = lambda: True) -> None:
        self.bus = EventBus()
        self.player = ActivePlayer(name="Tester")
        self.speaker = FakeSpeaker()
        self.timers = TimersService()
        self.handler = BardCountHandler(
            self.bus, self.player, self.speaker, timers=self.timers, enabled=enabled
        )
        self.collector = EventCollector(self.bus)
        self._counter = 0

    def line(self, message: str, timestamp: datetime = T0) -> None:
        self._counter += 1
        self.bus.publish(LineEvent(timestamp=timestamp, line=message, line_number=self._counter))

    def summaries(self) -> list[str]:
        return [e.content for e in self.collector.of_type(CommsEvent) if e.sender == "System"]


@pytest.fixture
def h() -> BardHarness:
    return BardHarness()


def test_resist_burst_summarized(h: BardHarness) -> None:
    h.line("Your target resisted the Chords of Dissonance spell.")
    h.line("Your target resisted the Chords of Dissonance spell.", T0 + timedelta(milliseconds=200))
    h.handler.flush()
    assert h.summaries() == ["2 Total | 2 Resists"]
    assert h.speaker.spoken == ["2 Total | 2 Resists"]
    overlay = [e for e in h.collector.of_type(OverlayEvent)]
    assert overlay[0].text == "2 Total | 2 Resists"


def test_hits_and_resists_in_one_session(h: BardHarness) -> None:
    h.line("Your target resisted the Chords of Dissonance spell.")
    h.line("a gnoll winces.", T0 + timedelta(milliseconds=100))
    h.line("a rat winces.", T0 + timedelta(milliseconds=200))
    h.handler.flush()
    assert h.summaries() == ["3 Total | 2 Hits | 1 Resist"]


def test_singular_hit_wording(h: BardHarness) -> None:
    h.line("a gnoll winces.")
    h.handler.flush()
    # The chat-stream record is still written, but a single-hit session is
    # suppressed from the overlay + TTS (deliberate divergence from the C#).
    assert h.summaries() == ["1 Total | 1 Hit"]
    assert h.collector.of_type(OverlayEvent) == []
    assert h.speaker.spoken == []


def test_two_hit_session_emits_overlay_and_tts(h: BardHarness) -> None:
    h.line("a gnoll winces.")
    h.line("a rat winces.", T0 + timedelta(milliseconds=100))
    h.handler.flush()
    assert h.summaries() == ["2 Total | 2 Hits"]
    assert h.speaker.spoken == ["2 Total | 2 Hits"]
    overlay = h.collector.of_type(OverlayEvent)
    assert overlay and overlay[-1].text == "2 Total | 2 Hits"


def test_disabled_suppresses_overlay_and_tts() -> None:
    h = BardHarness(enabled=lambda: False)
    h.line("Your target resisted the Chords of Dissonance spell.")
    h.line("Your target resisted the Chords of Dissonance spell.", T0 + timedelta(milliseconds=200))
    h.handler.flush()
    assert h.collector.of_type(OverlayEvent) == []
    assert h.speaker.spoken == []
    # The persistent chat-stream record is kept regardless of the toggle.
    assert h.summaries() == ["2 Total | 2 Resists"]


def test_bound_by_music_counts_as_hit(h: BardHarness) -> None:
    h.line("a gnoll is bound by silver strands of music.")
    h.line("a rat is bound in chords of music.", T0 + timedelta(milliseconds=100))
    h.handler.flush()
    assert h.summaries() == ["2 Total | 2 Hits"]


def test_events_outside_window_start_new_session(h: BardHarness) -> None:
    h.line("a gnoll winces.")
    h.line("a rat winces.", T0 + timedelta(seconds=2))
    h.handler.flush()
    assert h.summaries() == ["1 Total | 1 Hit", "1 Total | 1 Hit"]


def test_non_bard_resists_are_ignored(h: BardHarness) -> None:
    h.line("Your target resisted the Ice Comet spell.")
    h.handler.flush()
    assert h.summaries() == []


def test_you_resist_variant(h: BardHarness) -> None:
    h.line("You resist the Chords of Dissonance spell!")
    h.handler.flush()
    assert h.summaries() == ["1 Total | 1 Resist"]


def test_counter_rows_track_tallies(h: BardHarness) -> None:
    h.line("Your target resisted the Chords of Dissonance spell.")
    h.line("Your target resisted the Chords of Dissonance spell.", T0 + timedelta(milliseconds=50))
    counters = [r for r in h.timers.snapshot() if isinstance(r, CounterRow)]
    assert len(counters) == 1
    assert counters[0].name == "Chords of Dissonance Resists"
    assert counters[0].count == 2
