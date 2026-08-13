"""Update dialog release-history rendering and actions."""

import pytest
from PySide6.QtWidgets import QDialog, QMessageBox

from nparseplus.ui.updatewindow import DownloadOutcomeDialog, UpdateAvailableDialog
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
