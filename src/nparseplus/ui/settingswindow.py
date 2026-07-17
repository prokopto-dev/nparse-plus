"""The unified "nParse+ Settings" window — ONE settings surface.

Replaces both the legacy ``helpers.settings.SettingsWindow`` QDialog (which
edited the legacy ``config.data`` dict for the maps/discord windows) and the
M2 ``PreferencesWindow`` (which edited the Pydantic ``Settings``). Until the
maps window is rebuilt the app still runs two config systems, so this window
is the dual-write bridge: Apply writes the Pydantic model AND the legacy
dict, then notifies both worlds (``on_save`` / ``config.save`` +
``config_updated``, which live-applies legacy window opacity/flags) and
repaints the maps canvas (it reads its appearance keys at paint time).

Everything external is injected (legacy dict, save/notify/repaint callables,
window handles, backend player, zone database) so tests drive it with fakes.
"""

from __future__ import annotations

import contextlib
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from nparseplus.audio.tts import default_speaker, list_voices
from nparseplus.config.settings import PlayerInfo, Settings, WindowState
from nparseplus.core import friends, visionfix
from nparseplus.core.enums import PlayerClass
from nparseplus.core.events import (
    AfterPlayerChangedEvent,
    ClassDetectedEvent,
    PlayerLevelDetectionEvent,
    WhoPlayerEvent,
    YouZonedEvent,
)
from nparseplus.core.player import TRACKABLE_CLASSES, ActivePlayer
from nparseplus.core.zones import ZoneDatabase
from nparseplus.net.discordauth import DiscordAuthResult
from nparseplus.net.discordauth import login as discord_login
from nparseplus.ui.overlaybase import OverlayWindowBase

WINDOW_KEY = "settings"
DEFAULT_GEOMETRY = (240, 160, 640, 560)

# The Windows-grid rows. Legacy rows live in config.data[section]; new rows
# live in Settings.windows[key]. Both kinds get apply_window_state() called
# directly on their handle when applied.
LEGACY_WINDOW_ROWS = [("Maps", "maps"), ("Discord", "discord")]
NEW_WINDOW_ROWS = [
    ("Spell Timers", "spells"),
    ("DPS Meter", "dps"),
    ("Mob Info", "mobinfo"),
    ("Console", "console"),
    ("Trigger Editor", "triggereditor"),
]

# Class combo entries: every playable class (no OTHER), EQTool SettingsGeneral.
PLAYER_CLASSES = [cls for cls in PlayerClass if cls is not PlayerClass.OTHER]


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


class _WindowRow:
    """One Windows-grid row: on-top checkbox + opacity slider (+clickthrough)."""

    def __init__(
        self,
        label: str,
        *,
        on_top: bool,
        opacity_pct: int,
        clickthrough: bool | None = None,
        handle: object | None = None,
    ) -> None:
        self.label = label
        self.handle = handle
        self.on_top = QCheckBox()
        self.on_top.setChecked(on_top)
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(10, 100)  # 10% floor: a window must stay findable
        self.opacity.setValue(max(10, min(100, opacity_pct)))
        self.opacity.valueChanged.connect(self._live_preview)
        self.clickthrough: QCheckBox | None = None
        if clickthrough is not None:
            self.clickthrough = QCheckBox()
            self.clickthrough.setChecked(clickthrough)

    def _live_preview(self, value: int) -> None:
        if self.handle is not None:
            # A fake/absent handle must never break the slider.
            with contextlib.suppress(Exception):
                self.handle.setWindowOpacity(value / 100)


class UnifiedSettingsWindow(OverlayWindowBase):
    # Emitted from the login worker thread; queued onto the GUI thread.
    _discord_auth_done = Signal(object)

    def __init__(
        self,
        settings: Settings,
        on_save: Callable[[], None],
        *,
        discord_login_fn: Callable[[], DiscordAuthResult | None] = discord_login,
        on_log_dir_changed: Callable[[Path], None] | None = None,
        legacy_config: dict[str, Any] | None = None,
        on_legacy_save: Callable[[], None] | None = None,
        notify_legacy: Callable[[], None] | None = None,
        repaint_maps: Callable[[], None] | None = None,
        window_handles: dict[str, object] | None = None,
        backend_player: ActivePlayer | None = None,
        zones: ZoneDatabase | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            settings=settings,
            window_key=WINDOW_KEY,
            title="nParse+ Settings",
            default_geometry=DEFAULT_GEOMETRY,
            on_save=on_save,
            default_state=WindowState(frameless=False, always_on_top=False),
            translucent=False,
            parent=parent,
        )
        self._on_log_dir_changed = on_log_dir_changed
        self._legacy = legacy_config if legacy_config is not None else {}
        self._on_legacy_save = on_legacy_save
        self._notify_legacy = notify_legacy
        self._repaint_maps = repaint_maps
        self._handles = window_handles or {}
        self._backend_player = backend_player
        self._zones = zones
        self._discord_login = discord_login_fn
        self._discord_auth_done.connect(self._finish_discord_login)

        self._sidebar = QListWidget(self)
        self._sidebar.setFixedWidth(140)
        self._stack = QStackedWidget(self)
        self._sidebar.currentRowChanged.connect(self._stack.setCurrentIndex)

        for name, builder in (
            ("General", self._build_general),
            ("Character", self._build_character),
            ("Friends", self._build_friends),
            ("Spell Timers", self._build_spell_timers),
            ("Maps", self._build_maps),
            ("Windows", self._build_windows_grid),
            ("Audio && Overlays", self._build_audio_overlays),
            ("Sharing", self._build_sharing),
            ("Advanced", self._build_advanced),
        ):
            self._sidebar.addItem(name.replace("&&", "&"))
            self._stack.addWidget(builder())
        self._sidebar.setCurrentRow(0)

        apply_button = QPushButton("Apply && Save", self)
        apply_button.clicked.connect(self.apply)
        close_button = QPushButton("Close", self)
        close_button.clicked.connect(self.hide)

        body = QHBoxLayout()
        body.addWidget(self._sidebar)
        body.addWidget(self._stack, 1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(apply_button)
        buttons.addWidget(close_button)
        layout = QVBoxLayout()
        layout.addLayout(body, 1)
        layout.addLayout(buttons)
        self.setLayout(layout)

        self.restore_visibility()

    # -- legacy dict access -----------------------------------------------------

    def _lc(self, section: str, key: str, default: Any) -> Any:
        return self._legacy.get(section, {}).get(key, default)

    def _lc_set(self, section: str, key: str, value: Any) -> None:
        self._legacy.setdefault(section, {})[key] = value

    # -- General ------------------------------------------------------------------

    def _build_general(self) -> QWidget:
        general = self._settings.general
        form = QFormLayout()
        self._log_dir = _DirPicker("Select EverQuest Logs directory", self)
        self._log_dir.edit.setText(str(general.eq_log_dir))
        form.addRow("EQ Logs directory", self._log_dir)
        self._install_dir = _DirPicker("Select EverQuest install directory", self)
        self._install_dir.edit.setText(str(general.eq_install_dir or ""))
        self._install_dir.edit.textChanged.connect(lambda _text: self._refresh_visionfix_status())
        form.addRow("EQ install directory", self._install_dir)
        self._update_check = QCheckBox(self)
        self._update_check.setChecked(general.update_check)
        form.addRow("Check for updates", self._update_check)
        self._theme_combo = QComboBox(self)
        self._theme_combo.addItem("Dark", "dark")
        self._theme_combo.addItem("Light", "light")
        self._theme_combo.setCurrentIndex(max(self._theme_combo.findData(general.theme), 0))
        self._theme_combo.setToolTip(
            "Window color theme (the full-screen event overlay stays dark — "
            "it renders over the game)."
        )
        form.addRow("Theme", self._theme_combo)
        self._font_size = QSpinBox(self)
        self._font_size.setRange(6, 32)
        self._font_size.setValue(general.font_size)
        form.addRow("Font size", self._font_size)
        note = QLabel("Theme, font size, TTS, and overlay durations apply after restart.", self)
        note.setStyleSheet("color: #888888; font-size: 11px;")
        form.addRow(note)
        return self._page(form)

    # -- Character -------------------------------------------------------------------

    def _build_character(self) -> QWidget:
        form = QFormLayout()
        self._char_combo = QComboBox(self)
        for info in self._settings.players:
            self._char_combo.addItem(self._char_label(info))
        form.addRow("Character", self._char_combo)

        self._char_class = QComboBox(self)
        self._char_class.addItem("(unknown)")
        for cls in PLAYER_CLASSES:
            self._char_class.addItem(cls.display_name)
        self._char_class.currentIndexChanged.connect(lambda _i: self._sync_track_enabled())
        form.addRow("Class", self._char_class)

        self._char_level = QSpinBox(self)
        self._char_level.setRange(0, 60)
        self._char_level.setSpecialValueText("(unknown)")
        form.addRow("Your Level", self._char_level)

        self._char_zone = QComboBox(self)
        self._char_zone.setEditable(True)
        if self._zones is not None:
            self._char_zone.addItem("")
            for long_name in sorted(self._zones.long_names()):
                self._char_zone.addItem(long_name)
        form.addRow("Zone", self._char_zone)

        self._char_track = QSpinBox(self)
        self._char_track.setRange(0, 200)
        self._char_track.setSpecialValueText("(unset)")
        form.addRow("Track Skill", self._char_track)

        self._char_sharing = QComboBox(self)
        self._char_sharing.addItems(["everyone", "guild", "off"])
        form.addRow("Location sharing", self._char_sharing)

        self._char_share_timers = QCheckBox(self)
        form.addRow("Share timers", self._char_share_timers)

        # EQTool TimerRecastSetting: recast a detrimental spell on an NPC and
        # either refresh the running timer or stack a new one per cast (roots
        # always refresh).
        self._char_timer_recast = QComboBox(self)
        self._char_timer_recast.addItem("Restart Current Timer", "RestartCurrentTimer")
        self._char_timer_recast.addItem("Start New Timer", "StartNewTimer")
        self._char_timer_recast.setToolTip(
            "Recasting a detrimental spell on an NPC: restart the running timer, "
            "or start a new one per cast (for DoTs stacked on several mobs). "
            "Root spells always restart."
        )
        form.addRow("Timer recast", self._char_timer_recast)

        # Spell class filters (EQTool "Class Filters"): a spell shows on other
        # players when ANY checked class can cast it. All checked = show all.
        filters_box = QGroupBox("Show spells for classes", self)
        grid = QGridLayout()
        self._class_filter_boxes: dict[PlayerClass, QCheckBox] = {}
        for i, cls in enumerate(PLAYER_CLASSES):
            box = QCheckBox(cls.display_name, self)
            self._class_filter_boxes[cls] = box
            grid.addWidget(box, i // 3, i % 3)
        filters_box.setLayout(grid)
        form.addRow(filters_box)

        self._char_combo.currentIndexChanged.connect(lambda _i: self._load_character())
        self._active_character = self._backend_character()
        self._select_active_character()
        self._load_character()
        return self._page(form)

    @staticmethod
    def _char_label(info: PlayerInfo) -> str:
        return f"{info.name} ({info.server})"

    def _backend_character(self) -> tuple[str, str | None] | None:
        player = self._backend_player
        if player is None or not player.name:
            return None
        return (player.name, player.server_key)

    def _selected_player(self) -> PlayerInfo | None:
        index = self._char_combo.currentIndex()
        if 0 <= index < len(self._settings.players):
            return self._settings.players[index]
        return None

    def _select_active_character(self) -> None:
        active = self._backend_character()
        if active is None:
            return
        for i, info in enumerate(self._settings.players):
            if (info.name, info.server) == active:
                self._char_combo.setCurrentIndex(i)
                return

    def refresh_characters(self) -> None:
        """Re-sync the character combo with ``settings.players``.

        Profiles are created lazily on the driver thread once a log attaches,
        usually AFTER this window was built — so the combo must be refreshed
        on show and on character-change events. Repopulates only when the
        profile list or the active character actually changed, keeping any
        unsaved field edits for a still-selected character intact.
        """
        active = self._backend_character()
        active_changed = active is not None and active != self._active_character
        self._active_character = active
        labels = [self._char_label(info) for info in self._settings.players]
        current = [self._char_combo.itemText(i) for i in range(self._char_combo.count())]
        if labels == current and not active_changed:
            return
        previous_label = self._char_combo.currentText()
        blocker = QSignalBlocker(self._char_combo)
        self._char_combo.clear()
        self._char_combo.addItems(labels)
        index = -1
        if not active_changed and previous_label in labels:
            index = labels.index(previous_label)
        elif active is not None:
            for i, info in enumerate(self._settings.players):
                if (info.name, info.server) == active:
                    index = i
                    break
        if index < 0 and labels:
            index = 0
        self._char_combo.setCurrentIndex(index)
        del blocker
        if self._char_combo.currentText() != previous_label or not previous_label:
            self._load_character()

    def handle_backend_event(self, event: object) -> None:
        """Bridge slot (GUI thread): keep the selected active profile current."""
        if isinstance(event, AfterPlayerChangedEvent):
            self.refresh_characters()
            return

        active = self._backend_character()
        if active is not None and active != self._active_character:
            # Stale bookkeeping (e.g. the profile was created after this
            # window was built and no character-change event re-synced us):
            # heal it now so live /who//zone updates aren't silently dropped.
            self.refresh_characters()
        info = self._selected_player()
        if info is None or active is None or (info.name, info.server) != active:
            return
        if isinstance(event, WhoPlayerEvent):
            if event.player.name.casefold() != info.name.casefold():
                return
            self._refresh_character_fields(
                player_class=event.player.player_class is not None,
                level=event.player.level is not None,
            )
        elif isinstance(event, ClassDetectedEvent):
            self._refresh_character_fields(player_class=True)
        elif isinstance(event, PlayerLevelDetectionEvent):
            self._refresh_character_fields(level=True)
        elif isinstance(event, YouZonedEvent):
            self._refresh_character_fields(zone=True)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh_characters()
        # Always reload the character fields: the backend mutates the profile
        # (/who, level dings, zoning) while the window is hidden, and
        # refresh_characters skips _load_character when the combo is
        # unchanged — without this, reopening showed stale class/level/zone.
        self._load_character()

    def _load_character(self) -> None:
        info = self._selected_player()
        enabled = info is not None
        for widget in (
            self._char_class,
            self._char_level,
            self._char_zone,
            self._char_track,
            self._char_sharing,
            self._char_share_timers,
            self._char_timer_recast,
            *self._class_filter_boxes.values(),
        ):
            widget.setEnabled(enabled)
        if info is None:
            return
        self._char_class.setCurrentIndex(self._class_combo_index(info.player_class))
        self._char_level.setValue(info.level or 0)
        zone_display = info.zone
        if self._zones is not None and info.zone:
            zone_display = self._zones.long_name(info.zone) or info.zone
        self._char_zone.setCurrentText(zone_display)
        self._char_track.setValue(info.tracking_skill or 0)
        self._char_sharing.setCurrentText(info.map_location_sharing)
        self._char_share_timers.setChecked(info.share_timers)
        recast_index = self._char_timer_recast.findData(info.timer_recast)
        self._char_timer_recast.setCurrentIndex(max(recast_index, 0))
        selected = info.show_spells_for_classes
        for cls, box in self._class_filter_boxes.items():
            box.setChecked(selected is None or int(cls) in selected)
        self._sync_track_enabled()

    def _refresh_character_fields(
        self,
        *,
        player_class: bool = False,
        level: bool = False,
        zone: bool = False,
    ) -> None:
        """Refresh backend-owned fields without discarding other unsaved edits."""
        info = self._selected_player()
        if info is None:
            return
        if player_class:
            self._char_class.setCurrentIndex(self._class_combo_index(info.player_class))
            # The class signal enables Track Skill. Restore its saved value in
            # case the formerly-unknown class had caused the widget to clear.
            self._char_track.setValue(info.tracking_skill or 0)
            self._sync_track_enabled()
        if level:
            self._char_level.setValue(info.level or 0)
        if zone:
            zone_display = info.zone
            if self._zones is not None and info.zone:
                zone_display = self._zones.long_name(info.zone) or info.zone
            self._char_zone.setCurrentText(zone_display)

    @staticmethod
    def _class_combo_index(raw: int | None) -> int:
        """Class-combo index for a stored class value; 0 ("unknown") when the
        value is None, PlayerClass.OTHER (the castable-by-everyone spell
        fixup, not a real class), or junk from a hand-edited settings.json —
        PLAYER_CLASSES.index would raise for those in a live-event slot."""
        if raw is None:
            return 0
        try:
            cls = PlayerClass(raw)
        except ValueError:
            return 0
        if cls not in PLAYER_CLASSES:
            return 0
        return PLAYER_CLASSES.index(cls) + 1

    def _combo_class(self) -> PlayerClass | None:
        index = self._char_class.currentIndex()
        return PLAYER_CLASSES[index - 1] if index > 0 else None

    def _sync_track_enabled(self) -> None:
        """Track Skill only means something for Druid/Ranger/Bard (EQTool)."""
        trackable = self._combo_class() in TRACKABLE_CLASSES
        self._char_track.setEnabled(trackable and self._char_combo.currentIndex() >= 0)
        if not trackable:
            self._char_track.setValue(0)

    def _apply_character(self) -> None:
        info = self._selected_player()
        if info is None:
            return
        # Mutate IN PLACE: handlers and the sharing coordinator hold this object.
        cls = self._combo_class()
        info.player_class = int(cls) if cls is not None else None
        info.level = self._char_level.value() or None
        zone_text = self._char_zone.currentText().strip()
        if self._zones is not None and zone_text:
            info.zone = self._zones.short_name(zone_text) or zone_text
        else:
            info.zone = zone_text
        info.tracking_skill = self._char_track.value() if cls in TRACKABLE_CLASSES else 0
        info.map_location_sharing = self._char_sharing.currentText()  # type: ignore[assignment]
        info.share_timers = self._char_share_timers.isChecked()
        info.timer_recast = self._char_timer_recast.currentData()
        checked = [int(cls) for cls, box in self._class_filter_boxes.items() if box.isChecked()]
        info.show_spells_for_classes = None if len(checked) == len(PLAYER_CLASSES) else checked

        player = self._backend_player
        if player is not None and info.name == player.name and info.server == player.server_key:
            player.player_class = cls
            player.level = info.level
            if info.zone:
                player.zone = info.zone
            player.tracking_skill = info.tracking_skill or None

    # -- Friends (EQ client [Friends] ini sync, EQTool SettingsGeneral) -------------------

    def _build_friends(self) -> QWidget:
        layout = QVBoxLayout()
        form = QFormLayout()
        self._friends_server = QComboBox(self)
        self._friends_server.addItems(list(friends.SERVER_SUFFIXES))
        self._friends_server.currentIndexChanged.connect(lambda _i: self._load_friends())
        form.addRow("Server", self._friends_server)
        layout.addLayout(form)
        self._friends_text = QPlainTextEdit(self)
        self._friends_text.setPlaceholderText("One friend name per line…")
        layout.addWidget(self._friends_text, 1)
        buttons = QHBoxLayout()
        load_button = QPushButton("Load from characters", self)
        load_button.clicked.connect(self._load_friends)
        push_button = QPushButton("Push to all characters", self)
        push_button.clicked.connect(self._push_friends)
        buttons.addWidget(load_button)
        buttons.addWidget(push_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self._friends_status = QLabel(
            "Merges every character's in-game friends list on the selected server; "
            "Push writes the merged list back (originals backed up to friends_backup/).",
            self,
        )
        self._friends_status.setWordWrap(True)
        self._friends_status.setStyleSheet("color: #888888; font-size: 11px;")
        layout.addWidget(self._friends_status)
        page = QWidget(self)
        page.setLayout(layout)
        return page

    def _friends_files(self) -> list[Path]:
        eq_dir = self._install_dir.path() or str(self._settings.general.eq_install_dir or "")
        if not eq_dir:
            return []
        suffix = friends.SERVER_SUFFIXES[self._friends_server.currentText()]
        return friends.friend_ini_files(Path(eq_dir), suffix)

    def _load_friends(self) -> None:
        files = self._friends_files()
        if not files:
            self._friends_status.setText(
                "No character ini files found — set the EQ install directory on General."
            )
            self._friends_text.setPlainText("")
            return
        merged = friends.merged_friends(files)
        self._friends_text.setPlainText("\n".join(merged))
        self._friends_status.setText(f"{len(merged)} friends across {len(files)} character(s).")

    def _push_friends(self) -> None:
        files = self._friends_files()
        if not files:
            self._friends_status.setText("No character ini files found for this server.")
            return
        names = self._friends_text.toPlainText().splitlines()
        errors = friends.push_friends(files, names)
        if errors:
            self._friends_status.setText("Some files failed: " + "; ".join(errors))
        else:
            self._friends_status.setText(
                f"Pushed {len(friends.normalize_names(names))} friends to {len(files)} file(s)."
            )

    # -- Spell Timers -------------------------------------------------------------------

    def _build_spell_timers(self) -> QWidget:
        spellwindow = self._settings.spellwindow
        form = QFormLayout()
        self._you_only = QCheckBox(self)
        self._you_only.setChecked(spellwindow.you_only_spells)
        form.addRow("Show only your own spells", self._you_only)
        self._show_rolls = QCheckBox(self)
        self._show_rolls.setChecked(spellwindow.show_random_rolls)
        form.addRow("Show random rolls", self._show_rolls)
        # Category display toggles (hide the section; timers keep running
        # and respawn-expiry audio still fires while hidden).
        self._show_boats = QCheckBox(self)
        self._show_boats.setChecked(spellwindow.show_boats)
        form.addRow("Show boat timers", self._show_boats)
        self._show_custom_timers = QCheckBox(self)
        self._show_custom_timers.setChecked(spellwindow.show_custom_timers)
        self._show_custom_timers.setToolTip(
            "The Custom Timer section: mob death/respawn countdowns, FTE and shared timers."
        )
        form.addRow("Show mob respawn timers", self._show_custom_timers)
        self._show_trigger_timers = QCheckBox(self)
        self._show_trigger_timers.setChecked(spellwindow.show_trigger_timers)
        self._show_trigger_timers.setToolTip(
            "The Timers section: countdowns started by triggers and chat commands."
        )
        form.addRow("Show trigger && chat timers", self._show_trigger_timers)
        self._best_guess = QCheckBox(self)
        self._best_guess.setChecked(spellwindow.best_guess_spells)
        self._best_guess.setToolTip(
            "When a cast message matches several spells, start a timer for the "
            "closest-level guess. Off: ambiguous casts start no timer."
        )
        form.addRow("Guess ambiguous spells", self._best_guess)
        self._raid_mode = QCheckBox(self)
        self._raid_mode.setChecked(spellwindow.raid_mode_auto)
        self._raid_mode.setToolTip(
            "When buff rows span more targets than distinct spells (raids), "
            "regroup the window by spell instead of by target."
        )
        form.addRow("Auto raid-mode grouping", self._raid_mode)
        self._respawn_audio = QCheckBox(self)
        self._respawn_audio.setChecked(spellwindow.respawn_expiry_audio)
        self._respawn_audio.setToolTip(
            'Speak "<mob> spawn timer expired" when a respawn countdown runs out.'
        )
        form.addRow("Announce respawn-timer expiry", self._respawn_audio)
        self._buff_fade_secs = QSpinBox(self)
        self._buff_fade_secs.setRange(0, 300)
        self._buff_fade_secs.setSuffix(" s")
        self._buff_fade_secs.setSpecialValueText("off")
        self._buff_fade_secs.setValue(spellwindow.buff_fade_warning_seconds)
        self._buff_fade_secs.setToolTip(
            "Warn this many seconds before one of your own buffs fades "
            "(the time label turns red; optional voice alert below)."
        )
        form.addRow("Buff-fade warning", self._buff_fade_secs)
        self._buff_fade_audio = QCheckBox(self)
        self._buff_fade_audio.setChecked(spellwindow.buff_fade_warning_audio)
        form.addRow("Speak buff-fade warnings", self._buff_fade_audio)
        note = QLabel("Per-class spell filters live on the Character page.", self)
        note.setStyleSheet("color: #888888; font-size: 11px;")
        form.addRow(note)
        return self._page(form)

    # -- Maps (legacy config keys until the maps window is rebuilt) ----------------------

    def _build_maps(self) -> QWidget:
        form = QFormLayout()
        self._maps_line_width = QSpinBox(self)
        self._maps_line_width.setRange(1, 10)
        self._maps_line_width.setValue(int(self._lc("maps", "line_width", 1)))
        form.addRow("Map line width", self._maps_line_width)
        self._maps_grid_width = QSpinBox(self)
        self._maps_grid_width.setRange(1, 10)
        self._maps_grid_width.setValue(int(self._lc("maps", "grid_line_width", 1)))
        form.addRow("Grid line width", self._maps_grid_width)
        self._maps_font_scale = QSpinBox(self)
        self._maps_font_scale.setRange(50, 200)
        self._maps_font_scale.setSuffix(" %")
        self._maps_font_scale.setValue(int(self._lc("maps", "map_font_scale", 100)))
        self._maps_font_scale.setToolTip("Scales POI labels, player names, and spawn countdowns.")
        form.addRow("Map label size", self._maps_font_scale)
        self._maps_show_others = QCheckBox(self)
        self._maps_show_others.setChecked(bool(self._lc("maps", "show_other_players", True)))
        self._maps_show_others.setToolTip(
            "Draw other players' shared dots on the map. Off still shares your "
            "own location — it only hides theirs."
        )
        form.addRow("Show other players' dots", self._maps_show_others)
        self._z_current = QSpinBox(self)
        self._z_closest = QSpinBox(self)
        self._z_other = QSpinBox(self)
        for spin, key, label in (
            (self._z_current, "current_z_alpha", "Current Z opacity"),
            (self._z_closest, "closest_z_alpha", "Closest Z opacity"),
            (self._z_other, "other_z_alpha", "Other Z opacity"),
        ):
            spin.setRange(1, 100)
            spin.setSuffix(" %")
            spin.setValue(int(self._lc("maps", key, 100)))
            form.addRow(label, spin)

        fade_box = QGroupBox("Smooth z-axis fade (when Z layers are off)", self)
        fade_form = QFormLayout()
        self._z_fade_enabled = QCheckBox(self)
        self._z_fade_enabled.setChecked(bool(self._lc("maps", "z_fade_enabled", True)))
        fade_form.addRow("Enabled", self._z_fade_enabled)
        self._z_fade_min = QSpinBox(self)
        self._z_fade_min.setRange(1, 100)
        self._z_fade_min.setSuffix(" %")
        self._z_fade_min.setValue(int(self._lc("maps", "z_fade_min_opacity", 10)))
        self._z_fade_min.setToolTip("Opacity floor for geometry far above/below you.")
        fade_form.addRow("Minimum opacity", self._z_fade_min)
        self._z_fade_strength = QSpinBox(self)
        self._z_fade_strength.setRange(25, 400)
        self._z_fade_strength.setSuffix(" %")
        self._z_fade_strength.setValue(int(self._lc("maps", "z_fade_strength", 100)))
        self._z_fade_strength.setToolTip(
            "Above 100% fades sooner and harder; below 100% keeps distant levels visible longer."
        )
        fade_form.addRow("Fade strength", self._z_fade_strength)
        self._z_fade_fallback = QSpinBox(self)
        self._z_fade_fallback.setRange(0, 1000)
        self._z_fade_fallback.setSpecialValueText("(off)")
        self._z_fade_fallback.setValue(int(self._lc("maps", "z_fade_fallback_height", 0)))
        self._z_fade_fallback.setToolTip(
            "Level height (z-units) assumed for zones without level metadata, "
            "so they fade too. (off) = such zones never fade, like EQTool."
        )
        fade_form.addRow("Fallback level height", self._z_fade_fallback)
        fade_box.setLayout(fade_form)
        form.addRow(fade_box)
        return self._page(form)

    def _apply_maps(self) -> None:
        self._lc_set("maps", "line_width", self._maps_line_width.value())
        self._lc_set("maps", "grid_line_width", self._maps_grid_width.value())
        self._lc_set("maps", "map_font_scale", self._maps_font_scale.value())
        self._lc_set("maps", "show_other_players", self._maps_show_others.isChecked())
        self._lc_set("maps", "current_z_alpha", self._z_current.value())
        self._lc_set("maps", "closest_z_alpha", self._z_closest.value())
        self._lc_set("maps", "other_z_alpha", self._z_other.value())
        self._lc_set("maps", "z_fade_enabled", self._z_fade_enabled.isChecked())
        self._lc_set("maps", "z_fade_min_opacity", self._z_fade_min.value())
        self._lc_set("maps", "z_fade_strength", self._z_fade_strength.value())
        self._lc_set("maps", "z_fade_fallback_height", self._z_fade_fallback.value())

    # -- Windows grid ------------------------------------------------------------------------

    def _build_windows_grid(self) -> QWidget:
        grid = QGridLayout()
        grid.addWidget(QLabel("<b>Window</b>"), 0, 0)
        grid.addWidget(QLabel("<b>On top</b>"), 0, 1)
        grid.addWidget(QLabel("<b>Opacity</b>"), 0, 2)
        grid.addWidget(QLabel("<b>Click-through</b>"), 0, 3)
        self._legacy_rows: dict[str, _WindowRow] = {}
        self._new_rows: dict[str, _WindowRow] = {}
        row_index = 1
        for label, section in LEGACY_WINDOW_ROWS:
            row = _WindowRow(
                label,
                on_top=bool(self._lc(section, "always_on_top", True)),
                opacity_pct=int(self._lc(section, "opacity", 80)),
                clickthrough=bool(self._lc(section, "clickthrough", False)),
                handle=self._handles.get(section),
            )
            self._legacy_rows[section] = row
            self._add_grid_row(grid, row_index, row)
            row_index += 1
        for label, key in NEW_WINDOW_ROWS:
            state = self._settings.windows.setdefault(key, WindowState())
            row = _WindowRow(
                label,
                on_top=state.always_on_top,
                opacity_pct=round(state.opacity * 100),
                handle=self._handles.get(key),
            )
            self._new_rows[key] = row
            self._add_grid_row(grid, row_index, row)
            row_index += 1

        # Discord extras (bg opacity is the webview's own background).
        self._discord_bg = QSpinBox(self)
        self._discord_bg.setRange(0, 100)
        self._discord_bg.setSuffix(" %")
        self._discord_bg.setValue(int(self._lc("discord", "bg_opacity", 25)))
        grid.addWidget(QLabel("Discord background"), row_index, 0)
        grid.addWidget(self._discord_bg, row_index, 2)
        grid.setColumnStretch(2, 1)

        outer = QVBoxLayout()
        outer.addLayout(grid)
        note = QLabel("Opacity previews immediately; On top / Click-through apply on Save.", self)
        note.setStyleSheet("color: #888888; font-size: 11px;")
        outer.addWidget(note)
        outer.addStretch(1)
        page = QWidget(self)
        page.setLayout(outer)
        return page

    @staticmethod
    def _add_grid_row(grid: QGridLayout, index: int, row: _WindowRow) -> None:
        grid.addWidget(QLabel(row.label), index, 0)
        grid.addWidget(row.on_top, index, 1)
        grid.addWidget(row.opacity, index, 2)
        if row.clickthrough is not None:
            grid.addWidget(row.clickthrough, index, 3)

    def _apply_windows(self) -> None:
        for section, row in self._legacy_rows.items():
            self._lc_set(section, "always_on_top", row.on_top.isChecked())
            self._lc_set(section, "opacity", row.opacity.value())
            if row.clickthrough is not None:
                self._lc_set(section, "clickthrough", row.clickthrough.isChecked())
            # Apply directly, same as the new rows below — the config_updated
            # signal fires later in apply(), after the save callbacks, and a
            # failure anywhere in between must not leave the legacy windows
            # with stale flags while the new windows already changed.
            handle = self._handles.get(section)
            if handle is not None and hasattr(handle, "apply_window_state"):
                handle.apply_window_state()
        self._lc_set("discord", "bg_opacity", self._discord_bg.value())
        for key, row in self._new_rows.items():
            state = self._settings.windows.setdefault(key, WindowState())
            state.always_on_top = row.on_top.isChecked()
            state.opacity = row.opacity.value() / 100
            handle = self._handles.get(key)
            if handle is not None and hasattr(handle, "apply_window_state"):
                handle.apply_window_state()

    # -- Audio & Overlays ------------------------------------------------------------------

    def _build_audio_overlays(self) -> QWidget:
        general = self._settings.general
        form = QFormLayout()
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
        form.addRow("TTS voice", self._voice)
        self._volume = QSlider(Qt.Orientation.Horizontal, self)
        self._volume.setRange(0, 100)
        self._volume.setValue(general.global_audio_volume)
        form.addRow("Volume", self._volume)
        test_button = QPushButton("Test voice", self)
        test_button.clicked.connect(self._test_voice)
        form.addRow("", test_button)
        self._overlay_seconds = QDoubleSpinBox(self)
        self._overlay_seconds.setRange(1.0, 30.0)
        self._overlay_seconds.setSingleStep(0.5)
        self._overlay_seconds.setValue(general.overlay_text_seconds)
        form.addRow("Alert text duration (s)", self._overlay_seconds)
        self._ch_retention = QDoubleSpinBox(self)
        self._ch_retention.setRange(5.0, 300.0)
        self._ch_retention.setSingleStep(5.0)
        self._ch_retention.setValue(general.ch_lane_retention_seconds)
        form.addRow("CH lane retention (s)", self._ch_retention)
        return self._page(form)

    def _test_voice(self) -> None:
        voice = "" if self._voice.currentIndex() == 0 else self._voice.currentText()
        speaker = default_speaker(voice=voice, volume=self._volume.value() / 100)
        speaker.speak("nParse plus voice test")

    # -- Sharing --------------------------------------------------------------------------

    def _build_sharing(self) -> QWidget:
        form = QFormLayout()
        self._sharing_mode = QComboBox(self)
        self._sharing_mode.addItems(["pigparse", "nparse", "off"])
        self._sharing_mode.setCurrentText(self._settings.sharing.mode)
        form.addRow("Location sharing", self._sharing_mode)
        note = QLabel("Sharing mode applies after restart.", self)
        note.setStyleSheet("color: #888888; font-size: 11px;")
        form.addRow(note)

        account_box = QGroupBox("pigparse.org account", self)
        account_form = QFormLayout()
        self._account_status = QLabel("", self)
        self._account_status.setWordWrap(True)
        account_form.addRow(self._account_status)
        account_buttons = QHBoxLayout()
        self._account_login = QPushButton("Log in with Discord…", self)
        self._account_login.clicked.connect(self._start_discord_login)
        self._account_logout = QPushButton("Log out", self)
        self._account_logout.clicked.connect(self._discord_logout)
        account_buttons.addWidget(self._account_login)
        account_buttons.addWidget(self._account_logout)
        account_buttons.addStretch(1)
        account_form.addRow(account_buttons)
        self._inventory_upload = QCheckBox(self)
        self._inventory_upload.setChecked(self._settings.pigparse_account.inventory_upload)
        self._inventory_upload.setToolTip(
            "Watch the EQ directory for /outputfile inventory dumps and upload "
            "them to your pigparse.org character page. Needs a login."
        )
        account_form.addRow("Upload inventory dumps", self._inventory_upload)
        account_box.setLayout(account_form)
        form.addRow(account_box)
        self._refresh_account_status()
        return self._page(form)

    def _refresh_account_status(self) -> None:
        account = self._settings.pigparse_account
        if account.api_token:
            who = account.username or account.discord_id
            self._account_status.setText(f"Logged in as {who}.")
        else:
            self._account_status.setText(
                "Not logged in. Logging in via Discord enables the auction APIs "
                "and the pigparse.org character browser (inventory upload)."
            )
        self._account_login.setEnabled(True)
        self._account_logout.setEnabled(bool(account.api_token))

    def _start_discord_login(self) -> None:
        """Open the pigparse Discord login in the browser; the user
        authenticates there and the loopback redirect delivers the token."""
        self._account_login.setEnabled(False)
        self._account_status.setText("Waiting for the browser login…")
        login_fn = self._discord_login

        def run() -> None:
            try:
                result = login_fn()
            except Exception:
                result = None
            # Cross-thread emit: Qt queues delivery onto the GUI thread.
            self._discord_auth_done.emit(result)

        threading.Thread(target=run, name="discord-login", daemon=True).start()

    def _finish_discord_login(self, result: object) -> None:
        account = self._settings.pigparse_account
        if isinstance(result, DiscordAuthResult) and result.ok:
            account.username = result.username
            account.discord_id = result.discord_id
            account.api_token = result.api_token
            if self._on_save is not None:
                self._on_save()
        else:
            self._account_status.setText("Login failed or timed out — try again.")
            self._account_login.setEnabled(True)
            return
        self._refresh_account_status()

    def _discord_logout(self) -> None:
        account = self._settings.pigparse_account
        account.username = ""
        account.discord_id = ""
        account.api_token = ""
        if self._on_save is not None:
            self._on_save()
        self._refresh_account_status()

    # -- Advanced (archiving + Night Vision fix) ---------------------------------------------

    def _build_advanced(self) -> QWidget:
        general = self._settings.general
        form = QFormLayout()
        self._archive_enabled = QCheckBox(self)
        self._archive_enabled.setChecked(general.log_archive_enabled)
        form.addRow("Archive oversized logs", self._archive_enabled)
        self._archive_mb = QSpinBox(self)
        self._archive_mb.setRange(1, 4096)
        self._archive_mb.setSuffix(" MB")
        self._archive_mb.setValue(general.log_archive_size_mb)
        form.addRow("Archive threshold", self._archive_mb)

        visionfix_form = QFormLayout()
        self._visionfix_status = QLabel("", self)
        self._visionfix_status.setWordWrap(True)
        visionfix_form.addRow(self._visionfix_status)
        visionfix_buttons = QHBoxLayout()
        self._visionfix_apply = QPushButton("Apply fix", self)
        self._visionfix_apply.clicked.connect(self._apply_visionfix)
        self._visionfix_revert = QPushButton("Revert", self)
        self._visionfix_revert.clicked.connect(self._revert_visionfix)
        visionfix_buttons.addWidget(self._visionfix_apply)
        visionfix_buttons.addWidget(self._visionfix_revert)
        visionfix_form.addRow(visionfix_buttons)
        visionfix_box = QGroupBox("Night Vision fix", self)
        visionfix_box.setLayout(visionfix_form)
        form.addRow(visionfix_box)
        self._refresh_visionfix_status()
        return self._page(form)

    # -- Night Vision fix (moved from PreferencesWindow) --------------------------------------

    def _visionfix_dir(self) -> Path | None:
        text = self._install_dir.path()
        return Path(text).expanduser() if text else None

    def _refresh_visionfix_status(self) -> None:
        if not hasattr(self, "_visionfix_status"):
            return  # General pane builds before Advanced
        eq_dir = self._visionfix_dir()
        reason = visionfix.preflight(eq_dir)
        if reason is not None:
            self._visionfix_status.setText(reason)
            self._visionfix_apply.setEnabled(False)
            self._visionfix_revert.setEnabled(False)
            return
        assert eq_dir is not None
        has_backup = visionfix.backup_exists(eq_dir)
        self._visionfix_status.setText(
            "Applied (backup present — revert available)."
            if has_backup
            else "Replaces night-blind shaders/sky textures. Files are backed up first."
        )
        self._visionfix_apply.setEnabled(True)
        self._visionfix_revert.setEnabled(has_backup)

    def _eq_running(self) -> bool:
        try:
            result = subprocess.run(
                ["pgrep", "-if", "eqgame"], capture_output=True, timeout=5, check=False
            )
            return result.returncode == 0
        except Exception:
            return False

    def _apply_visionfix(self) -> None:
        eq_dir = self._visionfix_dir()
        if self._eq_running():
            answer = QMessageBox.warning(
                self,
                "EverQuest looks like it is running",
                "Apply anyway? The game must be restarted to pick up the fix.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            written = visionfix.apply_visionfix(eq_dir)
        except Exception as exc:
            QMessageBox.critical(self, "Night Vision fix failed", str(exc))
        else:
            QMessageBox.information(
                self,
                "Night Vision fix applied",
                f"{written} files written (originals backed up to "
                f"{visionfix.BACKUP_DIR_NAME}/). Restart EQ to see the fix.",
            )
        self._refresh_visionfix_status()

    def _revert_visionfix(self) -> None:
        eq_dir = self._visionfix_dir()
        try:
            restored = visionfix.revert_visionfix(eq_dir)
        except Exception as exc:
            QMessageBox.critical(self, "Revert failed", str(exc))
        else:
            QMessageBox.information(
                self, "Night Vision fix reverted", f"{restored} original files restored."
            )
        self._refresh_visionfix_status()

    # -- apply ------------------------------------------------------------------------------------

    def apply(self) -> None:
        general = self._settings.general
        old_log_dir = str(general.eq_log_dir)
        general.eq_log_dir = Path(self._log_dir.path()).expanduser()
        install = self._install_dir.path()
        general.eq_install_dir = Path(install).expanduser() if install else None
        general.update_check = self._update_check.isChecked()
        general.theme = self._theme_combo.currentData()
        general.font_size = self._font_size.value()
        general.tts_voice = None if self._voice.currentIndex() == 0 else self._voice.currentText()
        general.global_audio_volume = self._volume.value()
        general.overlay_text_seconds = self._overlay_seconds.value()
        general.ch_lane_retention_seconds = self._ch_retention.value()
        general.log_archive_enabled = self._archive_enabled.isChecked()
        general.log_archive_size_mb = self._archive_mb.value()
        self._settings.sharing.mode = self._sharing_mode.currentText()  # type: ignore[assignment]
        self._settings.pigparse_account.inventory_upload = self._inventory_upload.isChecked()
        spellwindow = self._settings.spellwindow
        spellwindow.you_only_spells = self._you_only.isChecked()
        spellwindow.show_random_rolls = self._show_rolls.isChecked()
        spellwindow.show_boats = self._show_boats.isChecked()
        spellwindow.show_custom_timers = self._show_custom_timers.isChecked()
        spellwindow.show_trigger_timers = self._show_trigger_timers.isChecked()
        spellwindow.best_guess_spells = self._best_guess.isChecked()
        spellwindow.raid_mode_auto = self._raid_mode.isChecked()
        spellwindow.respawn_expiry_audio = self._respawn_audio.isChecked()
        spellwindow.buff_fade_warning_seconds = self._buff_fade_secs.value()
        spellwindow.buff_fade_warning_audio = self._buff_fade_audio.isChecked()
        self._apply_character()
        self._apply_maps()
        self._apply_windows()

        if self._on_save is not None:
            self._on_save()
        if self._on_legacy_save is not None:
            self._on_legacy_save()
        if self._notify_legacy is not None:
            self._notify_legacy()  # live-applies legacy window opacity/flags
        if self._repaint_maps is not None:
            self._repaint_maps()  # maps canvas reads its keys at paint time
        if self._on_log_dir_changed is not None and str(general.eq_log_dir) != old_log_dir:
            self._on_log_dir_changed(Path(general.eq_log_dir))

    # -- keep normal window mouse behavior (text fields, sliders) ------------------------------

    def _page(self, form: QFormLayout) -> QWidget:
        outer = QVBoxLayout()
        outer.addLayout(form)
        outer.addStretch(1)
        page = QWidget(self)
        page.setLayout(outer)
        return page

    def mousePressEvent(self, event) -> None:
        QWidget.mousePressEvent(self, event)

    def mouseMoveEvent(self, event) -> None:
        QWidget.mouseMoveEvent(self, event)

    def mouseReleaseEvent(self, event) -> None:
        QWidget.mouseReleaseEvent(self, event)

    def wheelEvent(self, event) -> None:
        QWidget.wheelEvent(self, event)
