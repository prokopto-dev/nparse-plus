"""WindowChatCommands — show_/hide_/toggle_<window> from self-sent chat."""

from __future__ import annotations

from datetime import datetime

import pytest

from nparseplus.core.bus import EventBus
from nparseplus.core.enums import CommsChannel
from nparseplus.core.events import CommsEvent, WindowCommandEvent
from nparseplus.core.triggers.window_commands import _COMMAND_RE, WindowChatCommands

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
        # #50: the Macro Editor and plugin windows were unreachable while the
        # window name was an alternation over a frozen tuple.
        ("toggle_macroeditor", "macroeditor", "toggle"),
        ("hide_showy_main", "showy_main", "hide"),
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
    say(bus, "PigTimer-30")
    say(bus, "toggle_")
    say(bus, "toggle_maps-window")
    assert commands == []


def test_unknown_names_are_published_for_the_consumer_to_resolve(
    bus: EventBus, commands: list[WindowCommandEvent]
) -> None:
    """#50: the live handle table decides what exists, not this regex.

    A name nothing answers to reaches ``app._apply_window_command`` and is a
    no-op there, which is the price of letting runtime-only names (plugin
    command keys) through at all.
    """
    say(bus, "toggle_everything")
    assert [(e.window, e.action) for e in commands] == [("everything", "toggle")]


def test_generated_plugin_command_keys_match_this_pattern() -> None:
    """The producer regex and ``_plugin_command_key`` must stay in agreement.

    ``pluginbootstrap`` sanitizes a plugin's command key with
    ``re.sub(r"\\W", "_", ...)``, i.e. into exactly the ``\\w+`` this matches.
    Nothing else connects the two, so narrowing either one silently makes a
    documented chat toggle inert again — which is what #50 was.
    """
    from nparseplus.pluginbootstrap import _plugin_command_key

    class _Meta:
        id = "my.plugin-id"

    class _Loaded:
        meta = _Meta()

    for spec_key, declared in [
        ("main", None),
        ("side panel", None),
        ("main", "Loot Helper!"),
        ("main", "sp\u00e4ter"),
    ]:
        key = _plugin_command_key(_Loaded(), spec_key, declared)
        match = _COMMAND_RE.match(f"toggle_{key}")
        assert match is not None, key
        assert match.group("window") == key
