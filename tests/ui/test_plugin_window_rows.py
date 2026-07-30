"""build_plugin_ui: the Settings > Windows rows a plugin's windows earn.

The label a user reads in Settings > Windows is assembled here, from
plugin-supplied metadata that carries no guarantees — a blank name, a title
two windows share, a key declared twice. These pin the fallbacks.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from nparseplus.audio.tts import NullSpeaker
from nparseplus.composition import build_backend
from nparseplus.config.settings import PluginEntry, Settings
from nparseplus.core.plugins.host import PluginHost
from nparseplus.pluginbootstrap import build_plugin_ui
from nparseplus.ui.qtbridge import QtEventBridge

pytestmark = pytest.mark.qt

APP_VERSION = "1.15.0"

# One plugin declaring windows. {windows} is a comma-separated run of
# PluginWindowSpec(...) calls; {factory} is the body of _make.
PLUGIN_TEMPLATE = """
from nparseplus_sdk import NParsePlugin, PluginMeta, PluginWindowSpec


class Demo(NParsePlugin):
    meta = PluginMeta(id="demo", name={name!r}, version="1.0.0")

    def activate(self, ctx):
        for spec in [{windows}]:
            ctx.add_window(spec)

    def _make(self, wctx):
{factory}


def create_plugin():
    return Demo()
"""

# The real thing: a PluginWindow, so it has apply_window_state().
OVERLAY_FACTORY = """        from nparseplus_sdk.ui import PluginWindow

        return PluginWindow(wctx)"""

# Legal per PluginWindowSpec (it promises only .toggle()/.isVisible()), but
# with no overlay state to edit.
BARE_FACTORY = """        from PySide6.QtWidgets import QWidget

        widget = QWidget()
        widget.toggle = lambda: None
        return widget"""


def make_ui(tmp_path: Path, *, windows: str, name: str = "Demo Plugin", factory: str):
    settings = Settings()
    settings.sharing.mode = "off"
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "demo.py").write_text(
        PLUGIN_TEMPLATE.format(name=name, windows=windows, factory=factory),
        encoding="utf-8",
    )
    settings.plugins.entries["demo"] = PluginEntry(enabled=True, approved=True)
    backend = build_backend(settings, speaker=NullSpeaker())
    host = PluginHost(
        settings,
        backend,
        APP_VERSION,
        plugins_dir_override=plugins_dir,
        plugin_data_dir_override=lambda pid: tmp_path / "plugin-data" / pid,
    )
    host.discover_and_load()
    return build_plugin_ui(
        host,
        settings,
        APP_VERSION,
        lambda: None,
        QtEventBridge(backend.bus),
        {},
    )


def test_window_earns_a_row_pointing_at_the_same_widget(qtbot, tmp_path: Path) -> None:
    ui = make_ui(
        tmp_path,
        windows='PluginWindowSpec(key="timer", title="Timer", factory=self._make)',
        factory=OVERLAY_FACTORY,
    )

    assert ui.window_rows == [
        ("Demo Plugin — Timer", "plugin.demo.timer", ui.windows_by_key["plugin.demo.timer"])
    ]


def test_blank_name_and_title_fall_back_to_ids(qtbot, tmp_path: Path) -> None:
    # PluginMeta.name has no min-length validator, so "" is a legal name and
    # a bare " — Timer" would be the alternative.
    ui = make_ui(
        tmp_path,
        name="",
        windows='PluginWindowSpec(key="timer", title="", factory=self._make)',
        factory=OVERLAY_FACTORY,
    )

    assert [label for label, _key, _widget in ui.window_rows] == ["demo — timer"]


def test_two_windows_sharing_a_title_are_disambiguated(qtbot, tmp_path: Path) -> None:
    # The plugin-name prefix cannot separate these — only the key can.
    ui = make_ui(
        tmp_path,
        windows=(
            'PluginWindowSpec(key="a", title="Timer", factory=self._make), '
            'PluginWindowSpec(key="b", title="Timer", factory=self._make)'
        ),
        factory=OVERLAY_FACTORY,
    )

    assert [label for label, _key, _widget in ui.window_rows] == [
        "Demo Plugin — Timer",
        "Demo Plugin — Timer (b)",
    ]


def test_duplicate_window_key_keeps_the_first(qtbot, tmp_path: Path, caplog) -> None:
    # add_window() does not enforce unique keys; two widgets under one key
    # would share one WindowState and fight over a single row.
    with caplog.at_level(logging.WARNING):
        ui = make_ui(
            tmp_path,
            windows=(
                'PluginWindowSpec(key="dup", title="First", factory=self._make), '
                'PluginWindowSpec(key="dup", title="Second", factory=self._make)'
            ),
            factory=OVERLAY_FACTORY,
        )

    assert [label for label, _key, _widget in ui.window_rows] == ["Demo Plugin — First"]
    assert list(ui.windows_by_key) == ["plugin.demo.dup"]
    assert "declared window key 'dup' twice" in caplog.text


def test_widget_without_overlay_state_gets_no_row(qtbot, tmp_path: Path) -> None:
    # It still opens and still reaches the tray — it just has no opacity or
    # on-top state for the Windows grid to edit.
    ui = make_ui(
        tmp_path,
        windows='PluginWindowSpec(key="plain", title="Plain", factory=self._make)',
        factory=BARE_FACTORY,
    )

    assert ui.window_rows == []
    assert "plugin.demo.plain" in ui.windows_by_key
    assert "Plain" in ui.tray


def test_row_order_matches_tray_order(qtbot, tmp_path: Path) -> None:
    # Both are filled in the same loop; a user reading the tray and the
    # Windows grid should see the same sequence.
    ui = make_ui(
        tmp_path,
        windows=(
            'PluginWindowSpec(key="zulu", title="Zulu", factory=self._make), '
            'PluginWindowSpec(key="alpha", title="Alpha", factory=self._make)'
        ),
        factory=OVERLAY_FACTORY,
    )

    assert [label for label, _key, _widget in ui.window_rows] == [
        "Demo Plugin — Zulu",
        "Demo Plugin — Alpha",
    ]
    assert list(ui.tray) == ["Zulu", "Alpha"]
