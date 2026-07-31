"""The post-launch plugin update check: quiet, optional, and fail-soft."""

from __future__ import annotations

from pathlib import Path

import pytest

from nparseplus import pluginbootstrap
from nparseplus.audio.tts import NullSpeaker
from nparseplus.composition import build_backend
from nparseplus.config.settings import PluginEntry, Settings
from nparseplus.core.plugins.host import PluginHost
from nparseplus.core.plugins.registry import MultiFetchResult
from nparseplus.core.plugins.updatecheck import UpdateCheckResult

pytestmark = pytest.mark.qt

PLUGIN_SOURCE = """
from nparseplus_sdk import NParsePlugin, PluginMeta


class Demo(NParsePlugin):
    meta = PluginMeta(id="demo", name="Demo Plugin", version="1.2.0")

    def activate(self, ctx):
        pass


def create_plugin():
    return Demo()
"""


@pytest.fixture
def host(tmp_path: Path):
    settings = Settings()
    settings.sharing.mode = "off"
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "demo.py").write_text(PLUGIN_SOURCE, encoding="utf-8")
    settings.plugins.entries["demo"] = PluginEntry(enabled=True, approved=True)
    host = PluginHost(
        settings,
        build_backend(settings, speaker=NullSpeaker()),
        "1.15.0",
        plugins_dir_override=plugins_dir,
        plugin_data_dir_override=lambda pid: tmp_path / "plugin-data" / pid,
    )
    host.discover_and_load()
    return host


def test_the_check_populates_the_host_cache(qtbot, host, monkeypatch) -> None:
    result = UpdateCheckResult(fetched=MultiFetchResult(results=[]))
    monkeypatch.setattr(
        "nparseplus.core.plugins.updatecheck.check_for_updates",
        lambda *a, **k: result,
    )
    assert host.cached_update_check() is None

    pluginbootstrap.schedule_update_check(host, "1.15.0", delay_ms=0)

    qtbot.waitUntil(lambda: host.cached_update_check() is not None, timeout=5000)
    assert host.cached_update_check() is result


def test_it_asks_only_about_what_is_installed(qtbot, host, monkeypatch) -> None:
    seen: list[list] = []

    def fake(installed, registries, **kwargs):
        seen.append([p.plugin_id for p in installed])
        return UpdateCheckResult(fetched=MultiFetchResult(results=[]))

    monkeypatch.setattr("nparseplus.core.plugins.updatecheck.check_for_updates", fake)
    pluginbootstrap.schedule_update_check(host, "1.15.0", delay_ms=0)
    qtbot.waitUntil(lambda: bool(seen), timeout=5000)
    assert seen[0] == ["demo"]


def test_a_failing_check_is_swallowed(qtbot, host, monkeypatch) -> None:
    # The user did not ask for this check; a registry being down must never
    # reach them as a crash.
    calls: list[int] = []

    def boom(*a, **k):
        calls.append(1)
        raise OSError("the internet is closed")

    monkeypatch.setattr("nparseplus.core.plugins.updatecheck.check_for_updates", boom)
    pluginbootstrap.schedule_update_check(host, "1.15.0", delay_ms=0)
    qtbot.waitUntil(lambda: bool(calls), timeout=5000)
    assert host.cached_update_check() is None


def test_it_runs_off_the_gui_thread(qtbot, host, monkeypatch) -> None:
    import threading

    gui_thread = threading.current_thread()
    ran_on: list[threading.Thread] = []

    def fake(*a, **k):
        ran_on.append(threading.current_thread())
        return UpdateCheckResult(fetched=MultiFetchResult(results=[]))

    monkeypatch.setattr("nparseplus.core.plugins.updatecheck.check_for_updates", fake)
    pluginbootstrap.schedule_update_check(host, "1.15.0", delay_ms=0)
    qtbot.waitUntil(lambda: bool(ran_on), timeout=5000)
    assert ran_on[0] is not gui_thread


def test_build_plugin_ui_skips_the_check_when_it_is_off(qtbot, host, monkeypatch) -> None:
    scheduled: list[int] = []
    monkeypatch.setattr(
        pluginbootstrap, "schedule_update_check", lambda *a, **k: scheduled.append(1)
    )
    host._settings.plugins.update_check = False

    from nparseplus.ui.qtbridge import QtEventBridge

    pluginbootstrap.build_plugin_ui(
        host,
        host._settings,
        "1.15.0",
        lambda: None,
        QtEventBridge(host._backend.bus),
        {},
    )
    assert scheduled == []


def test_build_plugin_ui_schedules_the_check_when_it_is_on(qtbot, host, monkeypatch) -> None:
    scheduled: list[int] = []
    monkeypatch.setattr(
        pluginbootstrap, "schedule_update_check", lambda *a, **k: scheduled.append(1)
    )
    assert host._settings.plugins.update_check is True

    from nparseplus.ui.qtbridge import QtEventBridge

    pluginbootstrap.build_plugin_ui(
        host,
        host._settings,
        "1.15.0",
        lambda: None,
        QtEventBridge(host._backend.bus),
        {},
    )
    assert scheduled == [1]
