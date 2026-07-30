"""The tray must not mention add-ons to a user who never opted in."""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from nparseplus.helpers.application import NomnsParse

pytestmark = pytest.mark.qt


def _stub(*, plugins_enabled: bool) -> SimpleNamespace:
    # Drive the real method over a stub — constructing NomnsParse would build
    # every legacy window. Same trick as tests/ui/test_parserwindow_state.py.
    return SimpleNamespace(
        _available_release=None,
        _backend=SimpleNamespace(sharing=None),
        _backend_windows={},
        _parsers=[],
        _spell_window=None,
        _window_layouts=None,
        _plugins_enabled=plugins_enabled,
    )


def _labels(menu) -> list[str]:
    return [action.text() for action in menu.actions()]


def test_tray_hides_the_plugins_folder_when_plugins_are_off(qtbot) -> None:
    menu, actions = NomnsParse._build_tray_menu(_stub(plugins_enabled=False))
    assert actions["open_plugins"] is None
    assert "Open Plugins Folder" not in _labels(menu)
    assert not any("lugin" in label for label in _labels(menu))


def test_tray_shows_the_plugins_folder_when_plugins_are_on(qtbot) -> None:
    menu, actions = NomnsParse._build_tray_menu(_stub(plugins_enabled=True))
    assert actions["open_plugins"] is not None
    assert "Open Plugins Folder" in _labels(menu)


def test_the_plugins_dispatch_arm_is_null_guarded() -> None:
    """Dismissing the menu must not open the plugins folder.

    `_menu` compares the chosen action with `==`, and dismissing yields None.
    Without the `is not None` guard, None == None matches the disabled entry
    and a user who has never heard of plugins gets a folder opened at them.
    """
    source = inspect.getsource(NomnsParse._menu)
    assert 'actions["open_plugins"] is not None and action == actions["open_plugins"]' in source
