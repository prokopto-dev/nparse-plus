"""WindowChatCommands — show_/hide_/toggle_<window> from self-sent chat."""

from __future__ import annotations

from datetime import datetime

import pytest

from nparseplus.core.bus import EventBus
from nparseplus.core.enums import CommsChannel
from nparseplus.core.events import CommsEvent, WindowCommandEvent
from nparseplus.core.triggers.window_commands import WindowChatCommands

T0 = datetime(2026, 7, 8, 21, 59, 36)


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def commands(bus: EventBus) -> list[WindowCommandEvent]:
    WindowChatCommands(bus)
    out: list[WindowCommandEvent] = []
    bus.subscribe(WindowCommandEvent, out.append)
    return out


def say(bus: EventBus, content: str, sender: str = "You") -> None:
    bus.publish(
        CommsEvent(
            timestamp=T0,
            line=f"{sender} say, '{content}'",
            line_number=1,
            channel=CommsChannel.SAY,
            content=content,
            sender=sender,
        )
    )


@pytest.mark.parametrize(
    ("content", "window", "action"),
    [
        ("toggle_maps", "maps", "toggle"),
        ("show_spells", "spells", "show"),
        ("hide_dps", "dps", "hide"),
        ("toggle_mobinfo", "mobinfo", "toggle"),
        ("toggle_console", "console", "toggle"),
        ("toggle_discord", "discord", "toggle"),
        ("show_triggereditor", "triggereditor", "show"),
    ],
)
def test_commands_parse(
    bus: EventBus, commands: list[WindowCommandEvent], content: str, window: str, action: str
) -> None:
    say(bus, content)
    assert [(e.window, e.action) for e in commands] == [(window, action)]


def test_only_self_sent_messages_count(bus: EventBus, commands: list[WindowCommandEvent]) -> None:
    say(bus, "toggle_maps", sender="Jaloy")
    assert commands == []


def test_non_commands_ignored(bus: EventBus, commands: list[WindowCommandEvent]) -> None:
    say(bus, "toggle_maps please")
    say(bus, "toggle_everything")
    say(bus, "PigTimer-30")
    assert commands == []
