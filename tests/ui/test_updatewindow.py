"""Update dialog release-history rendering and actions."""

import pytest
from PySide6.QtWidgets import QDialog

from nparseplus.ui.updatewindow import UpdateAvailableDialog
from nparseplus.updater import ReleaseInfo, ReleaseNote

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
