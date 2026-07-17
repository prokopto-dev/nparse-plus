"""Named window-layout storage, application, and tray-menu tests."""

import pytest
from PySide6.QtWidgets import QMenu, QWidget

from nparseplus.config.settings import Settings, WindowLayoutPreset, WindowState
from nparseplus.ui.windowlayouts import WindowLayoutManager

pytestmark = pytest.mark.qt


def _manager(qtbot, *, settings=None, legacy=None):
    settings = settings or Settings()
    legacy = legacy or {"maps": {}, "discord": {}}
    maps = QWidget()
    spells = QWidget()
    overlay = QWidget()
    for widget in (maps, spells, overlay):
        qtbot.addWidget(widget)
    maps.setGeometry(-1200, 20, 700, 500)
    spells.setGeometry(10, 30, 220, 400)
    overlay.setGeometry(200, 100, 800, 600)
    saves: list[str] = []
    notices: list[str] = []
    manager = WindowLayoutManager(
        settings,
        {"maps": maps, "spells": spells, "overlay": overlay},
        on_save=lambda: saves.append("new"),
        legacy_config=legacy,
        on_legacy_save=lambda: saves.append("legacy"),
        notify=notices.append,
    )
    return manager, settings, legacy, maps, spells, overlay, saves, notices


def test_save_and_apply_layout_updates_both_settings_stores(qtbot) -> None:
    manager, settings, legacy, maps, spells, overlay, saves, notices = _manager(qtbot)
    manager.save_layout("Laptop mode")
    assert settings.window_layouts["Laptop mode"].geometries == {
        "maps": (-1200, 20, 700, 500),
        "spells": (10, 30, 220, 400),
        "overlay": (200, 100, 800, 600),
    }

    maps.setGeometry(0, 0, 100, 100)
    spells.setGeometry(0, 0, 100, 100)
    overlay.setGeometry(0, 0, 100, 100)
    manager.apply_layout("laptop MODE")

    assert maps.geometry().getRect() == (-1200, 20, 700, 500)
    assert spells.geometry().getRect() == (10, 30, 220, 400)
    assert overlay.geometry().getRect() == (200, 100, 800, 600)
    assert legacy["maps"]["geometry"] == [-1200, 20, 700, 500]
    assert settings.windows["spells"].geometry == (10, 30, 220, 400)
    assert settings.windows["overlay"].geometry == (200, 100, 800, 600)
    assert saves == ["new", "legacy", "new"]
    assert notices == ['Applied "Laptop mode".']


def test_overwrite_rename_delete_and_case_insensitive_uniqueness(qtbot) -> None:
    manager, settings, _legacy, _maps, spells, _overlay, saves, _notices = _manager(qtbot)
    manager.save_layout("Desktop mode")
    with pytest.raises(ValueError, match="already exists"):
        manager.save_layout("desktop MODE")

    spells.setGeometry(50, 60, 300, 500)
    assert manager.save_layout("desktop mode", overwrite=True) == "Desktop mode"
    assert settings.window_layouts["Desktop mode"].geometries["spells"] == (50, 60, 300, 500)
    assert manager.rename_layout("DESKTOP MODE", "External monitor") == "External monitor"
    assert manager.names == ["External monitor"]
    manager.delete_layout("external MONITOR")
    assert manager.names == []
    assert saves == ["new", "new", "new", "new"]


def test_apply_ignores_windows_not_present_in_this_app_version(qtbot) -> None:
    settings = Settings(
        window_layouts={
            "Future": WindowLayoutPreset(
                geometries={"spells": (1, 2, 300, 400), "future_window": (5, 6, 7, 8)}
            )
        },
        windows={"spells": WindowState()},
    )
    manager, settings, _legacy, _maps, spells, _overlay, _saves, _notices = _manager(
        qtbot, settings=settings
    )
    manager.apply_layout("Future")
    assert spells.geometry().getRect() == (1, 2, 300, 400)
    assert "future_window" not in settings.windows


def test_tray_submenu_lists_saved_layout_management_actions(qtbot) -> None:
    manager, settings, *_rest = _manager(qtbot)
    settings.window_layouts["Laptop mode"] = WindowLayoutPreset()
    parent = QMenu()
    qtbot.addWidget(parent)
    menu = manager.populate_menu(parent)
    assert menu.title() == "Window Layouts"
    assert [action.text() for action in menu.actions() if not action.isSeparator()] == [
        "Save Current Layout…",
        "Laptop mode",
    ]
    layout_menu = menu.actions()[-1].menu()
    assert [action.text() for action in layout_menu.actions() if not action.isSeparator()] == [
        "Apply",
        "Replace with Current Layout",
        "Rename…",
        "Delete…",
    ]
