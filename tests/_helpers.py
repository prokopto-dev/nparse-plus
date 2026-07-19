"""Shared test-only helpers imported by multiple test modules.

Kept as a plain module (not a conftest) so the identical ``EventCollector`` /
``FakeSpeaker`` live in one place; the per-area conftests re-export them so the
existing ``from tests.core.<area>.conftest import ...`` imports keep working.
"""

from __future__ import annotations

from nparseplus.core.bus import EventBus


class FakeSpeaker:
    """Records TTS output (Speaker protocol)."""

    def __init__(self) -> None:
        self.spoken: list[str] = []
        self.interrupts = 0

    def speak(self, text: str) -> None:
        self.spoken.append(text)

    def interrupt(self) -> None:
        self.interrupts += 1


class EventCollector:
    """Subscribes to every event type and records what the bus publishes."""

    def __init__(self, bus: EventBus) -> None:
        self.events: list[object] = []
        bus.subscribe_all(self.events.append)

    def of_type[E](self, event_type: type[E]) -> list[E]:
        return [e for e in self.events if type(e) is event_type]

    def single[E](self, event_type: type[E]) -> E:
        matches = self.of_type(event_type)
        assert len(matches) == 1, f"expected 1 {event_type.__name__}, got {len(matches)}"
        return matches[0]
