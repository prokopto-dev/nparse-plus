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
        self._install_finished.connect(self._on_install_finished)

        self._table = QTableWidget(0, len(_COLUMNS), self)
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setStretchLastSection(True)

        self._install_file_button = QPushButton("Install from file…", self)
        self._install_file_button.clicked.connect(self._install_from_file)
        self._install_url_button = QPushButton("Install from URL…", self)
        self._install_url_button.clicked.connect(self._install_from_url)
        self._uninstall_button = QPushButton("Uninstall", self)
        self._uninstall_button.clicked.connect(self._uninstall_selected)
        open_button = QPushButton("Open Plugins Folder", self)
        open_button.clicked.connect(self._open_folder)

        buttons = QHBoxLayout()
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
        self._set_install_buttons_enabled(False)

        def worker(target_url: str = url.strip()) -> None:
            result = install_from_url(
                target_url, self._host.plugins_dir, app_version=self._app_version
            )
            self._install_finished.emit(result)

        threading.Thread(target=worker, name="plugin-install", daemon=True).start()

    def _on_install_finished(self, result: InstallResult) -> None:
        self._set_install_buttons_enabled(True)
        if result.ok:
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


def plugin_manager_page_spec(host: PluginHost, app_version: str) -> SettingsPageSpec:
    """The Plugins page contribution for UnifiedSettingsWindow extra_pages."""
    return SettingsPageSpec(
        "Plugins",
        lambda parent: PluginManagerPage(host, app_version, parent),
    )
