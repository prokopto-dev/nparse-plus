"""A plugin's Qt surfaces follow the plugin, with no restart (#45).

The core half proves the registrations come and go; this proves the *visible*
half does — that toggling the box in Settings > Plugins creates and destroys
the window, the tray entry, the chat command, the layout entry, the skin
sweep membership and the settings page, all of which used to exist only
because ``build_plugin_ui`` ran once at startup.

The seams are the real ones: a real ``UnifiedSettingsWindow``, a real
``WindowLayoutManager``, and stand-ins only for the legacy tray dict (whose
whole contract is "``_build_tray_menu`` re-reads it") and the skin sweep.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QLabel, QWidget

from nparseplus.audio.tts import NullSpeaker
from nparseplus.composition import build_backend
from nparseplus.config.settings import PluginEntry, Settings
from nparseplus.core.plugins.host import PluginHost
from nparseplus.pluginbootstrap import build_plugin_ui
from nparseplus.ui.qtbridge import QtEventBridge
from nparseplus.ui.settingswindow import UnifiedSettingsWindow
from nparseplus.ui.windowlayouts import WindowLayoutManager

APP_VERSION = "1.15.0"

# A plugin with one window AND one settings page — the two surfaces the
# reviewer's scenario turns on.
PLUGIN = """
from nparseplus_sdk import NParsePlugin, PluginMeta, PluginSettingsPageSpec, PluginWindowSpec
from PySide6.QtWidgets import QLabel


# Stands in for a window on the overlay recipe: apply_window_state is the
# duck-type that earns a Settings > Windows row (a bare QWidget gets none,
# deliberately), so only a widget that has it exercises the row path.
class _Window(QLabel):
    def apply_window_state(self, *args, **kwargs):
        pass


class _Plugin(NParsePlugin):
    meta = PluginMeta(id="showy", name="Showy", version="1.0.0")

    def activate(self, ctx):
        ctx.add_window(
            PluginWindowSpec(
                key="main",
                title="Showy Window",
                factory=lambda wctx: _Window("hello"),
            )
        )
        ctx.add_settings_page(
            PluginSettingsPageSpec(title="Showy", builder=lambda parent: QLabel("page"))
        )

    def deactivate(self):
        pass


def create_plugin():
    return _Plugin()
"""


class FakeTray:
    """The legacy Application's tray dict, which is all this seam is."""

    def __init__(self) -> None:
        self.windows: dict[str, object] = {}

    def add_backend_window(self, label: str, window: object) -> None:
        self.windows[label] = window

    def remove_backend_window(self, label: str) -> bool:
        return self.windows.pop(label, None) is not None

    def has_backend_window(self, label: str) -> bool:
        return label in self.windows


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = Settings()
    s.sharing.mode = "off"
    s.mobinfo.wiki_details = False
    s.plugins.enabled = True
    s.plugins.update_check = False  # no post-launch QTimer in a test
    s.plugins.entries["showy"] = PluginEntry(enabled=True, approved=True)
    return s


@pytest.fixture
def plugins_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "plugins"
    directory.mkdir()
    (directory / "showy.py").write_text(PLUGIN, encoding="utf-8")
    return directory


@pytest.fixture
def wired(qtbot, settings: Settings, plugins_dir: Path):
    """create_app's assembly, reduced to the parts #45 has to keep in step."""
    backend = build_backend(settings, speaker=NullSpeaker())
    host = PluginHost(
        settings,
        backend,
        APP_VERSION,
        request_save=lambda: None,
        plugins_dir_override=plugins_dir,
    )
    host.discover_and_load()
    bridge = QtEventBridge(backend.bus)
    window_handles: dict[str, object] = {"spells": QWidget()}
    ui = build_plugin_ui(host, settings, APP_VERSION, lambda: None, bridge, window_handles)
    window_handles.update(ui.command_handles)

    settings_window = UnifiedSettingsWindow(
        settings,
        on_save=lambda: None,
        window_handles=window_handles,
        plugin_windows=ui.window_rows,
        extra_pages=ui.extra_pages,
    )
    qtbot.addWidget(settings_window)
    layouts = WindowLayoutManager(
        settings,
        {"spells": window_handles["spells"], **ui.windows_by_key},
        on_save=lambda: None,
    )
    tray = FakeTray()
    # Deliberately NOT seeded with ui.tray: create_app stopped merging plugin
    # entries into the tray literal, because that dict is last-write-wins and
    # a plugin titling its window "Settings" would replace the app's own.
    # attach_live claims the labels against the tray below.
    chrome_surfaces: list[object] = list(ui.windows_by_key.values())
    dressed: list[int] = []
    ui.attach_live(
        plugin_host=host,
        settings=settings,
        save=lambda: None,
        bridge=bridge,
        window_handles=window_handles,
        settings_window=settings_window,
        layouts=layouts,
        legacy_app=tray,
        chrome_surfaces=chrome_surfaces,
        apply_appearance=lambda: dressed.append(1),
    )
    yield {
        "host": host,
        "ui": ui,
        "settings_window": settings_window,
        "layouts": layouts,
        "tray": tray,
        "window_handles": window_handles,
        "chrome_surfaces": chrome_surfaces,
        "dressed": dressed,
    }
    backend.stop()


WINDOW_KEY = "plugin.showy.main"
COMMAND_KEY = "showy_main"
TRAY_LABEL = "Showy Window"


def page_titles(window: UnifiedSettingsWindow) -> list[str]:
    return [window._sidebar.item(row).text() for row in range(window._sidebar.count())]


def test_startup_puts_the_plugin_in_every_collection(wired) -> None:
    """The baseline the live path has to reproduce and then reverse."""
    assert WINDOW_KEY in wired["ui"].windows_by_key
    assert COMMAND_KEY in wired["window_handles"]
    assert TRAY_LABEL in wired["tray"].windows
    assert "Showy" in page_titles(wired["settings_window"])


def test_disabling_a_plugin_takes_its_ui_off_the_screen(wired) -> None:
    host, ui = wired["host"], wired["ui"]
    widget = ui.windows_by_key[WINDOW_KEY]

    host.set_enabled("showy", False)

    assert WINDOW_KEY not in ui.windows_by_key
    assert COMMAND_KEY not in wired["window_handles"]
    assert TRAY_LABEL not in wired["tray"].windows
    assert widget not in wired["chrome_surfaces"]
    assert wired["layouts"].remove_window(WINDOW_KEY) is False  # already gone
    assert "Showy" not in page_titles(wired["settings_window"])
    assert not ui.window_rows
    assert widget.isHidden()


def test_enabling_a_plugin_builds_its_ui_again(wired) -> None:
    host, ui = wired["host"], wired["ui"]
    host.set_enabled("showy", False)
    wired["dressed"].clear()

    host.set_enabled("showy", True)

    assert WINDOW_KEY in ui.windows_by_key
    assert COMMAND_KEY in wired["window_handles"]
    assert TRAY_LABEL in wired["tray"].windows
    assert ui.windows_by_key[WINDOW_KEY] in wired["chrome_surfaces"]
    # A window built after launch has never been dressed, so the skin sweep
    # has to run for it — otherwise it wears Qt's defaults until the next
    # skin change.
    assert wired["dressed"]
    assert "Showy" in page_titles(wired["settings_window"])
    assert [key for _label, key, _widget in ui.window_rows] == [WINDOW_KEY]
    # A fresh widget, not the destroyed one.
    assert ui.windows_by_key[WINDOW_KEY] is not None
    assert isinstance(ui.windows_by_key[WINDOW_KEY], QLabel)


def test_the_settings_sidebar_and_stack_stay_in_step(wired) -> None:
    """Removing a page must take its sidebar row AND its stack widget.

    They are addressed by the same index (``currentRowChanged`` feeds
    ``setCurrentIndex``), so dropping one without the other silently shows
    the wrong page for everything after it.
    """
    window = wired["settings_window"]
    wired["host"].set_enabled("showy", False)
    for row in range(window._sidebar.count()):
        window._sidebar.setCurrentRow(row)
        assert window._stack.currentIndex() == row
    assert window._sidebar.count() == window._stack.count()

    wired["host"].set_enabled("showy", True)
    assert window._sidebar.count() == window._stack.count()
    assert page_titles(window)[-1] == "Showy"


def test_a_toggle_cycle_leaves_no_duplicates(wired) -> None:
    host, ui = wired["host"], wired["ui"]
    window = wired["settings_window"]
    for _ in range(3):
        host.set_enabled("showy", False)
        host.set_enabled("showy", True)
    assert list(ui.windows_by_key) == [WINDOW_KEY]
    assert len(ui.window_rows) == 1
    assert page_titles(window).count("Showy") == 1
    assert len(wired["tray"].windows) == 1
    assert [w for w in wired["chrome_surfaces"]] == [ui.windows_by_key[WINDOW_KEY]]


def test_a_plugin_cannot_take_over_a_core_tray_entry(qtbot, settings, tmp_path) -> None:
    """A plugin names its own window, and the tray dict is last-write-wins.

    A window titled "Settings" would replace the app's own entry until the
    add-on was disabled — so the label is claimed against the tray itself,
    and disambiguated with the plugin id, which is also the answer to "whose
    window is this?".
    """
    plugins_dir = tmp_path / "greedy-plugins"
    plugins_dir.mkdir()
    (plugins_dir / "greedy.py").write_text(
        PLUGIN.replace('id="showy"', 'id="greedy"').replace('"Showy Window"', '"Settings"'),
        encoding="utf-8",
    )
    settings.plugins.entries["greedy"] = PluginEntry(enabled=True, approved=True)
    backend = build_backend(settings, speaker=NullSpeaker())
    host = PluginHost(
        settings, backend, APP_VERSION, request_save=lambda: None, plugins_dir_override=plugins_dir
    )
    host.discover_and_load()
    bridge = QtEventBridge(backend.bus)
    handles: dict[str, object] = {}
    ui = build_plugin_ui(host, settings, APP_VERSION, lambda: None, bridge, handles)
    settings_window = UnifiedSettingsWindow(settings, on_save=lambda: None)
    qtbot.addWidget(settings_window)
    tray = FakeTray()
    core = object()
    tray.windows["Settings"] = core  # the app's own entry, already there
    ui.attach_live(
        plugin_host=host,
        settings=settings,
        save=lambda: None,
        bridge=bridge,
        window_handles=handles,
        settings_window=settings_window,
        layouts=WindowLayoutManager(settings, {}, on_save=lambda: None),
        legacy_app=tray,
        chrome_surfaces=[],
        apply_appearance=lambda: None,
    )
    try:
        assert tray.windows["Settings"] is core  # untouched
        assert "Settings (greedy)" in tray.windows
        # ...and disabling the plugin takes only its own entry.
        host.set_enabled("greedy", False)
        assert tray.windows == {"Settings": core}
    finally:
        backend.stop()


def test_uninstalling_a_running_plugin_takes_its_ui_with_it(wired) -> None:
    """``forget`` deactivates first, so the same teardown runs (#45).

    An uninstall can now happen while the plugin is on screen; leaving the
    window behind would be the same orphan a disable used to leave.
    """
    host, ui = wired["host"], wired["ui"]
    host.forget("showy")

    assert [p.plugin_id for p in host.statuses()] == []
    assert WINDOW_KEY not in ui.windows_by_key
    assert TRAY_LABEL not in wired["tray"].windows
    assert "Showy" not in page_titles(wired["settings_window"])
