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
    host = PluginHost(
        settings,
        backend,
        "1.15.0",
        plugins_dir_override=plugins_dir,
        # Keep uninstall's forget() away from the real per-user plugin data.
        plugin_data_dir_override=lambda pid: tmp_path / "plugin-data" / pid,
    )
    host.discover_and_load()
    host.activate_enabled()
    return host


def make_page(qtbot, host) -> PluginManagerPage:
    page = PluginManagerPage(host, "1.15.0")
    qtbot.addWidget(page)
    return page


def install_from_file_and_wait(qtbot, page: PluginManagerPage) -> None:
    """Installs run on a worker thread; spin the loop until the result lands."""
    with qtbot.waitSignal(page._install_finished, timeout=5000):
        page._install_from_file()


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
    install_from_file_and_wait(qtbot, page)
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
    install_from_file_and_wait(qtbot, page)
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


def test_uninstall_forgets_consent_and_data(qtbot, host, tmp_path: Path, monkeypatch) -> None:
    """Uninstalling must not leave an approval a future 'demo' could inherit."""
    from nparseplus.core.plugins.storage import JsonPluginStorage

    data_dir = tmp_path / "plugin-data" / "demo"
    JsonPluginStorage(data_dir).save({"api_key": "hunter2"})
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    )
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    page = make_page(qtbot, host)
    page._table.setCurrentCell(0, 1)
    page._uninstall_selected()
    assert host.entry_for("demo") is None
    assert not data_dir.exists()
    assert (host.plugins_dir / "trash" / "plugin-data" / "demo" / "storage.json").is_file()


def test_uninstall_of_a_session_install_forgets_it(
    qtbot, host, tmp_path: Path, monkeypatch
) -> None:
    archive = tmp_path / "extra.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("extra.py", PLUGIN_SOURCE.replace('"demo"', '"extra"'))
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(archive), "zip"))
    )
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    )
    page = make_page(qtbot, host)
    install_from_file_and_wait(qtbot, page)
    assert host.entry_for("extra") is not None  # provenance recorded at install
    page._table.setCurrentCell(1, 1)  # the session-install row
    page._uninstall_selected()
    assert host.entry_for("extra") is None
    assert not (host.plugins_dir / "extra.py").exists()


def test_file_install_runs_off_the_gui_thread(qtbot, host, tmp_path: Path, monkeypatch) -> None:
    """install_from_file imports AND activates plugin code — never inline."""
    import threading

    archive = tmp_path / "worker.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("worker.py", PLUGIN_SOURCE.replace('"demo"', '"worker"'))
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(archive), "zip"))
    )
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    gui_thread = threading.current_thread()
    ran_on: list[threading.Thread] = []
    real_install = pluginmanager.install_from_file

    def recording_install(*args, **kwargs):
        ran_on.append(threading.current_thread())
        return real_install(*args, **kwargs)

    monkeypatch.setattr(pluginmanager, "install_from_file", recording_install)
    page = make_page(qtbot, host)
    install_from_file_and_wait(qtbot, page)

    assert ran_on and ran_on[0] is not gui_thread
    assert (host.plugins_dir / "worker.py").is_file()


def test_install_buttons_re_enable_after_a_worker_crash(qtbot, host, monkeypatch) -> None:
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    page = make_page(qtbot, host)

    def boom():
        raise RuntimeError("worker exploded")

    with qtbot.waitSignal(page._install_finished, timeout=5000):
        page._start_install(boom)
    assert page._install_file_button.isEnabled()


def test_dropped_plugin_tick_is_shown(qtbot, host) -> None:
    """The driver's eviction of a slow tick has to reach the user."""
    loaded = host.statuses()[0]
    assert loaded.context is not None
    loaded.context.tick_dropped = "tick removed after 2 consecutive runs over 250 ms"
    page = make_page(qtbot, host)
    status_item = page._table.item(0, 3)
    assert "tick disabled" in status_item.text().lower()
    assert "250 ms" in status_item.toolTip()


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


def _index(version: str = "9.9.9", plugin_id: str = "demo", requires_sdk: str = ">=1.0,<2"):
    from nparseplus.core.plugins.registry import RegistryIndex

    return RegistryIndex.model_validate(
        {
            "schema_version": 1,
            "plugins": [
                {
                    "id": plugin_id,
                    "name": plugin_id.title(),
                    "author": "someone",
                    "description": "a fine plugin",
                    "latest": {
                        "version": version,
                        "url": f"https://example.com/{plugin_id}.zip",
                        "sha256": "a" * 64,
                        "requires_sdk": requires_sdk,
                    },
                }
            ],
        }
    )


def make_dialog(qtbot, host, page=None, **kwargs):
    from nparseplus.ui.pluginmanager import RegistryBrowserDialog

    installs: list[tuple[str, str]] = []
    dialog = RegistryBrowserDialog(
        host,
        "1.15.0",
        on_install=lambda url, sha: installs.append((url, sha)),
        on_index=(page._set_index if page is not None else None),
        installed_ids=(page.installed_ids if page is not None else None),
        auto_fetch=False,
    )
    qtbot.addWidget(dialog)
    return dialog, installs


def test_browser_lists_and_installs_with_pinned_hash(qtbot, host) -> None:
    dialog, installs = make_dialog(qtbot, host)
    dialog._on_index_ready(_index(plugin_id="shiny"))
    assert dialog._table.rowCount() == 1
    assert dialog._table.item(0, 0).text() == "Shiny"
    assert dialog._table.item(0, 3).text() == "OK"
    button = dialog._table.cellWidget(0, 4)
    assert button.text() == "Install" and button.isEnabled()
    button.click()
    assert installs == [("https://example.com/shiny.zip", "a" * 64)]


def test_browser_incompatible_row_disabled_with_reason(qtbot, host) -> None:
    dialog, installs = make_dialog(qtbot, host)
    dialog._on_index_ready(_index(plugin_id="future", requires_sdk=">=99.0"))
    assert ">=99.0" in dialog._table.item(0, 3).text()
    button = dialog._table.cellWidget(0, 4)
    assert button.text() == "Incompatible" and not button.isEnabled()
    assert installs == []


def test_browser_installed_row_disabled(qtbot, host) -> None:
    page = make_page(qtbot, host)
    dialog, _installs = make_dialog(qtbot, host, page)
    dialog._on_index_ready(_index(plugin_id="demo"))  # demo is already installed
    button = dialog._table.cellWidget(0, 4)
    assert button.text() == "Installed" and not button.isEnabled()


def test_browser_unavailable_state(qtbot, host) -> None:
    dialog, _installs = make_dialog(qtbot, host)
    dialog._on_index_ready("could not reach the registry: offline")
    assert "unavailable" in dialog._status.text().lower()
    assert not dialog._table.isVisible()


def test_update_available_status_after_index(qtbot, host) -> None:
    page = make_page(qtbot, host)
    assert "update available" not in page._table.item(0, 3).text().lower()
    page._set_index(_index(version="9.9.9", plugin_id="demo"))
    assert "update available (v9.9.9)" in page._table.item(0, 3).text()
    # An index no newer than the installed version adds nothing.
    page._set_index(_index(version="1.2.0", plugin_id="demo"))
    assert "update available" not in page._table.item(0, 3).text().lower()


def test_registry_install_records_provenance(qtbot, host, monkeypatch) -> None:
    from nparseplus.core.plugins.install import InstallResult
    from nparseplus_sdk import PluginMeta

    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    page = make_page(qtbot, host)
    result = InstallResult(
        ok=True,
        meta=PluginMeta(id="fromreg", name="From Registry", version="1.0.0"),
        sha256="a" * 64,
        source_url="https://example.com/fromreg.zip",
        installed_path=host.plugins_dir / "fromreg.py",
    )
    page._on_install_finished(result)
    entry = host.entry_for("fromreg")
    assert entry is not None
    assert entry.source_url == "https://example.com/fromreg.zip"
    assert entry.approved is False  # consent still due next launch


# --- install provenance -----------------------------------------------------


def test_sideloaded_plugin_shows_no_provenance(qtbot, host) -> None:
    """A plugin dropped into the folder by hand says so, plainly."""
    page = make_page(qtbot, host)
    source_item = page._table.item(0, 5)
    assert source_item.text() == "Sideloaded"
    assert "no recorded source" in source_item.toolTip()


def test_url_installed_plugin_shows_source_and_hash(qtbot, host, monkeypatch) -> None:
    from nparseplus.core.plugins.install import InstallResult
    from nparseplus_sdk import PluginMeta

    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    page = make_page(qtbot, host)
    page._on_install_finished(
        InstallResult(
            ok=True,
            meta=PluginMeta(id="fromreg", name="From Registry", version="1.0.0"),
            sha256="b" * 64,
            source_url="https://example.com/fromreg.zip",
            installed_path=host.plugins_dir / "fromreg.py",
        )
    )
    source_item = page._table.item(1, 5)  # the session-install row
    assert "https://example.com/fromreg.zip" in source_item.text()
    assert "bbbbbbbbbbbb" in source_item.text()  # short hash
    assert "b" * 64 in source_item.toolTip()  # full hash on hover


def test_loaded_plugin_row_reads_provenance_from_the_entry(qtbot, host) -> None:
    """Next launch the row is a loaded plugin: provenance comes off the entry."""
    entry = host.entry_for("demo")
    entry.source_url = "https://example.com/demo.zip"
    entry.sha256 = "f" * 64
    page = make_page(qtbot, host)
    assert "https://example.com/demo.zip" in page._table.item(0, 5).text()
    assert "f" * 64 in page._table.item(0, 5).toolTip()


def test_provenance_display_forms() -> None:
    text, tip = pluginmanager.provenance_display("https://x/y.zip", "c" * 64)
    assert text.startswith("https://x/y.zip") and "c" * 64 in tip
    text, tip = pluginmanager.provenance_display("", "d" * 64)
    assert text == f"Local file ({'d' * 12}…)"
    assert pluginmanager.provenance_display("", "")[0] == "Sideloaded"
