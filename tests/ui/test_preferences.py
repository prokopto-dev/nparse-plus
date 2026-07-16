"""Preferences window tests."""

from pathlib import Path

import pytest

from nparseplus.config.settings import Settings
from nparseplus.ui.preferences import PreferencesWindow

pytestmark = pytest.mark.qt


def test_apply_writes_settings_and_notifies(qtbot, tmp_path: Path) -> None:
    settings = Settings()
    saves: list[int] = []
    dir_changes: list[Path] = []
    window = PreferencesWindow(
        settings,
        on_save=lambda: saves.append(1),
        on_log_dir_changed=dir_changes.append,
    )
    qtbot.addWidget(window)

    window._log_dir.edit.setText(str(tmp_path))
    window._font_size.setValue(15)
    window._overlay_seconds.setValue(8.0)
    window._ch_retention.setValue(45.0)
    window._archive_enabled.setChecked(True)
    window._archive_mb.setValue(50)
    window._sharing_mode.setCurrentText("off")
    window.apply()

    assert settings.general.eq_log_dir == tmp_path
    assert settings.general.font_size == 15
    assert settings.general.overlay_text_seconds == 8.0
    assert settings.general.ch_lane_retention_seconds == 45.0
    assert settings.general.log_archive_enabled is True
    assert settings.general.log_archive_size_mb == 50
    assert settings.sharing.mode == "off"
    assert saves == [1]
    assert dir_changes == [tmp_path]


def test_apply_without_log_dir_change_skips_notify(qtbot) -> None:
    settings = Settings()
    dir_changes: list[Path] = []
    window = PreferencesWindow(
        settings, on_save=lambda: None, on_log_dir_changed=dir_changes.append
    )
    qtbot.addWidget(window)
    window.apply()
    assert dir_changes == []
