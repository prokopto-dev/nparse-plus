"""Plugins manager page: listing, enable toggle, install, uninstall."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from PySide6.QtWidgets import QCheckBox, QFileDialog, QMessageBox

from nparseplus.audio.tts import NullSpeaker
from nparseplus.composition import build_backend
from nparseplus.config.settings import PluginEntry, Settings
from nparseplus.core.plugins.host import PluginHost
from nparseplus.ui import pluginmanager
from nparseplus.ui.pluginmanager import PluginManagerPage, plugin_manager_page_spec

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
    backend = build_backend(settings, speaker=NullSpeaker())
    host = PluginHost(settings, backend, "1.15.0", plugins_dir_override=plugins_dir)
    host.discover_and_load()
    host.activate_enabled()
    return host


def make_page(qtbot, host) -> PluginManagerPage:
    page = PluginManagerPage(host, "1.15.0")
    qtbot.addWidget(page)
    return page


def test_lists_discovered_plugins(qtbot, host) -> None:
    page = make_page(qtbot, host)
    assert page._table.rowCount() == 1
    assert page._table.item(0, 1).text() == "Demo Plugin"
    assert page._table.item(0, 2).text() == "1.2.0"
    assert page._table.item(0, 3).text() == "Active"


def test_enable_checkbox_persists(qtbot, host) -> None:
    page = make_page(qtbot, host)
    box = page._table.cellWidget(0, 0)
    assert isinstance(box, QCheckBox) and box.isChecked()
    box.setChecked(False)
    assert host.entry_for("demo").enabled is False


def test_install_from_file_via_dialog(qtbot, host, tmp_path: Path, monkeypatch) -> None:
    archive = tmp_path / "extra.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("extra.py", PLUGIN_SOURCE.replace('"demo"', '"extra"'))
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(archive), "zip"))
    )
    infos: list[tuple] = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: infos.append(a)))
    page = make_page(qtbot, host)
    page._install_from_file()
    assert (host.plugins_dir / "extra.py").is_file()
    assert infos, "success dialog not shown"
    assert page._table.rowCount() == 2  # session-install row appended
    assert "restart" in page._table.item(1, 3).text().lower()


def test_install_failure_shows_warning(qtbot, host, tmp_path: Path, monkeypatch) -> None:
    bad = tmp_path / "bad.zip"
    bad.write_text("not a zip", encoding="utf-8")
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(bad), "zip"))
    )
    warnings: list[tuple] = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a)))
    page = make_page(qtbot, host)
    page._install_from_file()
    assert warnings, "failure dialog not shown"
    assert page._table.rowCount() == 1


def test_uninstall_selected_moves_to_trash(qtbot, host, monkeypatch) -> None:
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    page = make_page(qtbot, host)
    page._table.setCurrentCell(0, 1)
    page._uninstall_selected()
    assert not (host.plugins_dir / "demo.py").exists()
    assert (host.plugins_dir / "trash" / "demo.py").is_file()


def test_page_spec_builds_page(qtbot, host) -> None:
    spec = plugin_manager_page_spec(host, "1.15.0")
    assert spec.title == "Plugins"
    page = spec.builder(None)
    qtbot.addWidget(page)
    assert isinstance(page, PluginManagerPage)
    assert spec.apply is None


def test_url_install_worker_roundtrip(qtbot, host, monkeypatch) -> None:
    """The URL path emits its result back to the GUI thread and refreshes."""
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("fetched.py", PLUGIN_SOURCE.replace('"demo"', '"fetched"'))

    monkeypatch.setattr(
        pluginmanager,
        "install_from_url",
        lambda url, plugins_dir, app_version=None: pluginmanager.install_from_file(
            _write_zip(plugins_dir.parent, buffer.getvalue()),
            plugins_dir,
            app_version=app_version,
        ),
    )
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    page = make_page(qtbot, host)
    # Drive the finished-signal slot directly (thread exercised in core tests).
    result = pluginmanager.install_from_url(
        "https://example.com/p.zip", host.plugins_dir, app_version="1.15.0"
    )
    page._on_install_finished(result)
    assert (host.plugins_dir / "fetched.py").is_file()
    assert page._table.rowCount() == 2


def _write_zip(directory: Path, payload: bytes) -> Path:
    target = directory / "downloaded.zip"
    target.write_bytes(payload)
    return target
