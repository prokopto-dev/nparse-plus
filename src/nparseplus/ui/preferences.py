"""Preferences window — the new (Pydantic) settings surface.

Covers General (directories, updates, fonts, TTS, overlay durations, log
archiving) and Sharing. The legacy nparse Settings dialog still owns the
maps/discord appearance options until the maps window is rebuilt.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from nparseplus.audio.tts import default_speaker, list_voices
from nparseplus.config.settings import Settings, WindowState
from nparseplus.ui.overlaybase import OverlayWindowBase

WINDOW_KEY = "preferences"
DEFAULT_GEOMETRY = (280, 200, 460, 560)


class _DirPicker(QWidget):
    def __init__(self, caption: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._caption = caption
        self.edit = QLineEdit(self)
        button = QPushButton("…", self)
        button.setFixedWidth(28)
        button.clicked.connect(self._browse)
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.edit, 1)
        layout.addWidget(button, 0)
        self.setLayout(layout)

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, self._caption, self.edit.text())
        if path:
            self.edit.setText(path)

    def path(self) -> str:
        return self.edit.text().strip()


class PreferencesWindow(OverlayWindowBase):
    def __init__(
        self,
        settings: Settings,
        on_save: Callable[[], None],
        on_log_dir_changed: Callable[[Path], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            settings=settings,
            window_key=WINDOW_KEY,
            title="nParse+ Preferences",
            default_geometry=DEFAULT_GEOMETRY,
            on_save=on_save,
            default_state=WindowState(frameless=False, always_on_top=False),
            translucent=False,
            parent=parent,
        )
        self._on_log_dir_changed = on_log_dir_changed

        general = settings.general
        form = QFormLayout()

        self._log_dir = _DirPicker("Select EverQuest Logs directory", self)
        self._log_dir.edit.setText(str(general.eq_log_dir))
        form.addRow("EQ Logs directory", self._log_dir)

        self._install_dir = _DirPicker("Select EverQuest install directory", self)
        self._install_dir.edit.setText(str(general.eq_install_dir or ""))
        form.addRow("EQ install directory", self._install_dir)

        self._update_check = QCheckBox(self)
        self._update_check.setChecked(general.update_check)
        form.addRow("Check for updates", self._update_check)

        self._font_size = QSpinBox(self)
        self._font_size.setRange(6, 32)
        self._font_size.setValue(general.font_size)
        form.addRow("Font size", self._font_size)

        general_box = QGroupBox("General", self)
        general_box.setLayout(form)

        # --- audio ---------------------------------------------------------
        audio_form = QFormLayout()
        self._voice = QComboBox(self)
        self._voice.addItem("(system default)")
        for voice in list_voices():
            self._voice.addItem(voice)
        if general.tts_voice:
            index = self._voice.findText(general.tts_voice)
            if index < 0:
                self._voice.addItem(general.tts_voice)
                index = self._voice.count() - 1
            self._voice.setCurrentIndex(index)
        audio_form.addRow("TTS voice", self._voice)

        self._volume = QSlider(Qt.Orientation.Horizontal, self)
        self._volume.setRange(0, 100)
        self._volume.setValue(general.global_audio_volume)
        audio_form.addRow("Volume", self._volume)

        test_button = QPushButton("Test voice", self)
        test_button.clicked.connect(self._test_voice)
        audio_form.addRow("", test_button)

        audio_box = QGroupBox("Audio / TTS", self)
        audio_box.setLayout(audio_form)

        # --- overlays ------------------------------------------------------
        overlay_form = QFormLayout()
        self._overlay_seconds = QDoubleSpinBox(self)
        self._overlay_seconds.setRange(1.0, 30.0)
        self._overlay_seconds.setSingleStep(0.5)
        self._overlay_seconds.setValue(general.overlay_text_seconds)
        overlay_form.addRow("Alert text duration (s)", self._overlay_seconds)

        self._ch_retention = QDoubleSpinBox(self)
        self._ch_retention.setRange(5.0, 300.0)
        self._ch_retention.setSingleStep(5.0)
        self._ch_retention.setValue(general.ch_lane_retention_seconds)
        overlay_form.addRow("CH lane retention (s)", self._ch_retention)

        overlay_box = QGroupBox("Overlays", self)
        overlay_box.setLayout(overlay_form)

        # --- log archive ---------------------------------------------------
        archive_form = QFormLayout()
        self._archive_enabled = QCheckBox(self)
        self._archive_enabled.setChecked(general.log_archive_enabled)
        archive_form.addRow("Archive oversized logs", self._archive_enabled)
        self._archive_mb = QSpinBox(self)
        self._archive_mb.setRange(1, 4096)
        self._archive_mb.setSuffix(" MB")
        self._archive_mb.setValue(general.log_archive_size_mb)
        archive_form.addRow("Archive threshold", self._archive_mb)

        archive_box = QGroupBox("Log archiving", self)
        archive_box.setLayout(archive_form)

        # --- sharing --------------------------------------------------------
        sharing_form = QFormLayout()
        self._sharing_mode = QComboBox(self)
        self._sharing_mode.addItems(["pigparse", "nparse", "off"])
        self._sharing_mode.setCurrentText(settings.sharing.mode)
        sharing_form.addRow("Location sharing", self._sharing_mode)
        sharing_box = QGroupBox("Sharing (network lands in M3)", self)
        sharing_box.setLayout(sharing_form)

        self._restart_note = QLabel(
            "TTS voice/volume and overlay durations apply after restart.", self
        )
        self._restart_note.setStyleSheet("color: #888888; font-size: 11px;")

        apply_button = QPushButton("Apply && Save", self)
        apply_button.clicked.connect(self.apply)

        layout = QVBoxLayout()
        layout.addWidget(general_box)
        layout.addWidget(audio_box)
        layout.addWidget(overlay_box)
        layout.addWidget(archive_box)
        layout.addWidget(sharing_box)
        layout.addStretch(1)
        layout.addWidget(self._restart_note)
        layout.addWidget(apply_button)
        self.setLayout(layout)

        self.restore_visibility()

    # -- actions -----------------------------------------------------------------

    def _test_voice(self) -> None:
        voice = "" if self._voice.currentIndex() == 0 else self._voice.currentText()
        speaker = default_speaker(voice=voice, volume=self._volume.value() / 100)
        speaker.speak("nParse plus voice test")

    def apply(self) -> None:
        general = self._settings.general
        old_log_dir = str(general.eq_log_dir)
        general.eq_log_dir = Path(self._log_dir.path()).expanduser()
        install = self._install_dir.path()
        general.eq_install_dir = Path(install).expanduser() if install else None
        general.update_check = self._update_check.isChecked()
        general.font_size = self._font_size.value()
        general.tts_voice = None if self._voice.currentIndex() == 0 else self._voice.currentText()
        general.global_audio_volume = self._volume.value()
        general.overlay_text_seconds = self._overlay_seconds.value()
        general.ch_lane_retention_seconds = self._ch_retention.value()
        general.log_archive_enabled = self._archive_enabled.isChecked()
        general.log_archive_size_mb = self._archive_mb.value()
        self._settings.sharing.mode = self._sharing_mode.currentText()

        if self._on_save is not None:
            self._on_save()
        if self._on_log_dir_changed is not None and str(general.eq_log_dir) != old_log_dir:
            self._on_log_dir_changed(Path(general.eq_log_dir))

    # keep normal window mouse behavior (text fields, sliders)
    def mousePressEvent(self, event) -> None:
        QWidget.mousePressEvent(self, event)

    def mouseMoveEvent(self, event) -> None:
        QWidget.mouseMoveEvent(self, event)

    def mouseReleaseEvent(self, event) -> None:
        QWidget.mouseReleaseEvent(self, event)

    def wheelEvent(self, event) -> None:
        QWidget.wheelEvent(self, event)
