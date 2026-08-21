"""Update dialogs: the release notes, a download outcome, and the Flatpak
in-place install (#74).

Every one of these is a renderer. The wording for a download lives on
:class:`nparseplus.updater.DownloadOutcome` and the wording for an in-place
install on :class:`nparseplus.flatpakportal.PortalOutcome` — both Qt-free and
tested without a window.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from nparseplus.flatpakportal import PortalOutcome, PortalProgress, PortalStatus
from nparseplus.ui import chromewidgets
from nparseplus.updater import DownloadOutcome, DownloadStatus, ReleaseInfo, format_release_notes


class UpdateAvailableDialog(chromewidgets.ChromeMixin, QDialog):
    install_requested = Signal()
    open_release_requested = Signal()

    def __init__(
        self,
        release: ReleaseInfo,
        installed_version: str,
        parent: QWidget | None = None,
        *,
        in_place: bool = False,
    ) -> None:
        super().__init__(parent)
        self.release = release
        self.in_place = in_place
        self.setWindowTitle("nParse+ Update Available")
        self.setMinimumSize(680, 520)

        layout = QVBoxLayout(self)
        # Inside a Flatpak the button installs rather than downloads, so it
        # says so — "Download Update" for a one-click install would be a lie
        # about a ~200 MB file the user never sees.
        note = (
            "<p>This will install the update in place through Flatpak and offer to restart.</p>"
            if in_place
            else ""
        )
        heading = QLabel(
            f"<h2>nParse+ {release.version} is available</h2>"
            f"<p>Installed version: {installed_version}</p>{note}",
            self,
        )
        layout.addWidget(heading)

        self.notes = QTextBrowser(self)
        self.notes.setOpenExternalLinks(True)
        self.notes.setMarkdown(format_release_notes(release))
        layout.addWidget(self.notes, 1)

        buttons = QHBoxLayout()
        self.release_button = QPushButton("View on GitHub", self)
        self.release_button.clicked.connect(self.open_release_requested.emit)
        buttons.addWidget(self.release_button)
        buttons.addStretch(1)
        self.later_button = QPushButton("Later", self)
        self.later_button.clicked.connect(self.reject)
        buttons.addWidget(self.later_button)
        self.install_button = QPushButton("Install Update" if in_place else "Download Update", self)
        self.install_button.setDefault(True)
        self.install_button.clicked.connect(self._request_install)
        buttons.addWidget(self.install_button)
        layout.addLayout(buttons)

    def _request_install(self) -> None:
        self.install_requested.emit()
        self.accept()


# A refused download is the loudest of these on purpose: it is the one case
# where something is actually wrong with the artifact rather than with the
# network or the release.
_ICONS = {
    DownloadStatus.DIGEST_MISMATCH: QMessageBox.Icon.Critical,
    DownloadStatus.SIZE_MISMATCH: QMessageBox.Icon.Critical,
    DownloadStatus.REFUSED: QMessageBox.Icon.Critical,
    DownloadStatus.FAILED: QMessageBox.Icon.Warning,
    DownloadStatus.UNAVAILABLE: QMessageBox.Icon.Information,
}


class DownloadOutcomeDialog(chromewidgets.ChromeMixin, QMessageBox):
    """Says why an update download did not simply install (#93).

    A pure renderer: every word comes from :class:`DownloadOutcome`, which is
    Qt-free and tested without a window. The technical line (both digests,
    the transport error) goes into the details drawer rather than the body —
    it is what a bug report quotes and what nobody else needs to read.

    The release-page button is offered rather than taken: for a refusal that
    page points at the artifact that was just refused, so opening it silently
    was the bug this dialog exists to fix.
    """

    open_release_requested = Signal()

    def __init__(self, outcome: DownloadOutcome, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.outcome = outcome
        self.setWindowTitle("nParse+ Update")
        self.setIcon(_ICONS.get(outcome.status, QMessageBox.Icon.Information))
        self.setText(outcome.title())
        self.setInformativeText(outcome.message())
        if outcome.detail:
            self.setDetailedText(outcome.detail)
        self.release_button = self.addButton("Open Release Page", QMessageBox.ButtonRole.ActionRole)
        self.release_button.clicked.connect(self.open_release_requested.emit)
        self.close_button = self.addButton(QMessageBox.StandardButton.Close)
        self.setDefaultButton(self.close_button)
        self.apply_chrome()


class PortalUpdateDialog(chromewidgets.ChromeMixin, QDialog):
    """Progress while the Flatpak portal installs the update in place (#74).

    Deliberately not modal and deliberately dismissable: the install runs on a
    worker thread and finishes whether or not this is on screen, so trapping
    the user in front of a progress bar for a multi-minute ostree pull buys
    nothing. Hiding it leaves the outcome dialog to do the reporting.
    """

    def __init__(self, version: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("nParse+ Update")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<h3>Installing nParse+ {version}</h3>", self))
        self.bar = QProgressBar(self)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        layout.addWidget(self.bar)
        self.status = QLabel("Starting…", self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.hide_button = QPushButton("Hide", self)
        self.hide_button.clicked.connect(self.hide)
        buttons.addWidget(self.hide_button)
        layout.addLayout(buttons)
        self.apply_chrome()

    def report(self, progress: PortalProgress) -> None:
        """Render one ``UpdateMonitor.Progress`` signal."""
        self.bar.setValue(progress.percent)
        self.status.setText(progress.label())


# OK is the only status that is not a problem; ALREADY_CURRENT is a "come back
# in a minute", which is information rather than a warning.
_PORTAL_ICONS = {
    PortalStatus.OK: QMessageBox.Icon.Information,
    PortalStatus.ALREADY_CURRENT: QMessageBox.Icon.Information,
    PortalStatus.NOT_SUPPORTED: QMessageBox.Icon.Warning,
    PortalStatus.FAILED: QMessageBox.Icon.Warning,
}


class PortalOutcomeDialog(chromewidgets.ChromeMixin, QMessageBox):
    """What the Flatpak portal did, and the one action that follows from it.

    A pure renderer of :class:`~nparseplus.flatpakportal.PortalOutcome`, like
    :class:`DownloadOutcomeDialog` beside it. The text format is pinned to
    plain: the informative text carries a shell command for the
    permission-widening case and the details drawer carries the portal's own
    error string, and neither should ever reach a rich-text renderer.
    """

    restart_requested = Signal()
    open_release_requested = Signal()

    def __init__(self, outcome: PortalOutcome, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.outcome = outcome
        self.setWindowTitle("nParse+ Update")
        self.setIcon(_PORTAL_ICONS.get(outcome.status, QMessageBox.Icon.Information))
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setText(outcome.title())
        self.setInformativeText(outcome.message())
        if outcome.detail:
            self.setDetailedText(outcome.detail)

        self.restart_button = None
        self.release_button = None
        if outcome.can_relaunch:
            self.restart_button = self.addButton("Restart Now", QMessageBox.ButtonRole.AcceptRole)
            self.restart_button.clicked.connect(self.restart_requested.emit)
        else:
            # Nothing was installed, so the release page is still a route —
            # unlike a refused download, where it points at the bad artifact.
            self.release_button = self.addButton(
                "Open Release Page", QMessageBox.ButtonRole.ActionRole
            )
            self.release_button.clicked.connect(self.open_release_requested.emit)
        self.close_button = self.addButton(
            "Later" if outcome.can_relaunch else "Close", QMessageBox.ButtonRole.RejectRole
        )
        self.setDefaultButton(self.restart_button or self.close_button)
        self.apply_chrome()
