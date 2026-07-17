"""Update-details dialog with all release notes crossed by an upgrade."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from nparseplus.updater import ReleaseInfo, format_release_notes


class UpdateAvailableDialog(QDialog):
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
