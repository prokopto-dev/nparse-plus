"""Settings > Plugins — the in-app plugin manager page.

Lists every discovered plugin with status, toggles enablement (persisted
immediately; activation changes take effect next launch), opens the plugins
folder, uninstalls (to ``plugins/trash/``, forgetting the plugin's consent
record and stored data along with it), and installs from a local
zip/.py or an https zip URL. Every install — URL download and local file
alike — runs on a worker thread, because validation imports AND activates the
plugin's module code (the page says so next to the buttons) and a plugin that
hangs there would otherwise hang the GUI thread with it; results land back on
the GUI thread via a signal.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QGroupBox,
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
    MergedListing,
    MultiFetchResult,
    RegistryFetchResult,
    ResolvedRegistry,
    best_update,
    duplicate_listing_ids,
    fetch_indexes,
    registry_display_name,
    release_compat,
)
from nparseplus.ui.pluginconsent import CONSENT_WARNING
from nparseplus.ui.pluginregistries import REGISTRY_WARNING, RegistryListWidget
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

_COLUMNS = ("Enabled", "Name", "Version", "Status", "Location", "Source")
_LOCATION_COLUMN = 4
_SOURCE_COLUMN = 5


def provenance_display(
    source_url: str,
    sha256: str,
    *,
    registry_name: str = "",
    registry_url: str = "",
) -> tuple[str, str]:
    """Cell text + tooltip for where an installed plugin came from.

    ``PluginHost.record_install`` records this for URL/registry/file
    installs; a plugin copied into the folder by hand has neither, and
    saying so plainly is the point — "no recorded source" is exactly the
    provenance the user needs to see.

    A registry-vouched install leads with the registry, because that — not
    the artifact host — is who the user chose to trust. ``registry_name``
    is resolved by the caller from the *current* registry list; passing a
    ``registry_url`` with no name means the registry is no longer
    configured, and the tooltip says so rather than quietly rewriting
    history.
    """
    short = f"{sha256[:12]}…" if sha256 else ""
    if registry_url:
        name = registry_name or registry_display_name(registry_url)
        text = f"{name} · {short}" if short else name
        listed = f"Listed by {name} ({registry_url})"
        if not registry_name:
            listed = f"{listed} — this registry is no longer configured."
        lines = [listed]
        if source_url:
            lines.append(f"Downloaded from {source_url}")
        if sha256:
            lines.append(f"sha256: {sha256}")
        return text, "\n".join(lines)
    if source_url:
        text = f"{source_url} ({short})" if short else source_url
        tooltip = f"Downloaded from {source_url}"
    elif short:
        text = f"Local file ({short})"
        tooltip = "Installed from a local file on this machine"
    else:
        return (
            "Sideloaded",
            "Copied into the plugins folder by hand — no recorded source or checksum.",
        )
    if sha256:
        tooltip = f"{tooltip}\nsha256: {sha256}"
    return text, tooltip


class PluginManagerPage(QWidget):
    """The page widget. Constructed by ``plugin_manager_page_spec``."""

    _install_finished = Signal(object)  # InstallResult, queued from the worker

    def __init__(self, host: PluginHost, app_version: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._host = host
        self._app_version = app_version
        # Installed this session (the host loads them next launch).
        self._session_installs: list[InstallResult] = []
        # Merged listings from the last Browse fetch this session; powers the
        # passive "update available" status decoration.
        self._last_result: MultiFetchResult | None = None
        # Which registry vouched for the install currently in flight (""
        # for file/plain-URL installs). One value is enough: installs are
        # single-flight, gated by _set_install_buttons_enabled.
        self._pending_registry_url = ""
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

        self._registries = RegistryListWidget(host, self)
        registries_box = QGroupBox("Plugin registries", self)
        registries_layout = QVBoxLayout()
        registries_layout.addWidget(self._registries)
        registries_box.setLayout(registries_layout)

        layout = QVBoxLayout()
        # Only the plugin table stretches; the registry list is a short,
        # fixed footnote under it.
        layout.addWidget(self._table, 1)
        layout.addLayout(buttons)
        layout.addWidget(registries_box)
        layout.addWidget(note)
        self.setLayout(layout)

        self.refresh()

    # --- table -------------------------------------------------------------
    def refresh(self) -> None:
        rows = self._host.statuses()
        # Resolved once per refresh: a registry the user has since removed
        # simply won't be in here, which is what makes the display fall back.
        names = {r.url.lower(): r.name for r in self._host.registries()}
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
            registry_url = entry.registry_url if entry is not None else ""
            status += self._update_suffix(plugin_id, version, registry_url)
            # A tick the driver evicted for stalling the app: the plugin is
            # still active, so the status line is the only place it shows.
            dropped = loaded.tick_dropped
            if dropped is not None:
                status = f"{status} — tick disabled (too slow)"
            source_text, source_tip = provenance_display(
                entry.source_url if entry is not None else "",
                entry.sha256 if entry is not None else "",
                registry_name=names.get(registry_url.lower(), ""),
                registry_url=registry_url,
            )
            for column, text in (
                (1, loaded.display_name),
                (2, version),
                (3, status),
                (_LOCATION_COLUMN, loaded.source.location),
                (_SOURCE_COLUMN, source_text),
            ):
                item = QTableWidgetItem(text)
                if column == 3 and (loaded.error or dropped):
                    item.setToolTip(dropped or loaded.error or "")
                elif column == _SOURCE_COLUMN:
                    item.setToolTip(source_tip)
                self._table.setItem(row_index, column, item)
        for offset, result in enumerate(self._session_installs):
            row_index = len(rows) + offset
            self._table.setCellWidget(row_index, 0, QCheckBox(self))
            name = result.meta.name if result.meta is not None else "?"
            version = result.meta.version if result.meta is not None else ""
            location = str(result.installed_path or "")
            entry = self._host.entry_for(result.meta.id) if result.meta is not None else None
            registry_url = entry.registry_url if entry is not None else ""
            source_text, source_tip = provenance_display(
                result.source_url or "",
                result.sha256 or "",
                registry_name=names.get(registry_url.lower(), ""),
                registry_url=registry_url,
            )
            for column, text in (
                (1, name),
                (2, version),
                (3, "Installed — restart to load"),
                (_LOCATION_COLUMN, location),
                (_SOURCE_COLUMN, source_text),
            ):
                item = QTableWidgetItem(text)
                if column == _SOURCE_COLUMN:
                    item.setToolTip(source_tip)
                self._table.setItem(row_index, column, item)

    def _update_suffix(
        self, plugin_id: str | None, installed_version: str, installed_registry_url: str
    ) -> str:
        """The " — update available…" tail for a row, or "".

        Names the offering registry when it is NOT the one that vouched for
        the installed copy: taking that update is a hop to a different
        publisher of the same id, and the user has to be able to see it
        before clicking anything.
        """
        if self._last_result is None or plugin_id is None or not installed_version:
            return ""
        listing = best_update(
            self._last_result.listings,
            plugin_id=plugin_id,
            installed_version=installed_version,
            installed_registry_url=installed_registry_url,
            sdk_version=self._host.sdk_version,
            app_version=self._app_version,
        )
        if listing is None:
            return ""
        version = listing.plugin.latest.version
        if (
            installed_registry_url
            and listing.registry.url.lower() != installed_registry_url.lower()
        ):
            return f" — update available (v{version} from {listing.registry.name})"
        return f" — update available (v{version})"

    def _set_listings(self, result: MultiFetchResult) -> None:
        self._last_result = result
        self.refresh()

    def installed_provenance(self) -> dict[str, str]:
        """Installed plugin id -> the registry URL that vouched for it.

        "" means no registry did (sideloaded, or installed from a bare URL
        or a file) — which is a different statement from "installed from
        somewhere else", and the browse dialog treats it as one.
        """
        provenance: dict[str, str] = {}

        def record(plugin_id: str) -> None:
            entry = self._host.entry_for(plugin_id)
            provenance[plugin_id] = entry.registry_url if entry is not None else ""

        for loaded in self._host.statuses():
            if loaded.plugin_id is not None:
                record(loaded.plugin_id)
        for result in self._session_installs:
            if result.ok and result.meta is not None:
                record(result.meta.id)
        return provenance

    def installed_ids(self) -> set[str]:
        """Plugin ids present on disk (loaded or installed this session)."""
        return set(self.installed_provenance())

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
        self._pending_registry_url = ""  # a file install has no vouching registry
        # Same worker seam as the URL path: install_from_file validates by
        # importing and activating the plugin's module code, which is
        # arbitrary third-party work and must never run on the GUI thread.
        self._start_install(
            lambda: install_from_file(path, self._host.plugins_dir, app_version=self._app_version)
        )

    def _install_from_url(self) -> None:
        url, ok = QInputDialog.getText(
            self, "Install plugin from URL", "https:// URL of a plugin .zip:"
        )
        if not ok or not url.strip():
            return
        self._start_url_install(url.strip())

    def _start_url_install(
        self, url: str, expected_sha256: str | None = None, registry_url: str = ""
    ) -> None:
        """Download+install on a worker thread (registry installs pin a hash).

        The vouching registry is stashed rather than threaded through the
        worker: the installer reports what it downloaded, not who listed it.
        """
        self._pending_registry_url = registry_url
        self._start_install(
            lambda: install_from_url(
                url,
                self._host.plugins_dir,
                app_version=self._app_version,
                expected_sha256=expected_sha256,
            )
        )

    def _start_install(self, install: Callable[[], InstallResult]) -> None:
        """Run one install off the GUI thread; the result comes back by signal."""
        self._set_install_buttons_enabled(False)

        def worker() -> None:
            try:
                result = install()
            except Exception as exc:  # never strand the disabled buttons
                result = InstallResult(ok=False, errors=[f"install failed: {exc!r}"])
            self._install_finished.emit(result)

        threading.Thread(target=worker, name="plugin-install", daemon=True).start()

    def _browse_registry(self) -> None:
        dialog = RegistryBrowserDialog(
            self._host,
            self._app_version,
            on_install=self._start_url_install,
            on_index=self._set_listings,
            installed_provenance=self.installed_provenance,
            parent=self,
        )
        dialog.exec()

    def _on_install_finished(self, result: InstallResult) -> None:
        self._set_install_buttons_enabled(True)
        registry_url, self._pending_registry_url = self._pending_registry_url, ""
        if result.ok:
            self._host.record_install(result, registry_url=registry_url)
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
        plugin_id = self._plugin_id_for_location(location)
        error = uninstall(Path(location), self._host.plugins_dir)
        if error is not None:
            QMessageBox.warning(self, "Uninstall failed", error)
        else:
            # The consent record and stored data go with the code: a plugin
            # re-installed under this id must ask for consent again.
            if plugin_id is not None:
                self._host.forget(plugin_id)
            self._session_installs = [
                r for r in self._session_installs if str(r.installed_path) != location
            ]
            QMessageBox.information(
                self,
                "Plugin uninstalled",
                f"{name} was moved to the trash folder. Restart nParse+ to unload it.",
            )
        self.refresh()

    def _plugin_id_for_location(self, location: str) -> str | None:
        """The plugin id behind a table row, from the host or this session."""
        for loaded in self._host.statuses():
            if loaded.source.location == location and loaded.plugin_id is not None:
                return loaded.plugin_id
        for result in self._session_installs:
            if str(result.installed_path) == location and result.meta is not None:
                return result.meta.id
        return None


_BROWSER_COLUMNS = ("Name", "Version", "Author", "Source", "Compatibility", "")
_BROWSER_NAME_COLUMN = 0
_BROWSER_VERSION_COLUMN = 1
_BROWSER_AUTHOR_COLUMN = 2
_BROWSER_SOURCE_COLUMN = 3
_BROWSER_COMPAT_COLUMN = 4
_BROWSER_ACTION_COLUMN = 5

_FILE_OR_URL_HINT = "You can still install plugins from a file or URL."

_BROWSE_NOTE = (
    "Listings come from the registries you have ticked; a listing is that "
    "registry's word, not a review. Plugins run with full permissions — "
    "install only authors you trust."
)


def source_cell(registry: ResolvedRegistry, also_listed_by: Sequence[str] = ()) -> tuple[str, str]:
    """Cell text + tooltip naming the registry that served a listing.

    "third-party" is spelled out rather than signalled with colour: the whole
    point of the column is that it survives a screenshot, a colour-blind
    user, and a theme that decided red means something else.
    """
    text = registry.name if registry.is_default else f"{registry.name} (third-party)"
    parts = [registry.url]
    if also_listed_by:
        text = f"{text} — also listed elsewhere"
        parts.append(
            "The same plugin id is also listed by: "
            + ", ".join(also_listed_by)
            + ".\nSame id, possibly different code — check which one you want."
        )
    parts.append(REGISTRY_WARNING)
    return text, "\n\n".join(parts)


class RegistryBrowserDialog(QDialog):
    """Browse every enabled plugin registry, merged, and install from one.

    Each registry is fetched on a worker thread and reported separately: one
    dead registry degrades to a line in the status label instead of hiding
    the ones that answered. Rows carry the registry that served them, because
    "which registry vouched for this" is the only thing distinguishing two
    listings that claim the same plugin id. Installs delegate back to the
    manager page's worker with the listing's pinned sha256.
    """

    _index_ready = Signal(object)  # MultiFetchResult, from the fetch worker

    def __init__(
        self,
        host: PluginHost,
        app_version: str,
        *,
        on_install,
        on_index=None,
        installed_provenance=None,
        auto_fetch: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("nParse+ plugin registries")
        self.resize(720, 380)
        self._host = host
        self._app_version = app_version
        self._on_install = on_install
        self._on_index = on_index
        self._installed_provenance = installed_provenance or (lambda: {})
        self._fetching = False

        self._status = QLabel("Fetching the plugin registries…", self)
        self._status.setWordWrap(True)
        self._table = QTableWidget(0, len(_BROWSER_COLUMNS), self)
        self._table.setHorizontalHeaderLabels(_BROWSER_COLUMNS)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setVisible(False)
        self._refresh_button = QPushButton("Refresh", self)
        self._refresh_button.clicked.connect(self._start_fetch)
        close_button = QPushButton("Close", self)
        close_button.clicked.connect(self.accept)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self._refresh_button)
        buttons.addWidget(close_button)

        layout = QVBoxLayout()
        layout.addWidget(self._status)
        layout.addWidget(self._table, 1)
        layout.addLayout(buttons)
        self.setLayout(layout)

        self._index_ready.connect(self._on_index_ready)
        if auto_fetch:
            self._start_fetch()

    def _start_fetch(self) -> None:
        """Kick off one fetch. Single-flight: a second click is a no-op."""
        if self._fetching:
            return
        self._fetching = True
        self._refresh_button.setEnabled(False)
        self._status.setText("Fetching the plugin registries…")
        threading.Thread(target=self._fetch, name="registry-fetch", daemon=True).start()

    def _fetch(self) -> None:
        registries = self._host.enabled_registries()
        try:
            result = fetch_indexes(registries)
        except Exception as exc:  # never strand the disabled Refresh button
            result = MultiFetchResult(
                results=[RegistryFetchResult(registry=r, error=repr(exc)) for r in registries]
            )
        self._index_ready.emit(result)

    def _on_index_ready(self, result: MultiFetchResult) -> None:
        self._fetching = False
        self._refresh_button.setEnabled(True)
        if self._on_index is not None:
            self._on_index(result)
        listings = result.listings
        summary = result.summary_lines()
        if not listings:
            # Nothing to show: either no registry is enabled, they all
            # failed, or they are all genuinely empty. summary_lines covers
            # the first two; the third needs saying here.
            self._table.setVisible(False)
            if summary:
                lines = [*summary, _FILE_OR_URL_HINT]
            else:
                lines = ["The registries are empty — no plugins published yet."]
            self._status.setText("\n".join(lines))
            return
        # Partial failure still shows the table: the registries that answered
        # are usable, and the ones that didn't are named above them.
        self._status.setText("\n".join([*summary, _BROWSE_NOTE]))
        self._table.setVisible(True)
        installed = self._installed_provenance()
        duplicates = duplicate_listing_ids(listings)
        self._table.setRowCount(len(listings))
        for row, merged in enumerate(listings):
            self._fill_row(row, merged, listings, duplicates, installed)

    def _fill_row(
        self,
        row: int,
        merged: MergedListing,
        listings: Sequence[MergedListing],
        duplicates: set[str],
        installed: dict[str, str],
    ) -> None:
        listing = merged.plugin
        reason = release_compat(
            listing.latest,
            sdk_version=self._host.sdk_version,
            app_version=self._app_version,
        )
        others = (
            [
                other.registry.name
                for other in listings
                if other.plugin.id == listing.id and other.registry.url != merged.registry.url
            ]
            if listing.id in duplicates
            else []
        )
        source_text, source_tip = source_cell(merged.registry, others)
        for column, text in (
            (_BROWSER_NAME_COLUMN, listing.name),
            (_BROWSER_VERSION_COLUMN, listing.latest.version),
            (_BROWSER_AUTHOR_COLUMN, listing.author),
            (_BROWSER_SOURCE_COLUMN, source_text),
            (_BROWSER_COMPAT_COLUMN, "OK" if reason is None else reason),
        ):
            item = QTableWidgetItem(text)
            if column == _BROWSER_NAME_COLUMN and listing.description:
                item.setToolTip(listing.description)
            elif column == _BROWSER_SOURCE_COLUMN:
                item.setToolTip(source_tip)
            self._table.setItem(row, column, item)
        self._table.setCellWidget(
            row, _BROWSER_ACTION_COLUMN, self._action_button(merged, reason, installed)
        )

    def _action_button(
        self, merged: MergedListing, reason: str | None, installed: dict[str, str]
    ) -> QPushButton:
        listing = merged.plugin
        button = QPushButton(self)
        if listing.id in installed:
            recorded = installed[listing.id]
            button.setEnabled(False)
            if recorded and recorded.lower() != merged.registry.url.lower():
                # Same id, different vouching registry: possibly different
                # code entirely. Refuse to make that swap a one-click action.
                button.setText("Installed (other source)")
                button.setToolTip(
                    f"Installed from {registry_display_name(recorded)} ({recorded}).\n"
                    f"This listing comes from {merged.registry.name} "
                    f"({merged.registry.url}) — the same id from another registry "
                    "may be entirely different code.\n"
                    "Uninstall the current copy first if you want this one."
                )
            else:
                button.setText("Installed")
        elif reason is not None:
            button.setText("Incompatible")
            button.setEnabled(False)
        else:
            button.setText("Install")
            button.clicked.connect(lambda _checked=False, item=merged: self._install(item))
        return button

    def _install(self, merged: MergedListing) -> None:
        self._on_install(merged.plugin.latest.url, merged.plugin.latest.sha256, merged.registry.url)
        self.accept()


def plugin_manager_page_spec(host: PluginHost, app_version: str) -> SettingsPageSpec:
    """The Plugins page contribution for UnifiedSettingsWindow extra_pages."""
    return SettingsPageSpec(
        "Plugins",
        lambda parent: PluginManagerPage(host, app_version, parent),
    )
