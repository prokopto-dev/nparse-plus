"""PluginWindow: persistence into Settings.windows + layout participation."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QLabel, QVBoxLayout

from nparseplus.config.settings import Settings
from nparseplus.ui.pluginwindow import PluginWindow
from nparseplus.ui.windowlayouts import WindowLayoutManager
from nparseplus_sdk.plugin import PluginWindowContext

pytestmark = pytest.mark.qt

WINDOW_KEY = "plugin.demo-plugin.main"


class DemoWindow(PluginWindow):
    def __init__(self, wctx: PluginWindowContext) -> None:
        super().__init__(wctx)
        layout = QVBoxLayout()
        layout.addWidget(QLabel("demo content", self))
        self.setLayout(layout)
        self.restore_visibility()


def make_window(qtbot, settings: Settings, saves: list[None] | None = None) -> DemoWindow:
    record = saves if saves is not None else []
    wctx = PluginWindowContext(
        settings=settings,
        window_key=WINDOW_KEY,
        title="Demo Plugin",
        default_geometry=(120, 130, 300, 200),
        on_save=lambda: record.append(None),
    )
    window = DemoWindow(wctx)
    qtbot.addWidget(window)
    return window


def test_state_entry_created_and_geometry_restored(qtbot) -> None:
    settings = Settings()
    window = make_window(qtbot, settings)
    assert WINDOW_KEY in settings.windows
    assert window.windowTitle() == "Demo Plugin"
    geometry = window.geometry()
    assert (geometry.width(), geometry.height()) == (300, 200)


def test_toggle_persists_shown_flag(qtbot) -> None:
    settings = Settings()
    saves: list[None] = []
    window = make_window(qtbot, settings, saves)
    assert not settings.windows[WINDOW_KEY].shown
    window.toggle()
    assert window.isVisible()
    assert settings.windows[WINDOW_KEY].shown is True
    window.toggle()
    assert settings.windows[WINDOW_KEY].shown is False
    assert saves


def test_geometry_persists_on_state_save(qtbot) -> None:
    settings = Settings()
    window = make_window(qtbot, settings)
    window.setGeometry(400, 410, 333, 222)
    window.persist_state()
    assert settings.windows[WINDOW_KEY].geometry == (400, 410, 333, 222)


def test_participates_in_window_layouts(qtbot) -> None:
    settings = Settings()
    window = make_window(qtbot, settings)
    window.setGeometry(150, 160, 280, 190)
    manager = WindowLayoutManager(settings, {WINDOW_KEY: window}, on_save=lambda: None)
    manager.save_layout("raid")
    assert settings.window_layouts["raid"].geometries[WINDOW_KEY] == (150, 160, 280, 190)
    window.setGeometry(10, 20, 300, 200)
    manager.apply_layout("raid")
    geometry = window.geometry()
    assert (geometry.x(), geometry.y()) == (150, 160)
