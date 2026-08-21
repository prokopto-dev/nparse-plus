"""Settings > Plugins — the in-app plugin manager page.

Lists every discovered plugin with status, toggles enablement (persisted AND
applied immediately since #45 — the row re-renders because the plugin really
did start or stop), opens the plugins folder, uninstalls (to
``plugins/trash/``, forgetting the plugin's consent record and stored data
along with it), and installs from a local
zip/.py or an https zip URL. Every install — URL download and local file
alike — runs on a worker thread, because validation imports AND activates the
plugin's module code (the page says so next to the buttons) and a plugin that
hangs there would otherwise hang the GUI thread with it; results land back on
the GUI thread via a signal.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
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
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from nparseplus.core.plugins.install import (
    InstallResult,
    ReplaceTarget,
    install_from_file,
    install_from_url,
    uninstall,
)
from nparseplus.core.plugins.registry import (
    MergedListing,
    MultiFetchResult,
    RegistryFetchResult,
    ResolvedRegistry,
    duplicate_listing_ids,
    fetch_indexes,
    registry_display_name,
)
from nparseplus.core.plugins.telemetry import format_note, format_tooltip
from nparseplus.core.plugins.updatecheck import (
    SELF_FEED_WARNING,
    InstalledPlugin,
    ListingAction,
    PluginUpdate,
    UpdateCheckResult,
    check_for_updates,
    listing_action,
    pending_updates,
    same_source_updates,
    updates_by_id,
)
from nparseplus.ui import chromewidgets
from nparseplus.ui.pluginconsent import CONSENT_WARNING
from nparseplus.ui.pluginregistries import REGISTRY_WARNING, RegistryListWidget
from nparseplus.ui.settingswindow import SettingsPageSpec

if TYPE_CHECKING:
    from nparseplus.core.plugins.host import LoadedPlugin, PluginHost

logger = logging.getLogger(__name__)

# How many failed updates a batch summary names before collapsing the rest.
# Not registry.py's identically-valued cap on unreachable registries — these
# are different lists and there is no reason they should move together.
_MAX_REPORTED_UPDATE_FAILURES = 5

STATUS_LABELS = {
    "active": "Active",
    "ready": "Ready",
    "disabled": "Disabled",
    "pending_consent": "Awaiting consent",
    "incompatible": "Incompatible",
    "error": "Error",
    "duplicate": "Duplicate id",
}

_COLUMNS = (
    "Enabled",
    "Name",
    "Version",
    "Status",
    "Performance",
    "Location",
    "Source",
    "Update",
)
_PERFORMANCE_COLUMN = 4
_LOCATION_COLUMN = 5
_SOURCE_COLUMN = 6
_UPDATE_COLUMN = 7

# The Performance cell is repainted on its own, without rebuilding the table:
# refresh() destroys and recreates the Enabled checkbox and the Update button,
# and doing that once a second under the user's cursor is not acceptable for a
# number that is only ever informational.
_STATS_REFRESH_MS = 1000


def install_outcome_text(name: str, loaded: LoadedPlugin | None) -> str:
    """What actually happened to a just-installed plugin, in one sentence.

    Since #45 an install loads the plugin immediately, so the dialog can say
    something true instead of "it will load next launch" — but only if it
    reads the row it ended up in. Declining consent, an SDK mismatch, a
    duplicate id and a raising ``activate()`` are all outcomes where the
    plugin is installed and NOT running, and reporting them as "installed and
    running" is worse than the restart notice it replaced: the user would go
    looking for a feature that is switched off.

    Pure, so the wording is testable without an installer or a dialog.
    """
    if loaded is None:
        return f"{name} installed. It will load the next time nParse+ starts."
    reason = f" — {loaded.error}" if loaded.error else ""
    return {
        "active": f"{name} installed and running.",
        "disabled": (
            f"{name} installed, and left disabled. Tick it in the plugins list "
            "when you want it to run."
        ),
        "incompatible": f"{name} installed, but it cannot run in this build{reason}.",
        "duplicate": f"{name} installed, but another add-on already claims its id{reason}.",
        "error": (
            f"{name} installed, but it failed to start{reason}. "
            "See nparseplus.log for the traceback."
        ),
    }.get(loaded.status, f"{name} installed. It will load the next time nParse+ starts.")


def update_suffix(update: PluginUpdate | None) -> str:
    """The " — update available…" tail for a status cell, or "".

    Names the offering source when it is NOT the one that vouched for the
    installed copy: taking that update is a hop to a different publisher of
    the same id, and the user has to be able to see that before clicking
    anything. Pure, so the wording is testable without a widget.
    """
    if update is None:
        return ""
    if update.needs_confirmation:
        return f" — update available (v{update.offered_version} from {update.source_name})"
    return f" — update available (v{update.offered_version})"


# How much of a release's notes a confirmation dialog carries. The registry
# caps them at 2048 bytes, which is a paragraph too many for a message box;
# the Browse pane below shows whatever is there in full.
_NOTES_IN_DIALOG_CHARS = 600


def release_notes_block(version: str, notes: str) -> str:
    """The "What's new" paragraph for a release, or "" when there is none.

    The text is the author's, carried by the registry as PLAIN TEXT and
    deliberately not interpreted: no Markdown, no HTML, no sanitiser (the
    registry's ADR-0013 chose plain text precisely so that no client needs
    one). This trims and truncates for the space available and does nothing
    else — every caller must render the result somewhere that shows text as
    text, which for Qt means a widget whose text format is PlainText, never
    a tooltip or an auto-format label.
    """
    text = notes.strip()
    if not text:
        return ""
    if len(text) > _NOTES_IN_DIALOG_CHARS:
        text = text[:_NOTES_IN_DIALOG_CHARS].rstrip() + "…"
    return f"What's new in v{version}:\n{text}"


def update_confirm_text(update: PluginUpdate) -> str:
    """The body of the "this comes from somewhere else" confirmation.

    Two different admissions, because the two situations are not the same:
    a cross-registry offer can name both ends, while an offer for a plugin
    nobody vouched for has to say plainly that there is no record at all.
    """
    header = (
        f"Update {update.plugin_id} from v{update.installed_version} to v{update.offered_version}?"
    )
    if update.unknown_provenance:
        origin = (
            "nParse+ has no record of where your copy of this plugin came from "
            "(it was sideloaded, or installed from a plain URL).\n\n"
            f"This update is offered by {update.source_name}."
        )
    else:
        origin = (
            f"Your copy came from {registry_display_name(update.installed_registry_url)}, "
            f"but this update is offered by {update.source_name}."
        )
    body = [header, "", origin, "", CONSENT_WARNING]
    if update.listing.registry.is_self_published:
        body += ["", SELF_FEED_WARNING]
    body += [
        "",
        "The same plugin id from a different source may be entirely different "
        "code. Your settings and this plugin's stored data are kept.",
    ]
    notes = release_notes_block(update.offered_version, update.listing.plugin.latest.notes)
    if notes:
        body += ["", notes]
    return "\n".join(body)


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
    _check_finished = Signal(object)  # UpdateCheckResult|None, from the worker

    def __init__(self, host: PluginHost, app_version: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._host = host
        self._app_version = app_version
        # Installed this session but NOT loadable in place — the narrow case
        # adopt_installed refuses (nothing the plugins-folder sweep would pick
        # up). An ordinary install joins host.statuses() and runs at once.
        self._session_installs: list[InstallResult] = []
        # How a just-installed plugin is asked about, injectable so a test can
        # answer without a modal dialog (the same seam run_consent_prompts
        # already offers). None = the real first-load dialog.
        self.consent_ask: Callable[[LoadedPlugin], bool] | None = None
        self._checking = False
        # Which registry vouched for the install currently in flight (""
        # for file/plain-URL installs). One value is enough: installs are
        # single-flight, gated by _set_install_buttons_enabled — and the
        # update batch below deliberately keeps it that way.
        self._pending_registry_url = ""
        # The update currently installing, and the rest of a batch behind it.
        self._pending_update: PluginUpdate | None = None
        self._update_queue: list[PluginUpdate] = []
        self._update_results: list[tuple[PluginUpdate, InstallResult]] = []
        self._batch_active = False
        self._install_finished.connect(self._on_install_finished)
        self._check_finished.connect(self._on_check_finished)

        self._table = QTableWidget(0, len(_COLUMNS), self)
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setStretchLastSection(True)

        self._check_button = QPushButton("Check for updates", self)
        self._check_button.clicked.connect(self.start_update_check)
        self._update_all_button = QPushButton("Update all", self)
        self._update_all_button.clicked.connect(self._update_all)
        self._status = chromewidgets.hint("", self)

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
        buttons.addWidget(self._check_button)
        buttons.addWidget(self._update_all_button)
        buttons.addWidget(self._browse_button)
        buttons.addWidget(self._install_file_button)
        buttons.addWidget(self._install_url_button)
        buttons.addWidget(self._uninstall_button)
        buttons.addWidget(open_button)
        buttons.addStretch(1)

        self._auto_check = QCheckBox("Check for plugin updates shortly after launch", self)
        self._auto_check.setChecked(host.update_check_enabled)
        self._auto_check.setToolTip(
            "Contacts every registry you have ticked, plus the update feed of "
            "each enabled plugin that declares one."
        )
        self._auto_check.toggled.connect(self._host.set_update_check)

        self._telemetry_box = QCheckBox("Measure add-on performance", self)
        self._telemetry_box.setChecked(host.telemetry_enabled)
        self._telemetry_box.setToolTip(
            "Fills the Performance column: how often each add-on's handlers "
            "run and what they cost on the log thread. Timing is only ever "
            "applied to add-on callbacks, never to nParse+'s own."
        )
        self._telemetry_box.toggled.connect(self._set_telemetry)

        note = QLabel(
            f"{CONSENT_WARNING} Installing runs the plugin's module code to "
            "validate it. Enabling, disabling and new installs take effect "
            "immediately; updating a plugin you already have needs a restart.",
            self,
        )

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
        layout.addWidget(self._status)
        layout.addWidget(self._auto_check)
        layout.addWidget(self._telemetry_box)
        layout.addWidget(registries_box)
        layout.addWidget(note)
        self.setLayout(layout)

        self.refresh()

        # Parented to this page, so it dies with the settings window rather
        # than polling a host nobody is looking at.
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(_STATS_REFRESH_MS)
        self._stats_timer.timeout.connect(self._tick_stats)
        self._stats_timer.start()

    @property
    def _check(self) -> UpdateCheckResult | None:
        """The last update check — read straight from the host, never copied.

        The host owns it because this page is rebuilt on every settings-window
        open while the host outlives them all (so a check that ran at launch
        is already visible the first time the page is built). Reading through
        rather than mirroring means there is no second copy to fall out of
        date with it.
        """
        return self._host.cached_update_check()

    @property
    def _updates(self) -> list[PluginUpdate]:
        check = self._check
        return list(check.updates) if check is not None else []

    def _toggle_enabled(self, plugin_id: str, enabled: bool) -> None:
        """Apply the box — and re-render, because the toggle happens NOW (#45).

        The row would otherwise keep saying "Active" for a plugin that has
        just been unwound, until something else happened to refresh the
        table. Status is not the only stale cell either: a disabled plugin
        stops being offered updates, and a re-enabled one can land in
        ``error``, which is exactly the case a user needs to see immediately.

        Deferred to the next turn of the event loop on purpose. ``refresh``
        rebuilds every row, which destroys the very checkbox whose ``toggled``
        signal we are standing in — Qt does not survive that. By the time the
        timer fires the signal has returned and the widget is safe to replace.
        """
        self._host.set_enabled(plugin_id, enabled)
        QTimer.singleShot(0, self._refresh_if_alive)

    def _refresh_if_alive(self) -> None:
        """``refresh`` unless the page was destroyed before the timer fired."""
        try:
            self.refresh()
        except RuntimeError:
            # The settings window went away with a refresh still queued; the
            # table it would redraw no longer exists, which is not a failure.
            logger.debug("plugin manager refresh skipped; page already gone")

    # --- table -------------------------------------------------------------
    def refresh(self) -> None:
        rows = self._host.statuses()
        # Resolved once per refresh: a registry the user has since removed
        # simply won't be in here, which is what makes the display fall back.
        names = {r.url.lower(): r.name for r in self._host.registries()}
        offers = updates_by_id(self._updates)
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
                    lambda checked, pid=plugin_id: self._toggle_enabled(pid, checked)
                )
            self._table.setCellWidget(row_index, 0, enabled_box)
            version = loaded.meta.version if loaded.meta is not None else ""
            status = STATUS_LABELS.get(loaded.status, loaded.status)
            registry_url = entry.registry_url if entry is not None else ""
            offer = offers.get(plugin_id or "")
            status += update_suffix(offer)
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
                (_PERFORMANCE_COLUMN, ""),
                (_LOCATION_COLUMN, loaded.source.location),
                (_SOURCE_COLUMN, source_text),
            ):
                item = QTableWidgetItem(text)
                if column == 3 and (loaded.error or dropped):
                    item.setToolTip(dropped or loaded.error or "")
                elif column == _SOURCE_COLUMN:
                    item.setToolTip(source_tip)
                self._table.setItem(row_index, column, item)
            self._table.setCellWidget(row_index, _UPDATE_COLUMN, self._update_cell(offer))
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
                (_PERFORMANCE_COLUMN, "—"),
                (_LOCATION_COLUMN, location),
                (_SOURCE_COLUMN, source_text),
            ):
                item = QTableWidgetItem(text)
                if column == _SOURCE_COLUMN:
                    item.setToolTip(source_tip)
                self._table.setItem(row_index, column, item)
        # Through the same method the timer uses, so the cell a user sees
        # first and the cell they see a second later come from one place.
        self._refresh_stats()
        self._refresh_status()
        self._refresh_update_all()

    # --- performance (#132) -------------------------------------------------
    def _refresh_stats(self) -> None:
        """Repaint only the Performance cells of the plugin rows.

        Deliberately not a ``refresh()``: that rebuilds every row, destroying
        the Enabled checkbox and Update button as it goes, which is not
        something to do under the user's cursor once a second for a number
        nothing depends on. Session-install rows are skipped — they have not
        run, so they have nothing to report.

        Every string here comes from ``core.plugins.telemetry``, so what the
        cell says is tested without a window.
        """
        rows = self._host.statuses()
        collecting = self._host.telemetry_enabled
        for row_index, loaded in enumerate(rows):
            item = self._table.item(row_index, _PERFORMANCE_COLUMN)
            if item is None:
                continue
            plugin_id = loaded.plugin_id
            # Only a RUNNING add-on gets numbers. A disabled one keeps its
            # record on the collector (a re-enable resets it), but showing
            # the last run's figures on a row that says "Disabled" reads as
            # a plugin still doing work.
            running = plugin_id is not None and loaded.status == "active"
            snapshot = self._host.stats_for(plugin_id) if running and plugin_id else None
            item.setText(format_note(snapshot, collecting=collecting))
            item.setToolTip(format_tooltip(snapshot))

    def _tick_stats(self) -> None:
        """Timer slot; silent about a page the settings window already closed."""
        try:
            self._refresh_stats()
        except RuntimeError:
            logger.debug("plugin manager stats tick skipped; page already gone")

    def _set_telemetry(self, enabled: bool) -> None:
        self._host.set_telemetry(enabled)
        self._refresh_stats()

    def _refresh_update_all(self) -> None:
        """Label carries the count; disabled when there is nothing to take."""
        takeable = [
            update
            for update in same_source_updates(self._updates)
            if update.installed_path is not None
        ]
        self._update_all_button.setText(
            f"Update all ({len(takeable)})" if takeable else "Update all"
        )
        self._update_all_button.setEnabled(bool(takeable) and not self._batch_active)

    def _update_cell(self, update: PluginUpdate | None) -> QWidget | None:
        """The per-row Update button, or nothing when there is no offer."""
        if update is None:
            return None
        button = QPushButton(f"Update to v{update.offered_version}", self)
        if update.needs_confirmation:
            # The ellipsis is the promise that a dialog comes first — the
            # same contract every other "…" button on this page keeps.
            button.setText(f"Update to v{update.offered_version}…")
            button.setToolTip(
                f"Offered by {update.source_name}, which is not where your copy "
                "came from. nParse+ will ask you to confirm."
            )
        else:
            button.setToolTip(
                f"Replaces v{update.installed_version} in place. Your consent and "
                "this plugin's stored data are kept; the old version is moved to "
                "the plugins trash folder."
            )
        button.setEnabled(update.installed_path is not None)
        if update.installed_path is None:
            # Entry-point plugins live in site-packages — pip owns them.
            button.setToolTip("This plugin was installed by pip; update it there.")
        button.clicked.connect(lambda _checked=False, item=update: self._update_one(item))
        return button

    # --- update checks -------------------------------------------------------
    def start_update_check(self) -> None:
        """Fetch every enabled registry and declared feed on a worker thread.

        Single-flight: a second click while one is in the air is a no-op,
        matching the Browse dialog and the app's own "Check now".
        """
        if self._checking:
            return
        self._checking = True
        self._check_button.setEnabled(False)
        self._status.setText("Checking for plugin updates…")

        def worker() -> None:
            try:
                result = check_for_updates(
                    self._host.installed_for_update_check(),
                    self._host.enabled_registries(),
                    sdk_version=self._host.sdk_version,
                    app_version=self._app_version,
                )
            except Exception:  # never strand the disabled button on a thread crash
                logger.exception("plugin update check failed")
                result = None
            self._check_finished.emit(result)

        threading.Thread(target=worker, name="plugin-update-check", daemon=True).start()

    def _on_check_finished(self, result: UpdateCheckResult | None) -> None:
        self._checking = False
        self._check_button.setEnabled(True)
        if result is None:
            self._status.setText("Could not check for updates — see nparseplus.log.")
            return
        self._host.cache_update_check(result)
        self.refresh()

    def _set_listings(self, result: MultiFetchResult) -> None:
        """Take Browse's fetch as an update check too.

        Browse already paid for the round trip, so re-deriving the offers from
        it keeps the two surfaces from disagreeing about what is available.
        It only covers registries — a Browse fetch never touches a plugin's
        own feed — so any self-published offers from an earlier check are
        carried forward rather than silently dropped.
        """
        previous = self._check
        carried = [u for u in self._updates if u.listing.registry.is_self_published]
        registry_updates = pending_updates(
            self._host.installed_for_update_check(),
            result.listings,
            sdk_version=self._host.sdk_version,
            app_version=self._app_version,
        )
        offered = {update.plugin_id for update in registry_updates}
        merged = UpdateCheckResult(
            fetched=result,
            updates=[*registry_updates, *(u for u in carried if u.plugin_id not in offered)],
            self_feeds=previous.self_feeds if previous is not None else [],
        )
        self._host.cache_update_check(merged)
        self.refresh()

    def _refresh_status(self) -> None:
        """The line under the buttons: fetch failures, then the offer count."""
        check = self._check
        lines = list(check.summary_lines()) if check is not None else []
        pending = self._updates
        if check is None:
            lines.append("No update check has run yet this session.")
        elif not pending:
            lines.append("Every installed add-on is up to date.")
        else:
            blocked = len(pending) - len(same_source_updates(pending))
            text = f"{len(pending)} update{'s' if len(pending) != 1 else ''} available."
            if blocked:
                text += (
                    f" {blocked} come{'s' if blocked == 1 else ''} from a different "
                    "source than the copy you have — update those one at a time."
                )
            lines.append(text)
        self._status.setText("\n".join(lines))

    def installed_index(self) -> dict[str, InstalledPlugin]:
        """Installed plugin id -> what the browser needs to decide its row.

        A registry_url of "" means nothing vouched for this copy (sideloaded,
        or installed from a bare URL or file) — a different statement from
        "installed from somewhere else", and the browser treats it as one.

        Plugins installed this session are folded in on top of the host's
        view, so a listing does not offer to install something the user just
        installed from the same dialog.
        """
        index = {plugin.plugin_id: plugin for plugin in self._host.installed_for_update_check()}
        for result in self._session_installs:
            if not result.ok or result.meta is None:
                continue
            entry = self._host.entry_for(result.meta.id)
            index[result.meta.id] = InstalledPlugin(
                plugin_id=result.meta.id,
                version=result.meta.version,
                registry_url=entry.registry_url if entry is not None else "",
                installed_path=result.installed_path,
                update_url=result.meta.update_url,
            )
        return index

    def installed_provenance(self) -> dict[str, str]:
        """Installed plugin id -> the registry URL that vouched for it."""
        return {pid: plugin.registry_url for pid, plugin in self.installed_index().items()}

    def installed_ids(self) -> set[str]:
        """Plugin ids present on disk (loaded or installed this session)."""
        return set(self.installed_index())

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

    # --- taking an update ----------------------------------------------------
    def _update_one(self, update: PluginUpdate) -> None:
        """Install one update, confirming first if the source changed."""
        if update.needs_confirmation:
            box = QMessageBox(self)
            box.setWindowTitle("Update from a different source?")
            # PlainText, not the QMessageBox default of AutoText: this body
            # carries a registry's display name and the author's release
            # notes, and Qt::AutoText would hand anything tag-shaped in
            # either of them to a rich-text renderer. Text is shown as text.
            box.setTextFormat(Qt.TextFormat.PlainText)
            box.setText(update_confirm_text(update))
            box.setStandardButtons(
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
            )
            box.setDefaultButton(QMessageBox.StandardButton.Cancel)
            if box.exec() != QMessageBox.StandardButton.Yes:
                return
        self._start_update(update)

    def _start_update(self, update: PluginUpdate) -> None:
        if update.installed_path is None:
            return
        release = update.listing.plugin.latest
        self._pending_update = update
        self._pending_registry_url = (
            # A self-published feed vouches for nothing, so it must not be
            # recorded as the registry that did — that would make the next
            # update from it look like it came from a source the user chose.
            "" if update.listing.registry.is_self_published else update.listing.registry.url
        )
        target = ReplaceTarget(
            plugin_id=update.plugin_id, installed_path=Path(update.installed_path)
        )
        self._start_install(
            lambda: install_from_url(
                release.url,
                self._host.plugins_dir,
                app_version=self._app_version,
                expected_sha256=release.sha256,
                replace=target,
            )
        )

    def _update_all(self) -> None:
        """Take every same-source update, one at a time.

        Serial on the one existing worker deliberately: staging and the
        download temp file are fixed paths inside the plugins folder, so
        concurrent installs would race on them — and each install imports and
        activates third-party code, which is not something to do in parallel
        on a whim.
        """
        pending = same_source_updates(self._updates)
        pending = [update for update in pending if update.installed_path is not None]
        if not pending or self._batch_active:
            return
        self._batch_active = True
        self._update_results = []
        self._update_queue = list(pending)
        self._start_next_update()

    def _start_next_update(self) -> None:
        if not self._update_queue:
            self._finish_batch()
            return
        self._start_update(self._update_queue.pop(0))

    def _finish_batch(self) -> None:
        """One summary for the whole batch, not a dialog per plugin."""
        results, self._update_results = self._update_results, []
        self._batch_active = False
        done = [(u, r) for u, r in results if r.ok]
        failed = [(u, r) for u, r in results if not r.ok]
        lines = [f"Updated {len(done)} of {len(results)} add-ons."]
        if done:
            lines.append("Restart nParse+ to load them.")
            lines.append("")
            lines += [
                f"• {u.plugin_id} {u.installed_version} → {u.offered_version}" for u, _ in done
            ]
            lines.append("")
            lines.append("The previous versions were moved to the plugins trash folder.")
        if failed:
            lines.append("")
            lines.append("Failed (the installed version is unchanged):")
            lines += [
                f"• {u.plugin_id} → {u.offered_version} — "
                f"{(r.errors[0] if r.errors else 'unknown error')}"
                for u, r in failed[:_MAX_REPORTED_UPDATE_FAILURES]
            ]
            if len(failed) > _MAX_REPORTED_UPDATE_FAILURES:
                lines.append(
                    f"• +{len(failed) - _MAX_REPORTED_UPDATE_FAILURES} more — see nparseplus.log"
                )
        advisories = sum(len(r.warnings) for _u, r in done)
        if advisories:
            # The full text would be a wall across a batch; the per-row
            # tooltip and the log still carry it.
            lines.append("")
            lines.append(
                f"{advisories} advisory finding{'s' if advisories != 1 else ''} across the "
                "updated add-ons — see nparseplus.log."
            )
        QMessageBox.information(self, "Plugins updated", "\n".join(lines))

    def _browse_registry(self) -> None:
        dialog = RegistryBrowserDialog(
            self._host,
            self._app_version,
            on_install=self._start_url_install,
            on_index=self._set_listings,
            on_update=self._update_from_listing,
            installed=self.installed_index,
            parent=self,
        )
        dialog.exec()

    def _update_from_listing(self, merged: MergedListing, current: InstalledPlugin) -> None:
        """Take an update the browser offered.

        Routed through pending_updates rather than hand-building a
        PluginUpdate, so a Browse row and a table row reach exactly the same
        same-source verdict — and therefore the same confirmation, or none.
        """
        offers = pending_updates(
            [current],
            [merged],
            sdk_version=self._host.sdk_version,
            app_version=self._app_version,
        )
        if offers:
            self._update_one(offers[0])

    def _on_install_finished(self, result: InstallResult) -> None:
        self._set_install_buttons_enabled(True)
        # Both stashes are popped together and stay single-valued: installs
        # are strictly one at a time, and the update batch preserves that by
        # queueing rather than by widening these.
        registry_url, self._pending_registry_url = self._pending_registry_url, ""
        update, self._pending_update = self._pending_update, None
        adopted = None
        if result.ok:
            self._host.record_install(result, registry_url=registry_url)
            if update is None:
                # A fresh install can load now (#45): nothing has imported
                # this file yet, so there is no stale module to fight. An
                # UPDATE cannot — re-importing in-session leaves the old
                # objects live and its submodules stale — so it keeps the
                # restart notice and stays a session-install row.
                adopted = self._adopt_installed(result)
                if adopted is None:
                    self._session_installs.append(result)
            else:
                self._drop_taken_update(update.plugin_id)
        if update is not None and self._batch_active:
            # Each update rolls back on its own, so one failure never stops
            # the rest — the summary at the end names both lists.
            self._update_results.append((update, result))
            self.refresh()
            self._start_next_update()
            return
        if result.ok:
            name = result.meta.name if result.meta is not None else "Plugin"
            if update is None:
                lines = [install_outcome_text(name, adopted)]
            else:
                lines = [
                    f"{name} updated to v{update.offered_version}. It will load the "
                    "next time nParse+ starts.",
                    "",
                    "Your settings and this plugin's stored data were kept; the "
                    "previous version was moved to the plugins trash folder.",
                ]
            if result.warnings:
                lines.append("")
                lines.append("Advisory findings (not a security guarantee):")
                lines.extend(f"• {w}" for w in result.warnings[:12])
            QMessageBox.information(
                self, "Plugin updated" if update else "Plugin installed", "\n".join(lines)
            )
        else:
            QMessageBox.warning(
                self,
                "Update failed" if update else "Install failed",
                "\n".join(result.errors) or "Unknown error",
            )
        self.refresh()

    def _adopt_installed(self, result: InstallResult) -> LoadedPlugin | None:
        """Load a just-installed plugin now: classify, consent, activate.

        The install half of #45. Consent is unchanged and non-negotiable —
        ``record_install`` wrote an unapproved entry, so the plugin arrives
        ``pending_consent`` and the same first-load dialog runs here that
        would have run at the next launch. Declining leaves it installed and
        disabled, which is a load answered, not a load failed.

        Returns the row, whatever state it ended in — declined, incompatible,
        duplicate or failed are all outcomes the user has to be told about
        accurately, and only the row knows which happened. None means the
        plugin could not be adopted at all (an entry-point plugin, an
        unreadable path), so the caller falls back to the session-install row
        and its restart notice.
        """
        from nparseplus.ui.pluginconsent import run_consent_prompts

        if result.installed_path is None:
            return None
        loaded = self._host.adopt_installed(Path(result.installed_path))
        if loaded is None or loaded.plugin_id is None:
            return None
        if loaded.status == "pending_consent":
            run_consent_prompts(self._host, self.consent_ask)
        if loaded.status == "ready":
            self._host.activate_one(loaded.plugin_id)
        return loaded

    def _drop_taken_update(self, plugin_id: str) -> None:
        """Retire an offer once taken, so the row stops advertising it.

        The plugin on disk is now the new version, but the loaded metadata
        still says otherwise until a restart — so the offer has to be removed
        explicitly rather than recomputed.
        """
        check = self._check
        if check is None:
            return
        remaining = [u for u in check.updates if u.plugin_id != plugin_id]
        if len(remaining) == len(check.updates):
            return
        self._host.cache_update_check(
            UpdateCheckResult(fetched=check.fetched, updates=remaining, self_feeds=check.self_feeds)
        )

    def _set_install_buttons_enabled(self, enabled: bool) -> None:
        self._install_file_button.setEnabled(enabled)
        self._install_url_button.setEnabled(enabled)

    def _uninstall_selected(self) -> None:
        row = self._table.currentRow()
        if row < 0:
            return
        location_item = self._table.item(row, _LOCATION_COLUMN)
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
                f"{name} was moved to the trash folder.",
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
    listings that claim the same plugin id. Installs and updates delegate
    back to the manager page's worker with the listing's pinned sha256.
    """

    _index_ready = Signal(object)  # MultiFetchResult, from the fetch worker

    def __init__(
        self,
        host: PluginHost,
        app_version: str,
        *,
        on_install,
        on_index=None,
        on_update=None,
        installed=None,
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
        self._on_update = on_update or (lambda _merged, _current: None)
        # id -> InstalledPlugin. Carries the version and the path as well as
        # the provenance, because a row now has to decide between "Installed"
        # and "Update to vX" rather than just whether to grey the button out.
        self._installed = installed or (lambda: {})
        self._fetching = False
        self._listings: list[MergedListing] = []

        self._status = QLabel("Fetching the plugin registries…", self)
        self._status.setWordWrap(True)
        self._table = QTableWidget(0, len(_BROWSER_COLUMNS), self)
        self._table.setHorizontalHeaderLabels(_BROWSER_COLUMNS)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setVisible(False)
        self._table.itemSelectionChanged.connect(self._show_selected_notes)
        # The selected release's notes. A read-only QPlainTextEdit rather
        # than a label or a tooltip because it is the one Qt sink that can
        # never interpret what it is given: the registry promises this field
        # is plain text and carries whatever the author wrote, so a widget
        # that auto-detects rich text would be rendering markup from a
        # publish request. It scrolls, which a 2 KiB note in a label would
        # not, and it costs no height when there is nothing to show.
        self._notes = QPlainTextEdit(self)
        self._notes.setReadOnly(True)
        self._notes.setMaximumHeight(84)
        self._notes.setVisible(False)
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
        layout.addWidget(self._notes)
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
            self._listings = []
            self._show_selected_notes()
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
        installed = self._installed()
        duplicates = duplicate_listing_ids(listings)
        self._table.setRowCount(len(listings))
        self._listings = list(listings)
        for row, merged in enumerate(listings):
            self._fill_row(row, merged, listings, duplicates, installed)
        self._show_selected_notes()

    def _fill_row(
        self,
        row: int,
        merged: MergedListing,
        listings: Sequence[MergedListing],
        duplicates: set[str],
        installed: dict[str, InstalledPlugin],
    ) -> None:
        listing = merged.plugin
        current = installed.get(listing.id)
        action = listing_action(
            merged,
            installed_version=current.version if current is not None else "",
            installed_registry_url=current.registry_url if current is not None else "",
            is_installed=current is not None,
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
            (_BROWSER_COMPAT_COLUMN, "OK" if action.kind != "incompatible" else action.tooltip),
        ):
            item = QTableWidgetItem(text)
            if column == _BROWSER_NAME_COLUMN and listing.description:
                item.setToolTip(listing.description)
            elif column == _BROWSER_SOURCE_COLUMN:
                item.setToolTip(source_tip)
            self._table.setItem(row, column, item)
        self._table.setCellWidget(
            row, _BROWSER_ACTION_COLUMN, self._action_button(merged, action, current)
        )

    def _show_selected_notes(self) -> None:
        """Show the selected listing's release notes, or nothing.

        Notes are optional and most listings will not carry any until the
        registry starts serving them, so an empty one leaves the pane hidden
        rather than showing an empty box.
        """
        row = self._table.currentRow()
        merged = self._listings[row] if 0 <= row < len(self._listings) else None
        notes = merged.plugin.latest.notes.strip() if merged is not None else ""
        if not notes:
            self._notes.clear()
            self._notes.setVisible(False)
            return
        self._notes.setPlainText(f"What's new in v{merged.plugin.latest.version}\n\n{notes}")
        self._notes.setVisible(True)

    def _action_button(
        self,
        merged: MergedListing,
        action: ListingAction,
        current: InstalledPlugin | None,
    ) -> QPushButton:
        """Render one decided action. All six kinds come from listing_action,
        so this stays a renderer and the browser cannot drift from the table.
        """
        button = QPushButton(action.label, self)
        button.setEnabled(action.enabled)
        if action.tooltip:
            button.setToolTip(action.tooltip)
        if action.kind == "install":
            button.clicked.connect(lambda _checked=False, item=merged: self._install(item))
        elif action.kind in ("update", "update_other_source"):
            if current is None or current.installed_path is None:
                # Installed by pip, so there is nothing here to replace.
                button.setEnabled(False)
                button.setToolTip("This plugin was installed by pip; update it there.")
            else:
                button.clicked.connect(
                    lambda _checked=False, item=merged, now=current: self._update(item, now)
                )
        return button

    def _install(self, merged: MergedListing) -> None:
        self._on_install(merged.plugin.latest.url, merged.plugin.latest.sha256, merged.registry.url)
        self.accept()

    def _update(self, merged: MergedListing, current: InstalledPlugin) -> None:
        self._on_update(merged, current)
        self.accept()


def plugin_manager_page_spec(host: PluginHost, app_version: str) -> SettingsPageSpec:
    """The Plugins page contribution for UnifiedSettingsWindow extra_pages."""
    return SettingsPageSpec(
        "Plugins",
        lambda parent: PluginManagerPage(host, app_version, parent),
    )
