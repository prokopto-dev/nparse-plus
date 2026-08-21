"""Update dialog release-history rendering and actions."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QMessageBox

from nparseplus.flatpakportal import PortalOutcome, PortalProgress, PortalStatus
from nparseplus.ui.updatewindow import (
    DownloadOutcomeDialog,
    PortalOutcomeDialog,
    PortalUpdateDialog,
    UpdateAvailableDialog,
)
from nparseplus.updater import DownloadOutcome, DownloadStatus, ReleaseInfo, ReleaseNote

pytestmark = pytest.mark.qt


def _release() -> ReleaseInfo:
    return ReleaseInfo(
        version="1.6.0",
        html_url="https://example/releases/1.6.0",
        notes=(
            ReleaseNote(version="1.6.0", body="- Added desktop layouts."),
            ReleaseNote(version="1.5.1", body="- Fixed PigParse reconnects."),
        ),
    )


def test_update_dialog_shows_all_crossed_versions(qtbot) -> None:
    dialog = UpdateAvailableDialog(_release(), "1.4.0")
    qtbot.addWidget(dialog)
    text = dialog.notes.toPlainText()
    assert "Version 1.6.0" in text
    assert "Version 1.5.1" in text
    assert "desktop layouts" in text
    assert "PigParse reconnects" in text


def test_update_dialog_actions_emit(qtbot) -> None:
    dialog = UpdateAvailableDialog(_release(), "1.4.0")
    qtbot.addWidget(dialog)
    with qtbot.waitSignal(dialog.open_release_requested):
        dialog.release_button.click()
    with qtbot.waitSignal(dialog.install_requested):
        dialog.install_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted


def _refusal() -> DownloadOutcome:
    return DownloadOutcome(
        status=DownloadStatus.DIGEST_MISMATCH,
        asset_name="nParse+-9.9.9-macos-arm64.dmg",
        detail="checksum mismatch: expected sha256 aaa, got bbb — refusing to install",
    )


def test_refused_download_says_the_checksum_did_not_match(qtbot) -> None:
    dialog = DownloadOutcomeDialog(_refusal())
    qtbot.addWidget(dialog)
    assert "refused" in dialog.text().lower()
    body = dialog.informativeText()
    assert "did not match the checksum" in body
    assert "nParse+-9.9.9-macos-arm64.dmg" in body
    assert dialog.icon() == QMessageBox.Icon.Critical
    # The digests stay in the details drawer — quotable, not in the user's face.
    assert "expected sha256 aaa" in dialog.detailedText()


def test_a_network_failure_does_not_read_as_a_verification_failure(qtbot) -> None:
    dialog = DownloadOutcomeDialog(
        DownloadOutcome(status=DownloadStatus.FAILED, asset_name="a.dmg", opened_release_page=True)
    )
    qtbot.addWidget(dialog)
    assert "failed" in dialog.text().lower()
    assert "network" in dialog.informativeText()
    assert "checksum" not in dialog.informativeText()
    assert dialog.icon() == QMessageBox.Icon.Warning


def test_an_unverifiable_download_is_not_reported_as_corrupt(qtbot) -> None:
    # A release from before GitHub published per-asset digests: distinct
    # wording, distinct severity, and it never claims the file is wrong.
    dialog = DownloadOutcomeDialog(
        DownloadOutcome(status=DownloadStatus.OK, asset_name="a.dmg", pinned=False)
    )
    qtbot.addWidget(dialog)
    assert "not verified" in dialog.text().lower()
    assert "no checksum" in dialog.informativeText()
    assert "did not match" not in dialog.informativeText()


def test_the_release_page_is_offered_not_taken(qtbot) -> None:
    dialog = DownloadOutcomeDialog(_refusal())
    qtbot.addWidget(dialog)
    with qtbot.waitSignal(dialog.open_release_requested):
        dialog.release_button.click()


# -- Flatpak in-place update (#74) ---------------------------------------------------


def test_the_button_says_install_inside_a_flatpak(qtbot) -> None:
    """A one-click install must not be labelled as a 200 MB download."""
    download = UpdateAvailableDialog(_release(), "1.4.0")
    in_place = UpdateAvailableDialog(_release(), "1.4.0", in_place=True)
    qtbot.addWidget(download)
    qtbot.addWidget(in_place)
    assert download.install_button.text() == "Download Update"
    assert in_place.install_button.text() == "Install Update"


def test_the_progress_dialog_renders_a_portal_signal(qtbot) -> None:
    dialog = PortalUpdateDialog("9.9.9")
    qtbot.addWidget(dialog)
    dialog.report(PortalProgress(n_ops=3, op=2, percent=42))
    assert dialog.bar.value() == 42
    assert dialog.status.text() == "Step 2 of 3 — 42%"


def test_an_installed_update_offers_a_restart(qtbot) -> None:
    dialog = PortalOutcomeDialog(PortalOutcome(status=PortalStatus.OK, version="9.9.9"))
    qtbot.addWidget(dialog)
    assert dialog.restart_button is not None
    assert dialog.release_button is None
    assert "9.9.9" in dialog.informativeText()
    with qtbot.waitSignal(dialog.restart_requested):
        dialog.restart_button.click()


def test_a_permission_widening_update_names_the_terminal_command(qtbot) -> None:
    dialog = PortalOutcomeDialog(
        PortalOutcome(status=PortalStatus.NOT_SUPPORTED, version="9.9.9", detail="NotSupported")
    )
    qtbot.addWidget(dialog)
    assert "flatpak update io.github.prokopto_dev.nparse_plus" in dialog.informativeText()
    # Nothing was installed, so there is no restart to offer — but the release
    # page is still a route, unlike after a refused download.
    assert dialog.restart_button is None
    with qtbot.waitSignal(dialog.open_release_requested):
        dialog.release_button.click()


def test_the_portal_dialog_never_renders_its_text_as_markup(qtbot) -> None:
    """The portal's own error string reaches this dialog; it is not markup."""
    dialog = PortalOutcomeDialog(
        PortalOutcome(status=PortalStatus.FAILED, detail="<b>ostree</b> pull failed")
    )
    qtbot.addWidget(dialog)
    assert dialog.textFormat() == Qt.TextFormat.PlainText
    assert "<b>ostree</b>" in dialog.detailedText()
