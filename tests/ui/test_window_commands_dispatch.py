"""app._apply_window_command — WindowCommandEvent onto the window handles."""

from __future__ import annotations

from datetime import datetime

from nparseplus.app import _apply_window_command
from nparseplus.core.events import WindowCommandEvent

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
