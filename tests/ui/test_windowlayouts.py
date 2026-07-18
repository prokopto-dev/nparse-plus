"""Named window-layout storage, application, and tray-menu tests."""

import pytest
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from nparseplus.config.settings import Settings, WindowLayoutPreset, WindowState
from nparseplus.ui.windowlayouts import WindowLayoutManager, clamp_rect_to_screen

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
        "Reset Window Positions",
        "Laptop mode",
    ]
    layout_menu = menu.actions()[-1].menu()
    assert [action.text() for action in layout_menu.actions() if not action.isSeparator()] == [
        "Apply",
        "Replace with Current Layout",
        "Rename…",
        "Delete…",
    ]


def test_clamp_rect_to_screen_leaves_onscreen_rect_untouched() -> None:
    screen = (0, 0, 1000, 800)
    assert clamp_rect_to_screen((100, 50, 300, 200), screen) == (100, 50, 300, 200)


def test_clamp_rect_to_screen_pulls_offscreen_rects_fully_inside() -> None:
    screen = (0, 0, 1000, 800)
    assert clamp_rect_to_screen((-500, 50, 300, 200), screen) == (0, 50, 300, 200)
    assert clamp_rect_to_screen((100, -500, 300, 200), screen) == (100, 0, 300, 200)
    assert clamp_rect_to_screen((900, 50, 300, 200), screen) == (700, 50, 300, 200)
    assert clamp_rect_to_screen((100, 700, 300, 200), screen) == (100, 600, 300, 200)


def test_clamp_rect_to_screen_shrinks_and_pins_oversized_rect() -> None:
    screen = (10, 20, 1000, 800)
    assert clamp_rect_to_screen((-50, -50, 5000, 4000), screen) == (10, 20, 1000, 800)


def test_reset_onscreen_clamps_shrinks_persists_and_preserves_visibility(qtbot) -> None:
    manager, settings, legacy, maps, spells, overlay, saves, notices = _manager(qtbot)
    overlay.setGeometry(50, 60, 10000, 8000)
    available = QApplication.primaryScreen().availableGeometry()
    sx, sy, sw, sh = available.x(), available.y(), available.width(), available.height()

    manager.reset_onscreen()

    # The off-screen -1200 maps window is brought fully within the screen.
    maps_rect = maps.geometry().getRect()
    mx, my, mw, mh = maps_rect
    assert mx >= sx and my >= sy
    assert mx + mw <= sx + sw and my + mh <= sy + sh
    # A legacy window writes its clamped geometry back to the legacy config.
    assert legacy["maps"]["geometry"] == list(maps_rect)

    # The oversized overlay is shrunk to fit the screen.
    ox, oy, ow, oh = overlay.geometry().getRect()
    assert ow == sw and oh == sh
    assert ox >= sx and oy >= sy

    # A non-legacy window updates settings.windows with the clamped tuple.
    assert settings.windows["spells"].geometry == spells.geometry().getRect()

    # Persistence callbacks fired (legacy + new) and a notice was sent.
    assert "legacy" in saves
    assert saves[-1] == "new"
    assert notices == ["Window positions reset."]

    # Hidden windows are never shown by the reset.
    assert not overlay.isVisible()
    assert not maps.isVisible()
