"""An add-on written against an older SDK is untouched by a newer one (#155).

SDK 1.x is **additive-only**, and the expensive way to learn that is the way
#166 did: the 1.4 skin façade shipped, and two follow-up fixes
(``83b284ed``, ``2b4c1a8e``) were both backward-compatibility breaks against
windows built on the older contract — a base class that *replaced* a
stylesheet a pre-1.4 plugin had set by hand, and a first dress that never ran.

So this file is deliberately adversarial about the SDK 1.5 release rather than
about the region feature: the plugin here declares ``requires_sdk=">=1.0,<2"``,
uses ``add_window`` and ``add_settings_page`` and nothing else, sets its own
stylesheet the pre-1.4 way, and **does not know regions exist**. Every
assertion is about it being unchanged.

The region tests live next door; nothing here contributes a region on purpose.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QWidget

import nparseplus_sdk
from nparseplus.audio.tts import NullSpeaker
from nparseplus.composition import build_backend
from nparseplus.config.settings import OverlayRegion, PluginEntry, Settings, WindowState
from nparseplus.core.plugins.host import PluginHost
from nparseplus.pluginbootstrap import build_plugin_ui
from nparseplus.ui.eventoverlay import EventOverlayWindow
from nparseplus.ui.qtbridge import QtEventBridge
from nparseplus_sdk.compat import check_compat

pytestmark = pytest.mark.qt

APP_VERSION = "2.27.0"
WINDOW_KEY = "plugin.oldtimer.main"
OWN_SHEET = "#OldTimerLabel { color: #ff00ff; }"

# Written as if SDK 1.5 does not exist: a 1.0 range, no region import, and a
# window that styles itself with setStyleSheet because before 1.4 there was no
# hook to be called by.
OLD_PLUGIN = f"""
from nparseplus_sdk import (
    NParsePlugin,
    PluginMeta,
    PluginSettingsPageSpec,
    PluginWindowSpec,
)


def _make_window(wctx):
    from PySide6.QtWidgets import QLabel, QVBoxLayout

    from nparseplus_sdk.ui import PluginWindow

    class OldWindow(PluginWindow):
        def __init__(self, ctx):
            super().__init__(ctx)
            label = QLabel("hello", self)
            label.setObjectName("OldTimerLabel")
            layout = QVBoxLayout()
            layout.addWidget(label)
            self.setLayout(layout)
            # The pre-1.4 pattern: own the sheet by hand. #166 broke exactly
            # this, twice.
            self.setStyleSheet({OWN_SHEET!r})
            self.restore_visibility()

    return OldWindow(wctx)


def _make_page(parent):
    from PySide6.QtWidgets import QLabel

    return QLabel("page", parent)


class _Plugin(NParsePlugin):
    meta = PluginMeta(
        id="oldtimer", name="Old Timer", version="1.0.0", requires_sdk=">=1.0,<2"
    )

    def activate(self, ctx):
        ctx.add_window(
            PluginWindowSpec(key="main", title="Old Timer", factory=_make_window)
        )
        ctx.add_settings_page(
            PluginSettingsPageSpec(title="Old Timer", builder=_make_page)
        )

    def deactivate(self):
        pass


def create_plugin():
    return _Plugin()
"""


class FakeTray:
    def __init__(self) -> None:
        self.windows: dict[str, object] = {}

    def add_backend_window(self, label: str, window: object, **kwargs) -> None:
        self.windows[label] = window

    def remove_backend_window(self, label: str) -> bool:
        return self.windows.pop(label, None) is not None

    def has_backend_window(self, label: str) -> bool:
        return label in self.windows


class FakeSettingsWindow:
    def __init__(self) -> None:
        self.pages: list[object] = []
        self.rows: list[tuple] = []

    def add_page(self, spec) -> None:
        self.pages.append(spec)

    def remove_page(self, spec) -> None:
        if spec in self.pages:
            self.pages.remove(spec)

    def set_plugin_window_rows(self, rows) -> None:
        self.rows = list(rows)


class FakeLayouts:
    def __init__(self) -> None:
        self.windows: dict[str, object] = {}

    def add_window(self, key: str, widget: object) -> None:
        self.windows[key] = widget

    def remove_window(self, key: str) -> bool:
        return self.windows.pop(key, None) is not None


def _overlay_state() -> WindowState:
    return WindowState(
        geometry=(0, 0, 1000, 800),
        overlay_regions={
            "lanes": OverlayRegion(anchor="top"),
            "utility": OverlayRegion(anchor="top", dy=96),
            "alert": OverlayRegion(anchor="center"),
            "bars": OverlayRegion(anchor="bottom"),
        },
    )


@pytest.fixture
def wired(qtbot, tmp_path: Path):
    directory = tmp_path / "plugins"
    directory.mkdir()
    (directory / "oldtimer.py").write_text(OLD_PLUGIN, encoding="utf-8")

    settings = Settings()
    settings.sharing.mode = "off"
    settings.mobinfo.wiki_details = False
    settings.plugins.enabled = True
    settings.plugins.update_check = False
    settings.plugins.entries["oldtimer"] = PluginEntry(enabled=True, approved=True)

    backend = build_backend(settings, speaker=NullSpeaker())
    host = PluginHost(
        settings,
        backend,
        APP_VERSION,
        request_save=lambda: None,
        plugins_dir_override=directory,
    )
    host.discover_and_load()
    bridge = QtEventBridge(backend.bus)
    overlay = EventOverlayWindow(state=_overlay_state())
    qtbot.addWidget(overlay)
    window_handles: dict[str, object] = {}
    ui = build_plugin_ui(host, settings, APP_VERSION, lambda: None, bridge, window_handles, overlay)
    # create_app merges the startup command table by hand, outside
    # build_plugin_ui; the live path then keeps that merge true.
    window_handles.update(ui.command_handles)
    settings_window = FakeSettingsWindow()
    layouts = FakeLayouts()
    tray = FakeTray()
    chrome_surfaces: list[object] = list(ui.windows_by_key.values())
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
        apply_appearance=lambda: None,
        event_overlay=overlay,
    )
    yield {
        "host": host,
        "ui": ui,
        "overlay": overlay,
        "tray": tray,
        "layouts": layouts,
        "settings_window": settings_window,
        "window_handles": window_handles,
        "chrome_surfaces": chrome_surfaces,
    }
    backend.stop()


# -- the contract --------------------------------------------------------------


@pytest.mark.parametrize("declared", [">=1.0,<2", ">=1.1,<2", ">=1.2,<2", ">=1.3,<2", ">=1.4,<2"])
def test_every_older_range_still_admits_this_sdk(declared: str) -> None:
    """Additive-only means a minor bump refuses nobody it used to admit."""
    from nparseplus_sdk import PluginMeta

    meta = PluginMeta(id="oldtimer", name="Old Timer", requires_sdk=declared)

    assert check_compat(meta, sdk_version=nparseplus_sdk.__version__) is None


def test_the_context_protocol_is_not_runtime_checkable() -> None:
    """Adding ``add_overlay_region`` to ``PluginContext`` would otherwise break
    every ``isinstance(ctx, PluginContext)`` against a fake that predates it."""
    from nparseplus_sdk.context import PluginContext

    assert getattr(PluginContext, "_is_runtime_protocol", False) is False


def test_a_pre_1_5_fake_context_is_unchanged_except_by_addition() -> None:
    from nparseplus_sdk.testing import FakePluginContext

    ctx = FakePluginContext()

    # Everything a plugin's existing unit test reads is still there and empty.
    assert (ctx.windows, ctx.settings_pages, ctx.parsers, ctx.ticks) == ([], [], [], [])
    assert ctx.overlay_regions == []


# -- the plugin, end to end ----------------------------------------------------


def test_it_activates_and_declares_no_regions(wired) -> None:
    host = wired["host"]

    (loaded,) = [row for row in host.statuses() if row.plugin_id == "oldtimer"]
    assert loaded.status == "active"
    assert [spec.key for spec in loaded.window_specs] == ["main"]
    assert loaded.overlay_region_specs == []
    assert host.overlay_region_specs() == []


def test_its_window_joins_every_collection_it_always_did(wired) -> None:
    ui = wired["ui"]

    assert WINDOW_KEY in ui.windows_by_key
    assert "oldtimer_main" in wired["window_handles"]
    assert "Old Timer" in wired["tray"].windows
    assert [key for _label, key, _widget in ui.window_rows] == [WINDOW_KEY]
    # Its settings page. At launch it is only in ``extra_pages`` — create_app
    # builds the settings window FROM that list, so nothing calls add_page
    # until the live path does.
    assert any(getattr(spec, "title", None) == "Old Timer" for spec in ui.extra_pages)


def test_the_overlay_gains_nothing(wired) -> None:
    """A plugin that knows nothing about regions must leave the overlay with
    exactly its four built-ins, and the persisted layout untouched."""
    overlay = wired["overlay"]

    assert list(overlay._region_hosts()) == ["lanes", "utility", "alert", "bars"]
    assert wired["ui"].regions_by_key == {}
    assert set(overlay._state.overlay_regions) == {"lanes", "utility", "alert", "bars"}


def test_its_hand_set_stylesheet_survives_a_skin_change(wired) -> None:
    """THE #166 regression, re-asserted from the other side of an SDK bump:
    ``PluginWindow`` adopts a sheet it did not write and re-applies it after
    its own rules, so a pre-1.4 window is dressed rather than undressed."""
    window = wired["ui"].windows_by_key[WINDOW_KEY]
    assert OWN_SHEET in window.styleSheet()

    window.apply_skin()
    window.apply_skin()

    assert OWN_SHEET in window.styleSheet()
    # And it is not accumulating a copy per change.
    assert window.styleSheet().count(OWN_SHEET) == 1


def test_disabling_and_re_enabling_it_is_unchanged(wired) -> None:
    host, ui, overlay = wired["host"], wired["ui"], wired["overlay"]
    widget = ui.windows_by_key[WINDOW_KEY]

    host.set_enabled("oldtimer", False)

    assert WINDOW_KEY not in ui.windows_by_key
    assert "oldtimer_main" not in wired["window_handles"]
    assert "Old Timer" not in wired["tray"].windows
    assert not [spec for spec in ui.extra_pages if getattr(spec, "title", None) == "Old Timer"]
    assert widget.isHidden()
    # The region teardown loop runs for every plugin; one with no regions must
    # touch nothing.
    assert list(overlay._region_hosts()) == ["lanes", "utility", "alert", "bars"]

    host.set_enabled("oldtimer", True)

    rebuilt = ui.windows_by_key[WINDOW_KEY]
    assert rebuilt is not widget
    assert isinstance(rebuilt, QWidget)
    assert OWN_SHEET in rebuilt.styleSheet()
    assert "Old Timer" in wired["tray"].windows
    # The live path DOES go through add_page, unlike the startup sweep.
    assert [getattr(spec, "title", None) for spec in wired["settings_window"].pages] == [
        "Old Timer"
    ]
    assert list(overlay._region_hosts()) == ["lanes", "utility", "alert", "bars"]


def test_position_mode_still_works_with_only_built_in_regions(wired) -> None:
    """``_apply_region_chrome`` now reads each host's CURRENT sheet instead of
    the snapshot taken at registration. The built-ins must round-trip through
    position mode exactly as before."""
    overlay = wired["overlay"]
    before = {key: host.styleSheet() for key, host in overlay._region_hosts().items()}

    overlay.set_edit_mode(True)
    assert all("dashed" in host.styleSheet() for host in overlay._region_hosts().values())

    overlay.set_edit_mode(False)

    assert {key: host.styleSheet() for key, host in overlay._region_hosts().items()} == before
