"""How the tray menu is grouped.

Two things: an add-on must not be mentioned to a user who never opted in, and
the entries that *are* shown are fenced into blocks — Settings pinned to the
top group, plugin windows below their own separator (nparseplus #173).
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from nparseplus.helpers.application import NomnsParse

pytestmark = pytest.mark.qt


class _Window:
    """The two things _build_tray_menu asks a tray window for."""

    def __init__(self, visible: bool = False) -> None:
        self.visible = visible
        self.toggled = 0

    def isVisible(self) -> bool:
        return self.visible

    def toggle(self) -> None:
        self.toggled += 1


def _stub(
    *,
    plugins_enabled: bool,
    windows: dict[str, object] | None = None,
    plugin_labels: set[str] | None = None,
    skins: bool = False,
) -> SimpleNamespace:
    # Drive the real method over a stub — constructing NomnsParse would build
    # every legacy window. Same trick as tests/ui/test_parserwindow_state.py.
    return SimpleNamespace(
        _available_release=None,
        _backend=SimpleNamespace(
            sharing=None,
            settings=SimpleNamespace(general=SimpleNamespace(skin="duxa")),
        ),
        _backend_windows=dict(windows or {}),
        _plugin_window_labels=set(plugin_labels or ()),
        _parsers=[],
        _spell_window=None,
        _window_layouts=None,
        _plugins_enabled=plugins_enabled,
        # The UI Skin submenu is what follows the window block in the real
        # menu, so a test asserting where that block ENDS has to have it.
        _on_skin_changed=(lambda _name: None) if skins else None,
        _open_settings=None,
    )


def _labels(menu) -> list[str]:
    return [action.text() for action in menu.actions()]


def _rows(menu) -> list[str]:
    """Menu rows with separators made visible, so blocks can be asserted."""
    return ["---" if action.isSeparator() else action.text() for action in menu.actions()]


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


def test_settings_is_pinned_to_the_top_group_as_a_plain_action(qtbot) -> None:
    """It is the primary configuration surface, not one more window toggle."""
    settings = _Window()
    menu, actions = NomnsParse._build_tray_menu(
        _stub(plugins_enabled=False, windows={"Settings": settings, "Console": _Window()})
    )
    rows = _rows(menu)

    assert rows.index("Settings") < rows.index("---")
    assert rows.index("Settings") < rows.index("Select EQ Logs Directory")
    assert rows.count("Settings") == 1

    settings_action = next(a for a in menu.actions() if a.text() == "Settings")
    assert not settings_action.isCheckable()
    # Still dispatched by _menu's existing backend_windows arm — no new branch.
    assert actions["backend_windows"][settings_action] is settings


def test_settings_stays_in_the_backend_windows_dict(qtbot) -> None:
    """Only the rendering moved.

    has_backend_window is the collision guard that stops a plugin titling its
    own window "Settings" and replacing the app's entry, and it answers from
    that dict.
    """
    stub = _stub(plugins_enabled=False, windows={"Settings": _Window()})
    NomnsParse._build_tray_menu(stub)
    assert NomnsParse.has_backend_window(stub, "Settings")


def test_a_plugin_window_titled_settings_renders_below_the_divider(qtbot) -> None:
    """_free_tray_label suffixed it, so both entries coexist."""
    menu, _actions = NomnsParse._build_tray_menu(
        _stub(
            plugins_enabled=True,
            windows={"Settings": _Window(), "Settings (showy)": _Window()},
            plugin_labels={"Settings (showy)"},
        )
    )
    rows = _rows(menu)

    assert rows.index("Settings") < rows.index("Settings (showy)")
    assert "---" in rows[rows.index("Settings") + 1 : rows.index("Settings (showy)")]
    assert not next(a for a in menu.actions() if a.text() == "Settings").isCheckable()
    assert next(a for a in menu.actions() if a.text() == "Settings (showy)").isCheckable()


def test_a_separator_fences_plugin_windows_off_from_core_ones(qtbot) -> None:
    menu, _actions = NomnsParse._build_tray_menu(
        _stub(
            plugins_enabled=True,
            windows={
                "Console": _Window(),
                "Character Dumps": _Window(),
                "Showy": _Window(),
            },
            plugin_labels={"Showy"},
        )
    )
    rows = _rows(menu)

    assert rows[rows.index("Character Dumps") + 1] == "---"
    assert rows[rows.index("Character Dumps") + 2] == "Showy"
    # The folder those add-ons live in belongs to the same block.
    assert rows[rows.index("Showy") + 1] == "Open Plugins Folder"


def test_the_plugin_block_is_not_inferred_from_dict_order(qtbot) -> None:
    """A core window registered late must still land above the divider."""
    menu, _actions = NomnsParse._build_tray_menu(
        _stub(
            plugins_enabled=True,
            windows={"Showy": _Window(), "Console": _Window()},
            plugin_labels={"Showy"},
        )
    )
    rows = _rows(menu)
    assert rows.index("Console") < rows.index("Showy")
    assert "---" in rows[rows.index("Console") + 1 : rows.index("Showy")]


def test_no_plugin_separator_when_no_add_on_is_installed(qtbot) -> None:
    """A dangling separator is a style-dependent artifact, not Qt's to collapse."""
    windows = {"Console": _Window(), "Character Dumps": _Window()}
    plain = _rows(
        NomnsParse._build_tray_menu(_stub(plugins_enabled=False, windows=windows, skins=True))[0]
    )
    with_folder = _rows(
        NomnsParse._build_tray_menu(_stub(plugins_enabled=True, windows=windows, skins=True))[0]
    )

    # Nothing between the last core window and the skin block.
    assert plain[plain.index("Character Dumps") + 1] == "---"
    assert plain[plain.index("Character Dumps") + 2] == "UI Skin"
    assert plain.count("---") == with_folder.count("---") - 1
