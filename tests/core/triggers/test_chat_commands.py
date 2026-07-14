"""StartTimer-/PigTimer- chat command parsing (CustomTimerHandlerTests.cs)."""

from datetime import UTC, datetime

import pytest

from nparseplus.core.bus import EventBus
from nparseplus.core.enums import CommsChannel
from nparseplus.core.events import CommsEvent
from nparseplus.core.triggers.chat_commands import CustomTimerChatCommands, parse_custom_timer

T0 = datetime(2026, 7, 14, 12, 0, 0, tzinfo=UTC)


class FakeTimers:
    def __init__(self) -> None:
        self.added: list[tuple[str, int, str, str, str]] = []
        self.cancelled: list[str] = []

    def add_timer(self, name: str, seconds: int, color: str, icon: str, restart: str) -> None:
        self.added.append((name, seconds, color, icon, restart))

    def cancel(self, name: str) -> None:
        self.cancelled.append(name)


@pytest.mark.parametrize(
    ("content", "name", "seconds"),
    [
        ("PigTimer-30:00-StupidGoblin", "StupidGoblin", 30 * 60),
        ("PigTimer-30:20-StupidGoblin", "StupidGoblin", 30 * 60 + 20),
        ("PigTimer-90:20-StupidGoblin", "StupidGoblin", 90 * 60 + 20),
        (
            "PigTimer-30:00-StupidGoblin_with_club_near_me",
            "StupidGoblin_with_club_near_me",
            30 * 60,
        ),
        ("PigTimer-02", "PigTimer-02", 2),
        ("PigTimer-02:03", "PigTimer-02:03", 2 * 60 + 3),
        ("PigTimer-02:03:04", "PigTimer-02:03:04", 2 * 3600 + 3 * 60 + 4),
        ("PigTimer-02-xyzzy", "xyzzy", 2),
        ("PigTimer-02:03-xyzzy", "xyzzy", 2 * 60 + 3),
        ("PigTimer-02:03:04-xyzzy", "xyzzy", 2 * 3600 + 3 * 60 + 4),
        # StartTimer- is an alias with identical semantics
        ("StartTimer-30", "StartTimer-30", 30),
        ("StartTimer-10:00", "StartTimer-10:00", 10 * 60),
        ("StartTimer-1:02:00-LongTimer", "LongTimer", 3600 + 2 * 60),
        ("StartTimer-6:40-Tim_the_Mighty", "Tim_the_Mighty", 6 * 60 + 40),
    ],
)
def test_parse_custom_timer(content: str, name: str, seconds: int) -> None:
    parsed = parse_custom_timer(content)
    assert parsed is not None
    assert parsed.name == name
    assert parsed.seconds == seconds


@pytest.mark.parametrize(
    "content",
    [
        "",
        "hello there",
        "a PigTimer-30 midway does not count",  # must start the message
        "PigTimer 30",  # no dash
        "PigTimer-",  # no duration
        "StartTimer-abc",  # non-numeric duration
    ],
)
def test_parse_rejects_non_commands(content: str) -> None:
    assert parse_custom_timer(content) is None


def comms(content: str, channel: CommsChannel, sender: str = "Soandso") -> CommsEvent:
    return CommsEvent(
        timestamp=T0,
        line=f"{sender} says, '{content}'",
        channel=channel,
        content=content,
        sender=sender,
    )


def test_any_channel_and_any_sender_can_start_a_timer() -> None:
    bus = EventBus()
    timers = FakeTimers()
    CustomTimerChatCommands(bus, timers)

    for channel in (
        CommsChannel.SAY,
        CommsChannel.TELL,
        CommsChannel.GROUP,
        CommsChannel.GUILD,
        CommsChannel.AUCTION,
        CommsChannel.OOC,
        CommsChannel.SHOUT,
    ):
        bus.publish(comms("StartTimer-30-Guard", channel, sender="Someoneelse"))

    assert len(timers.added) == 7
    name, seconds, color, icon, restart = timers.added[0]
    assert (name, seconds) == ("Guard", 30)
    assert color == "DarkSeaGreen"
    assert icon == "Feign Death"
    assert restart == "StartNewTimer"


def test_non_command_chat_is_ignored_and_close_unsubscribes() -> None:
    bus = EventBus()
    timers = FakeTimers()
    handler = CustomTimerChatCommands(bus, timers)

    bus.publish(comms("selling fine steel weapons", CommsChannel.AUCTION))
    assert timers.added == []

    handler.close()
    bus.publish(comms("PigTimer-30", CommsChannel.SAY))
    assert timers.added == []
