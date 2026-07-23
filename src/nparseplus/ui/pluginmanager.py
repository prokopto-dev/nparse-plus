"""Settings > Plugins — the in-app plugin manager page.

Lists every discovered plugin with status, toggles enablement (persisted
immediately; activation changes take effect next launch), opens the plugins
folder, uninstalls (to ``plugins/trash/``), and installs from a local
zip/.py or an https zip URL. URL downloads and archive validation run on a
worker thread (validation imports the plugin's module code — the page says
so next to the buttons); results land back on the GUI thread via a signal.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from nparseplus.core.plugins.install import (
    InstallResult,
    install_from_file,
    install_from_url,
    uninstall,
)
from nparseplus.core.plugins.registry import (
    RegistryIndex,
    RegistryPlugin,
    fetch_index,
    release_compat,
    update_available,
)
from nparseplus.ui.pluginconsent import CONSENT_WARNING
from nparseplus.ui.settingswindow import SettingsPageSpec

if TYPE_CHECKING:
    from nparseplus.core.plugins.host import PluginHost

STATUS_LABELS = {
    "active": "Active",
    "ready": "Ready",
    "disabled": "Disabled",
    "pending_consent": "Awaiting consent",
    "incompatible": "Incompatible",
    "error": "Error",
    "duplicate": "Duplicate id",
}

_COLUMNS = ("Enabled", "Name", "Version", "Status", "Location")


class PluginManagerPage(QWidget):
    """The page widget. Constructed by ``plugin_manager_page_spec``."""

    _install_finished = Signal(object)  # InstallResult, queued from the worker

    def __init__(self, host: PluginHost, app_version: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._host = host
        self._app_version = app_version
        # Installed this session (the host loads them next launch).
        self._session_installs: list[InstallResult] = []
        # Registry index from the last successful Browse fetch this session;
        # powers the passive "update available" status decoration.
        self._last_index: RegistryIndex | None = None
        self._install_finished.connect(self._on_install_finished)

        self._table = QTableWidget(0, len(_COLUMNS), self)
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setStretchLastSection(True)

        self._browse_button = QPushButton("Browse registry…", self)
        self._browse_button.clicked.connect(self._browse_registry)
        self._install_file_button = QPushButton("Install from file…", self)
        self._install_file_button.clicked.connect(self._install_from_file)
        self._install_url_button = QPushButton("Install from URL…", self)
        self._install_url_button.clicked.connect(self._install_from_url)
        self._uninstall_button = QPushButton("Uninstall", self)
        self._uninstall_button.clicked.connect(self._uninstall_selected)
        open_button = QPushButton("Open Plugins Folder", self)
        open_button.clicked.connect(self._open_folder)

        buttons = QHBoxLayout()
        buttons.addWidget(self._browse_button)
        buttons.addWidget(self._install_file_button)
        buttons.addWidget(self._install_url_button)
        buttons.addWidget(self._uninstall_button)
        buttons.addWidget(open_button)
        buttons.addStretch(1)

        note = QLabel(
            f"{CONSENT_WARNING} Installing runs the plugin's module code to "
            "validate it. Enable/disable and new installs take effect the "
            "next time nParse+ starts.",
            self,
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #888888; font-size: 11px;")

        layout = QVBoxLayout()
        layout.addWidget(self._table, 1)
        layout.addLayout(buttons)
        layout.addWidget(note)
        self.setLayout(layout)

        self.refresh()

    # --- table -------------------------------------------------------------
    def refresh(self) -> None:
        rows = self._host.statuses()
        self._table.setRowCount(len(rows) + len(self._session_installs))
        for row_index, loaded in enumerate(rows):
            plugin_id = loaded.plugin_id
            enabled_box = QCheckBox(self)
            entry = self._host.entry_for(plugin_id or "")
            enabled_box.setChecked(
                entry.enabled if entry is not None else loaded.status in ("active", "ready")
            )
            enabled_box.setEnabled(plugin_id is not None)
            if plugin_id is not None:
                enabled_box.toggled.connect(
                    lambda checked, pid=plugin_id: self._host.set_enabled(pid, checked)
                )
            self._table.setCellWidget(row_index, 0, enabled_box)
            version = loaded.meta.version if loaded.meta is not None else ""
            status = STATUS_LABELS.get(loaded.status, loaded.status)
            newer = self._registry_update_for(plugin_id, version)
            if newer is not None:
                status = f"{status} — update available (v{newer})"
            for column, text in (
                (1, loaded.display_name),
                (2, version),
                (3, status),
                (4, loaded.source.location),
            ):
                item = QTableWidgetItem(text)
                if column == 3 and loaded.error:
                    item.setToolTip(loaded.error)
                self._table.setItem(row_index, column, item)
        for offset, result in enumerate(self._session_installs):
            row_index = len(rows) + offset
            self._table.setCellWidget(row_index, 0, QCheckBox(self))
            name = result.meta.name if result.meta is not None else "?"
            version = result.meta.version if result.meta is not None else ""
            location = str(result.installed_path or "")
            for column, text in (
                (1, name),
                (2, version),
                (3, "Installed — restart to load"),
                (4, location),
            ):
                self._table.setItem(row_index, column, QTableWidgetItem(text))

    def _registry_update_for(self, plugin_id: str | None, installed_version: str) -> str | None:
        """Newer registry version string for this plugin, if known this session."""
        if self._last_index is None or plugin_id is None or not installed_version:
            return None
        for listing in self._last_index.plugins:
            if listing.id == plugin_id and update_available(installed_version, listing.latest):
                return listing.latest.version
        return None

    def _set_index(self, index: RegistryIndex) -> None:
        self._last_index = index
        self.refresh()

    def installed_ids(self) -> set[str]:
        """Plugin ids present on disk (loaded or installed this session)."""
        ids = {p.plugin_id for p in self._host.statuses() if p.plugin_id is not None}
        ids.update(r.meta.id for r in self._session_installs if r.ok and r.meta is not None)
        return ids

    # --- actions -----------------------------------------------------------
    def _open_folder(self) -> None:
        path = self._host.plugins_dir
        path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _install_from_file(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "Install plugin", "", "Plugin (*.zip *.py)"
        )
        if not path:
            return
        result = install_from_file(path, self._host.plugins_dir, app_version=self._app_version)
        self._on_install_finished(result)

    def _install_from_url(self) -> None:
        url, ok = QInputDialog.getText(
            self, "Install plugin from URL", "https:// URL of a plugin .zip:"
        )
        if not ok or not url.strip():
            return
        self._start_url_install(url.strip())

    def _start_url_install(self, url: str, expected_sha256: str | None = None) -> None:
        """Download+install on a worker thread (registry installs pin a hash)."""
        self._set_install_buttons_enabled(False)

        def worker() -> None:
            result = install_from_url(
                url,
                self._host.plugins_dir,
                app_version=self._app_version,
                expected_sha256=expected_sha256,
            )
            self._install_finished.emit(result)

        threading.Thread(target=worker, name="plugin-install", daemon=True).start()

    def _browse_registry(self) -> None:
        dialog = RegistryBrowserDialog(
            self._host,
            self._app_version,
            on_install=self._start_url_install,
            on_index=self._set_index,
            installed_ids=self.installed_ids,
            parent=self,
        )
        dialog.exec()

    def _on_install_finished(self, result: InstallResult) -> None:
        self._set_install_buttons_enabled(True)
        if result.ok:
            self._host.record_install(result)
            self._session_installs.append(result)
            name = result.meta.name if result.meta is not None else "Plugin"
            lines = [f"{name} installed. It will load the next time nParse+ starts."]
            if result.warnings:
                lines.append("")
                lines.append("Advisory findings (not a security guarantee):")
                lines.extend(f"• {w}" for w in result.warnings[:12])
            QMessageBox.information(self, "Plugin installed", "\n".join(lines))
        else:
            QMessageBox.warning(self, "Install failed", "\n".join(result.errors) or "Unknown error")
        self.refresh()

    def _set_install_buttons_enabled(self, enabled: bool) -> None:
        self._install_file_button.setEnabled(enabled)
        self._install_url_button.setEnabled(enabled)

    def _uninstall_selected(self) -> None:
        row = self._table.currentRow()
        if row < 0:
            return
        location_item = self._table.item(row, 4)
        name_item = self._table.item(row, 1)
        if location_item is None:
            return
        location = location_item.text()
        in_dir = bool(location) and Path(location).is_relative_to(self._host.plugins_dir)
        if not in_dir:
            QMessageBox.information(
                self,
                "Cannot uninstall",
                "Only plugins inside the plugins folder can be uninstalled here.",
            )
            return
        name = name_item.text() if name_item is not None else location
        confirm = QMessageBox.question(
            self,
            "Uninstall plugin?",
            f"Move {name} to the plugins trash folder?\n({location})",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        error = uninstall(Path(location), self._host.plugins_dir)
        if error is not None:
            QMessageBox.warning(self, "Uninstall failed", error)
        else:
            self._session_installs = [
                r for r in self._session_installs if str(r.installed_path) != location
            ]
            QMessageBox.information(
                self,
                "Plugin uninstalled",
                f"{name} was moved to the trash folder. Restart nParse+ to unload it.",
            )
        self.refresh()


_BROWSER_COLUMNS = ("Name", "Version", "Author", "Compatibility", "")


class RegistryBrowserDialog(QDialog):
    """Browse the curated plugin registry and one-click install from it.

    The index fetch runs on a worker thread; while the registry repo isn't
    live (or the user is offline) the dialog degrades to a plain
    "Registry unavailable" message. Installs delegate back to the manager
    page's worker with the index's pinned sha256.
    """

    _index_ready = Signal(object)  # RegistryIndex on success, str(reason) on failure

    def __init__(
        self,
        host: PluginHost,
        app_version: str,
        *,
        on_install,
        on_index=None,
        installed_ids=None,
        auto_fetch: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("nParse+ plugin registry")
        self.resize(640, 360)
        self._host = host
        self._app_version = app_version
        self._on_install = on_install
        self._on_index = on_index
        self._installed_ids = installed_ids or (lambda: set())

        self._status = QLabel("Fetching the plugin registry…", self)
        self._status.setWordWrap(True)
        self._table = QTableWidget(0, len(_BROWSER_COLUMNS), self)
        self._table.setHorizontalHeaderLabels(_BROWSER_COLUMNS)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setVisible(False)
        close_button = QPushButton("Close", self)
        close_button.clicked.connect(self.accept)

        layout = QVBoxLayout()
        layout.addWidget(self._status)
        layout.addWidget(self._table, 1)
        layout.addWidget(close_button)
        self.setLayout(layout)

        self._index_ready.connect(self._on_index_ready)
        if auto_fetch:
            threading.Thread(target=self._fetch, name="registry-fetch", daemon=True).start()

    def _fetch(self) -> None:
        try:
            index = fetch_index(self._host.registry_url)
        except ValueError as exc:
            self._index_ready.emit(str(exc))
        else:
            self._index_ready.emit(index)

    def _on_index_ready(self, payload: object) -> None:
        if isinstance(payload, str):
            self._status.setText(
                f"Registry unavailable — {payload}\n"
                "You can still install plugins from a file or URL."
            )
            return
        assert isinstance(payload, RegistryIndex)
        if self._on_index is not None:
            self._on_index(payload)
        if not payload.plugins:
            self._status.setText("The registry is empty — no plugins published yet.")
            return
        self._status.setText(
            "Plugins below are community-reviewed listings; they still run "
            "with full permissions — install only authors you trust."
        )
        self._table.setVisible(True)
        installed = self._installed_ids()
        self._table.setRowCount(len(payload.plugins))
        for row, listing in enumerate(payload.plugins):
            reason = release_compat(
                listing.latest,
                sdk_version=self._host.sdk_version,
                app_version=self._app_version,
            )
            for column, text in (
                (0, listing.name),
                (1, listing.latest.version),
                (2, listing.author),
                (3, "OK" if reason is None else reason),
            ):
                item = QTableWidgetItem(text)
                if column == 0 and listing.description:
                    item.setToolTip(listing.description)
                self._table.setItem(row, column, item)
            button = QPushButton(self)
            if listing.id in installed:
                button.setText("Installed")
                button.setEnabled(False)
            elif reason is not None:
                button.setText("Incompatible")
                button.setEnabled(False)
            else:
                button.setText("Install")
                button.clicked.connect(lambda _checked=False, plug=listing: self._install(plug))
            self._table.setCellWidget(row, 4, button)

    def _install(self, listing: RegistryPlugin) -> None:
        self._on_install(listing.latest.url, listing.latest.sha256)
        self.accept()


def plugin_manager_page_spec(host: PluginHost, app_version: str) -> SettingsPageSpec:
    """The Plugins page contribution for UnifiedSettingsWindow extra_pages."""
    return SettingsPageSpec(
        "Plugins",
        lambda parent: PluginManagerPage(host, app_version, parent),
    )
