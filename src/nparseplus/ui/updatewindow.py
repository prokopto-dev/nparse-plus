"""Update-details dialog with all release notes crossed by an upgrade."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from nparseplus.ui import chromewidgets
from nparseplus.updater import DownloadOutcome, DownloadStatus, ReleaseInfo, format_release_notes


class UpdateAvailableDialog(chromewidgets.ChromeMixin, QDialog):
    install_requested = Signal()
    open_release_requested = Signal()

    def __init__(
        self, release: ReleaseInfo, installed_version: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.release = release
        self.setWindowTitle("nParse+ Update Available")
        self.setMinimumSize(680, 520)

        layout = QVBoxLayout(self)
        heading = QLabel(
            f"<h2>nParse+ {release.version} is available</h2>"
            f"<p>Installed version: {installed_version}</p>",
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
        self.install_button = QPushButton("Download Update", self)
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
