"""app._apply_window_command — WindowCommandEvent onto the window handles."""

from __future__ import annotations

from datetime import datetime

from nparseplus.app import _apply_window_command
from nparseplus.core.bus import EventBus
from nparseplus.core.enums import CommsChannel
from nparseplus.core.events import CommsEvent, WindowCommandEvent
from nparseplus.core.triggers.window_commands import WindowChatCommands

T0 = datetime(2026, 7, 14, 12, 0, 0)


class FakeWindow:
    def __init__(self, visible: bool = False) -> None:
        self.visible = visible
        self.toggles = 0

    def isVisible(self) -> bool:
        return self.visible

    def toggle(self) -> None:
        self.visible = not self.visible
        self.toggles += 1


def command(window: str, action: str) -> WindowCommandEvent:
    return WindowCommandEvent(timestamp=T0, line="", line_number=1, window=window, action=action)


def test_toggle_always_flips() -> None:
    window = FakeWindow(visible=False)
    handles = {"maps": window}
    _apply_window_command(command("maps", "toggle"), handles)
    _apply_window_command(command("maps", "toggle"), handles)
    assert window.toggles == 2
    assert window.visible is False


def test_show_and_hide_only_flip_on_state_change() -> None:
    window = FakeWindow(visible=False)
    handles = {"spells": window}
    _apply_window_command(command("spells", "hide"), handles)
    assert window.toggles == 0
    _apply_window_command(command("spells", "show"), handles)
    assert (window.toggles, window.visible) == (1, True)
    _apply_window_command(command("spells", "show"), handles)
    assert window.toggles == 1


def test_missing_window_and_foreign_events_ignored() -> None:
    _apply_window_command(command("maps", "toggle"), {"maps": None})
    _apply_window_command(object(), {"maps": FakeWindow()})


def test_unknown_window_name_is_a_no_op() -> None:
    """#50 widened the parser to any ``\\w+``, so this table is the gate."""
    window = FakeWindow()
    _apply_window_command(command("everything", "toggle"), {"maps": window})
    assert window.toggles == 0


def test_plugin_key_reaches_its_window() -> None:
    """The whole point of #50: a runtime-only key resolves like a built-in."""
    window = FakeWindow()
    _apply_window_command(command("showy_main", "toggle"), {"showy_main": window})
    assert (window.toggles, window.visible) == (1, True)


def test_a_widget_without_the_documented_pair_is_skipped(caplog) -> None:
    """A plugin's widget comes from an arbitrary ``spec.factory``.

    The SDK documents it as "any widget with ``.toggle()``/``.isVisible()``",
    but nothing enforces that, and widening the parser is exactly what first
    makes a third-party widget reachable here. A plugin's bug must not reach
    the GUI thread as an unhandled exception.
    """
    with caplog.at_level("WARNING"):
        _apply_window_command(command("showy_main", "toggle"), {"showy_main": object()})
    assert "showy_main" in caplog.text


def test_a_widget_that_raises_does_not_escape(caplog) -> None:
    class Exploding:
        def isVisible(self) -> bool:
            return False

        def toggle(self) -> None:
            raise RuntimeError("boom")

    with caplog.at_level("ERROR"):
        _apply_window_command(command("showy_main", "toggle"), {"showy_main": Exploding()})
    assert "boom" in caplog.text


def test_a_chat_line_reaches_a_plugin_window_and_the_macro_editor() -> None:
    """#50 end to end: the producer and this consumer, over a real bus.

    ``create_app`` connects this to ``QtEventBridge.event_received``, which
    re-emits bus events verbatim, so subscribing directly is the same chain
    without a Qt event loop in the middle.
    """
    bus = EventBus()
    WindowChatCommands(bus)
    handles: dict[str, object] = {"showy_main": FakeWindow(), "macroeditor": FakeWindow()}
    bus.subscribe(WindowCommandEvent, lambda event: _apply_window_command(event, handles))

    for content in ("toggle_showy_main", "toggle_macroeditor", "toggle_nothing_here"):
        bus.publish(
            CommsEvent(
                timestamp=T0,
                line=f"You say, '{content}'",
                line_number=1,
                channel=CommsChannel.SAY,
                content=content,
                sender="You",
            )
        )

    assert handles["showy_main"].visible is True
    assert handles["macroeditor"].visible is True
