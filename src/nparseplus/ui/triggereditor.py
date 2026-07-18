"""Trigger editor — a framed tool window for browsing and editing triggers.

Port of EQTool's SettingsTrigger / TriggerOutputEditor UX: a folder tree of
triggers (built-in folders + user categories) on the left, a form editor on
the right, and a Test box that runs a log line through the trigger's own
matching machinery.

All edits happen on deep copies of ``settings.triggers``; nothing reaches the
:class:`TriggerEngine` or the persisted settings until Apply. Built-ins can be
edited (they gain ``customized=True`` on Apply) but never deleted — the Delete
button becomes Disable for them, and Revert restores the stock definition via
``sync_builtin_triggers``.

This is a normal framed window, not a frameless overlay, so it deliberately
skips ``OverlayWindowBase`` (whose drag-to-move/wheel handling suits
overlays) and rolls minimal geometry persistence into
``Settings.windows["triggereditor"]``.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from nparseplus.config.settings import Settings, WindowState
from nparseplus.core.triggers.builtin import sync_builtin_triggers
from nparseplus.core.triggers.engine import TriggerEngine
from nparseplus.core.triggers.exchange import dump_triggers, parse_triggers, sanitize_imported
from nparseplus.core.triggers.gina import load_gina_triggers
from nparseplus.core.triggers.model import (
    TimerRestartBehavior,
    TimerType,
    Trigger,
    TriggerAudioType,
    TriggerCounter,
    TriggerOutput,
    TriggerTimer,
    TriggerTimerEnded,
    TriggerTimerEnding,
)
from nparseplus.core.zones import load_zone_database

WINDOW_KEY = "triggereditor"
DEFAULT_GEOMETRY = (200, 120, 900, 600)

# Common WPF color names EQTool's editor offers for text / timer bars.
COLOR_NAMES = [
    "Red",
    "Yellow",
    "Gold",
    "Orange",
    "ForestGreen",
    "SteelBlue",
    "MediumPurple",
    "White",
]

_TIMER_TYPES = [
    ("No Timer", TimerType.NO_TIMER.value),
    ("CountDown", TimerType.COUNT_DOWN.value),
    ("CountUp", TimerType.COUNT_UP.value),
]

_RESTART_BEHAVIORS = [
    ("Start new timer", TimerRestartBehavior.START_NEW_TIMER.value),
    ("Restart timer", TimerRestartBehavior.RESTART_TIMER.value),
    ("Do nothing", TimerRestartBehavior.DO_NOTHING.value),
]

_ROLE_ID = Qt.ItemDataRole.UserRole
#: Folder items carry "builtin" or "user" here so menus/new-trigger can tell
#: read-only built-in folders from movable user groups.
_ROLE_FOLDER_KIND = _ROLE_ID + 1


def _set_combo(combo: QComboBox, value: str) -> None:
    """Select the entry whose data equals ``value``, adding it if unknown."""
    index = combo.findData(value)
    if index < 0:
        combo.addItem(str(value), value)
        index = combo.count() - 1
    combo.setCurrentIndex(index)


def _hms(total_seconds: float) -> tuple[int, int, int]:
    hours, rem = divmod(max(0, int(total_seconds)), 3600)
    minutes, seconds = divmod(rem, 60)
    return hours, minutes, seconds


class TriggerEditorWindow(QWidget):
    """Framed trigger-editor tool window.

    Public API (for integration/tests): ``toggle()``, ``apply()``,
    ``new_trigger()``, ``duplicate_current()``, ``delete_current()``,
    ``revert_current()``, ``run_test()``, ``select_trigger(id)``,
    ``current_trigger()``, ``item_for(id)``, ``folder_names()``,
    ``trigger_ids()``, plus the named form widgets (``name_edit``,
    ``search_edit``, ...).
    """

    def __init__(
        self,
        settings: Settings,
        engine: TriggerEngine,
        on_save: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._engine = engine
        self._on_save = on_save

        self._working: list[Trigger] = [
            t.model_copy(deep=True) for t in (settings.triggers or engine.triggers)
        ]
        self._current: Trigger | None = None
        self._loaded_values: dict[str, Any] | None = None
        self._loading = False
        self._dirty = False
        #: User group names with no triggers yet — kept only for this session,
        #: dropped once a trigger lands in them (or on close).
        self._extra_groups: set[str] = set()
        #: Set False (e.g. in tests) to skip the unsaved-changes prompt on close.
        self.confirm_unsaved = True
        #: Set False (e.g. in tests) to skip the delete-group confirmation prompt.
        self.confirm_delete = True

        self.setWindowTitle("Trigger Editor")
        self.setWindowFlags(Qt.WindowType.Window)
        self._restore_geometry()

        self._build_ui()
        self._rebuild_tree()
        self._load_form(None)

    # -- window state ----------------------------------------------------------

    def _window_state(self) -> WindowState:
        state = self._settings.windows.get(WINDOW_KEY)
        if state is None:
            state = WindowState(frameless=False, always_on_top=False, shown=False)
            self._settings.windows[WINDOW_KEY] = state
        return state

    def _restore_geometry(self) -> None:
        state = self._window_state()
        self.setGeometry(*(state.geometry or DEFAULT_GEOMETRY))

    def _persist_geometry(self) -> None:
        state = self._window_state()
        geo = self.geometry()
        state.geometry = (geo.x(), geo.y(), geo.width(), geo.height())
        state.shown = False
        self._on_save()

    def toggle(self) -> None:
        if self.isVisible():
            self.close()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    # -- UI construction --------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        root.addWidget(splitter, 1)

        self.tree = QTreeWidget(self)
        self.tree.setHeaderHidden(True)
        self.tree.currentItemChanged.connect(self._on_current_item_changed)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        splitter.addWidget(self.tree)

        right = QWidget(self)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        self.editor_pane = QWidget(right)
        pane_layout = QVBoxLayout(self.editor_pane)
        pane_layout.setContentsMargins(0, 0, 0, 0)
        self._build_form(pane_layout)

        scroll = QScrollArea(right)
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.editor_pane)
        right_layout.addWidget(scroll, 1)

        right_layout.addWidget(self._build_test_box(right))
        root.addLayout(self._build_buttons())

    def _build_form(self, layout: QVBoxLayout) -> None:
        general = QGroupBox("Trigger", self.editor_pane)
        form = QFormLayout(general)
        self.name_edit = QLineEdit()
        form.addRow("Name", self.name_edit)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search text ({c}, {word} tokens supported)")
        form.addRow("Search text", self.search_edit)
        self.regex_check = QCheckBox("Use regex")
        form.addRow("", self.regex_check)
        self.zone_combo = QComboBox()
        self.zone_combo.addItem("Everywhere", "")
        zonedb = load_zone_database()
        for key, info in sorted(zonedb.zones.items(), key=lambda kv: kv[1].name.lower()):
            self.zone_combo.addItem(info.name, key)
        form.addRow("Zone", self.zone_combo)
        self.group_combo = QComboBox()
        self.group_combo.setEditable(True)
        self.group_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.group_combo.activated.connect(self._on_group_changed)
        self.group_combo.lineEdit().editingFinished.connect(self._on_group_changed)
        form.addRow("Group", self.group_combo)
        self.comments_edit = QPlainTextEdit()
        self.comments_edit.setMaximumHeight(60)
        form.addRow("Comments", self.comments_edit)
        layout.addWidget(general)

        basic = QGroupBox("Basic output", self.editor_pane)
        form = QFormLayout(basic)
        self.basic_display_check = QCheckBox("Show display text")
        form.addRow("", self.basic_display_check)
        self.basic_display_edit = QLineEdit()
        form.addRow("Display text", self.basic_display_edit)
        self.basic_color_combo = QComboBox()
        for name in COLOR_NAMES:
            self.basic_color_combo.addItem(name, name)
        form.addRow("Text color", self.basic_color_combo)
        self.basic_tts_check = QCheckBox("Text to speech")
        form.addRow("", self.basic_tts_check)
        self.basic_tts_edit = QLineEdit()
        form.addRow("TTS text", self.basic_tts_edit)
        layout.addWidget(basic)

        timer = QGroupBox("Timer", self.editor_pane)
        form = QFormLayout(timer)
        self.timer_type_combo = QComboBox()
        for label, value in _TIMER_TYPES:
            self.timer_type_combo.addItem(label, value)
        form.addRow("Type", self.timer_type_combo)
        self.timer_name_edit = QLineEdit()
        self.timer_name_edit.setPlaceholderText("Defaults to the trigger name")
        form.addRow("Timer name", self.timer_name_edit)
        duration_row = QHBoxLayout()
        self.timer_hours_spin = _duration_spin(23, "h")
        self.timer_minutes_spin = _duration_spin(59, "m")
        self.timer_seconds_spin = _duration_spin(59, "s")
        for spin in (self.timer_hours_spin, self.timer_minutes_spin, self.timer_seconds_spin):
            duration_row.addWidget(spin)
        duration_row.addStretch(1)
        form.addRow("Duration", duration_row)
        self.timer_color_combo = QComboBox()
        for name in COLOR_NAMES:
            self.timer_color_combo.addItem(name, name)
        form.addRow("Bar color", self.timer_color_combo)
        self.timer_overlay_check = QCheckBox("Show in overlay")
        form.addRow("", self.timer_overlay_check)
        self.timer_restart_combo = QComboBox()
        for label, value in _RESTART_BEHAVIORS:
            self.timer_restart_combo.addItem(label, value)
        form.addRow("Restart behavior", self.timer_restart_combo)
        layout.addWidget(timer)

        ending = QGroupBox("Timer Ending warning", self.editor_pane)
        form = QFormLayout(ending)
        self.ending_enabled_check = QCheckBox("Enabled")
        form.addRow("", self.ending_enabled_check)
        self.ending_seconds_spin = QSpinBox()
        self.ending_seconds_spin.setRange(0, 86400)
        self.ending_seconds_spin.setSuffix(" s before end")
        form.addRow("Warn at", self.ending_seconds_spin)
        self.ending_text_edit = QLineEdit()
        form.addRow("Display text", self.ending_text_edit)
        self.ending_tts_edit = QLineEdit()
        form.addRow("TTS text", self.ending_tts_edit)
        layout.addWidget(ending)

        ended = QGroupBox("Timer Ended", self.editor_pane)
        form = QFormLayout(ended)
        self.ended_enabled_check = QCheckBox("Enabled")
        form.addRow("", self.ended_enabled_check)
        self.ended_text_edit = QLineEdit()
        form.addRow("Display text", self.ended_text_edit)
        self.ended_tts_edit = QLineEdit()
        form.addRow("TTS text", self.ended_tts_edit)
        layout.addWidget(ended)

        counter = QGroupBox("Counter", self.editor_pane)
        form = QFormLayout(counter)
        self.counter_reset_check = QCheckBox("Reset {COUNTER} after inactivity")
        form.addRow("", self.counter_reset_check)
        reset_row = QHBoxLayout()
        self.counter_hours_spin = _duration_spin(23, "h")
        self.counter_minutes_spin = _duration_spin(59, "m")
        self.counter_seconds_spin = _duration_spin(59, "s")
        for spin in (self.counter_hours_spin, self.counter_minutes_spin, self.counter_seconds_spin):
            reset_row.addWidget(spin)
        reset_row.addStretch(1)
        form.addRow("Reset after", reset_row)
        layout.addWidget(counter)
        layout.addStretch(1)

    def _build_test_box(self, parent: QWidget) -> QGroupBox:
        box = QGroupBox("Test", parent)
        layout = QVBoxLayout(box)
        row = QHBoxLayout()
        self.test_line_edit = QLineEdit()
        self.test_line_edit.setPlaceholderText("Paste a log line…")
        row.addWidget(self.test_line_edit, 1)
        self.test_button = QPushButton("Test")
        self.test_button.clicked.connect(self.run_test)
        row.addWidget(self.test_button)
        layout.addLayout(row)
        self.test_result = QLabel("")
        self.test_result.setWordWrap(True)
        layout.addWidget(self.test_result)
        return box

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.new_button = QPushButton("New Trigger")
        self.new_button.clicked.connect(self.new_trigger)
        row.addWidget(self.new_button)
        self.duplicate_button = QPushButton("Duplicate")
        self.duplicate_button.clicked.connect(self.duplicate_current)
        row.addWidget(self.duplicate_button)
        self.delete_button = QPushButton("Delete")
        self.delete_button.clicked.connect(self.delete_current)
        row.addWidget(self.delete_button)
        self.revert_button = QPushButton("Revert built-in")
        self.revert_button.clicked.connect(self.revert_current)
        row.addWidget(self.revert_button)
        self.import_button = QPushButton("Import…")
        self.import_button.clicked.connect(self.import_triggers)
        row.addWidget(self.import_button)
        self.export_button = QPushButton("Export…")
        self.export_button.clicked.connect(self.export_triggers)
        row.addWidget(self.export_button)
        row.addStretch(1)
        self.apply_button = QPushButton("Apply / Save")
        self.apply_button.clicked.connect(self.apply)
        row.addWidget(self.apply_button)
        return row

    # -- tree -------------------------------------------------------------------

    @staticmethod
    def _group_key(trigger: Trigger) -> str:
        if trigger.is_built_in:
            return trigger.built_in_folder or "Built-in"
        category = (trigger.category or "").strip()
        if not category or category == "Default":
            return "Custom"
        return category

    @staticmethod
    def _item_label(trigger: Trigger) -> str:
        name = trigger.trigger_name or "(unnamed)"
        if trigger.is_built_in and trigger.customized:
            return f"{name} (customized)"
        return name

    def _make_folder_item(self, key: str) -> QTreeWidgetItem:
        folder = QTreeWidgetItem([key])
        # Selectable so Export… can target a whole folder.
        folder.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        folder.setData(0, _ROLE_FOLDER_KIND, "user")
        self.tree.addTopLevelItem(folder)
        return folder

    def _rebuild_tree(self, select_id: str | None = None) -> None:
        self._loading = True
        try:
            self.tree.clear()
            folders: dict[str, QTreeWidgetItem] = {}
            ordered = sorted(
                self._working,
                key=lambda t: (self._group_key(t).lower(), (t.trigger_name or "").lower()),
            )
            for trigger in ordered:
                key = self._group_key(trigger)
                folder = folders.get(key)
                if folder is None:
                    folder = self._make_folder_item(key)
                    folders[key] = folder
                if trigger.is_built_in:
                    folder.setData(0, _ROLE_FOLDER_KIND, "builtin")
                # A group with real content is no longer a placeholder.
                self._extra_groups.discard(key)
                item = QTreeWidgetItem([self._item_label(trigger)])
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    0,
                    Qt.CheckState.Checked if trigger.trigger_enabled else Qt.CheckState.Unchecked,
                )
                item.setData(0, _ROLE_ID, trigger.trigger_id)
                folder.addChild(item)
            # Empty user groups created this session get a bare folder row.
            for key in sorted(self._extra_groups):
                if key not in folders:
                    folders[key] = self._make_folder_item(key)
            self.tree.expandAll()
        finally:
            self._loading = False
        if select_id is not None:
            self.select_trigger(select_id)

    def _trigger_by_id(self, trigger_id: str) -> Trigger | None:
        return next((t for t in self._working if t.trigger_id == trigger_id), None)

    def item_for(self, trigger_id: str) -> QTreeWidgetItem | None:
        for i in range(self.tree.topLevelItemCount()):
            folder = self.tree.topLevelItem(i)
            for j in range(folder.childCount()):
                item = folder.child(j)
                if item.data(0, _ROLE_ID) == trigger_id:
                    return item
        return None

    def folder_names(self) -> list[str]:
        return [self.tree.topLevelItem(i).text(0) for i in range(self.tree.topLevelItemCount())]

    # -- groups ------------------------------------------------------------------

    def user_group_names(self) -> list[str]:
        """Sorted group names over user triggers plus empty session groups."""
        names = {self._group_key(t) for t in self._working if not t.is_built_in}
        names.update(self._extra_groups)
        return sorted(names)

    def create_group(self, name: str) -> None:
        """Register an empty user group so it can be picked before it has rows."""
        name = name.strip()
        if not name:
            return
        self._extra_groups.add(name)
        self._rebuild_tree()

    def move_trigger_to_group(self, trigger_id: str, group: str) -> bool:
        """Reassign a user trigger's group. Built-ins refuse (return False)."""
        trigger = self._trigger_by_id(trigger_id)
        if trigger is None or trigger.is_built_in:
            return False
        trigger.category = group.strip() or "Custom"
        self._dirty = True
        self._extra_groups.discard(group.strip())
        self._rebuild_tree(select_id=trigger_id)
        return True

    def rename_group(self, old: str, new: str) -> bool:
        """Rename a user group. Refuses if it holds any built-in trigger."""
        members = [t for t in self._working if self._group_key(t) == old]
        if any(t.is_built_in for t in members):
            return False
        new_name = new.strip() or "Custom"
        for trigger in members:
            trigger.category = new_name
        if old in self._extra_groups:
            self._extra_groups.discard(old)
            self._extra_groups.add(new_name)
        self._dirty = True
        self._rebuild_tree()
        return True

    def delete_group(self, group: str) -> bool:
        """Delete a user group and all its triggers. Refuses if it holds a built-in."""
        members = [t for t in self._working if self._group_key(t) == group]
        if any(t.is_built_in for t in members):
            return False
        current_removed = self._current in members
        for trigger in members:
            self._working.remove(trigger)
        self._extra_groups.discard(group)
        if current_removed:
            self._current = None
            self._loaded_values = None
        self._dirty = True
        self._rebuild_tree()
        if current_removed:
            self._load_form(None)
        return True

    def trigger_ids(self) -> list[str]:
        ids: list[str] = []
        for i in range(self.tree.topLevelItemCount()):
            folder = self.tree.topLevelItem(i)
            ids.extend(folder.child(j).data(0, _ROLE_ID) for j in range(folder.childCount()))
        return ids

    def select_trigger(self, trigger_id: str) -> None:
        item = self.item_for(trigger_id)
        if item is not None:
            self.tree.setCurrentItem(item)

    def current_trigger(self) -> Trigger | None:
        return self._current

    def _on_current_item_changed(
        self, current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None
    ) -> None:
        if self._loading:
            return
        self._commit_form()
        trigger_id = current.data(0, _ROLE_ID) if current is not None else None
        self._load_form(self._trigger_by_id(trigger_id) if trigger_id else None)

    def _on_item_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        if self._loading:
            return
        trigger_id = item.data(0, _ROLE_ID)
        if not trigger_id:
            return
        trigger = self._trigger_by_id(trigger_id)
        if trigger is None:
            return
        enabled = item.checkState(0) == Qt.CheckState.Checked
        if trigger.trigger_enabled != enabled:
            trigger.trigger_enabled = enabled
            self._dirty = True

    # -- context menu ------------------------------------------------------------

    def _on_tree_context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        global_pos = self.tree.viewport().mapToGlobal(pos)
        trigger_id = item.data(0, _ROLE_ID)
        if trigger_id:
            trigger = self._trigger_by_id(trigger_id)
            if trigger is None or trigger.is_built_in:
                return  # built-in triggers cannot be moved
            self._show_trigger_context_menu(trigger, global_pos)
        elif item.data(0, _ROLE_FOLDER_KIND) == "user":
            self._show_folder_context_menu(item.text(0), global_pos)

    def _show_trigger_context_menu(self, trigger: Trigger, global_pos) -> None:
        menu = QMenu(self.tree)
        submenu = menu.addMenu("Move to group")
        actions: dict[Any, str] = {}
        for name in self.user_group_names():
            if name == self._group_key(trigger):
                continue
            actions[submenu.addAction(name)] = name
        submenu.addSeparator()
        new_action = submenu.addAction("New group…")
        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        if chosen is new_action:
            name, ok = QInputDialog.getText(self, "New group", "Group name:")
            if ok and name.strip():
                self.move_trigger_to_group(trigger.trigger_id, name.strip())
        elif chosen in actions:
            self.move_trigger_to_group(trigger.trigger_id, actions[chosen])

    def _show_folder_context_menu(self, group: str, global_pos) -> None:
        menu = QMenu(self.tree)
        rename_action = menu.addAction("Rename group…")
        delete_action = menu.addAction("Delete group…")
        new_action = menu.addAction("New group…")
        chosen = menu.exec(global_pos)
        if chosen is rename_action:
            name, ok = QInputDialog.getText(self, "Rename group", "New name:", text=group)
            if ok and name.strip():
                self.rename_group(group, name.strip())
        elif chosen is delete_action:
            self._delete_group_prompt(group)
        elif chosen is new_action:
            name, ok = QInputDialog.getText(self, "New group", "Group name:")
            if ok and name.strip():
                self.create_group(name.strip())

    def _delete_group_prompt(self, group: str) -> None:
        if self.confirm_delete:
            count = sum(1 for t in self._working if self._group_key(t) == group)
            choice = QMessageBox.question(
                self,
                "Delete group",
                f"Delete group '{group}' and its {count} trigger(s)?",
            )
            if choice != QMessageBox.StandardButton.Yes:
                return
        if not self.delete_group(group):
            QMessageBox.information(
                self,
                "Delete group",
                "Built-in trigger groups cannot be deleted.",
            )

    # -- form load/commit ---------------------------------------------------------

    def _values_from(self, trigger: Trigger) -> dict[str, Any]:
        basic = trigger.effective_basic()
        timer = trigger.timer or TriggerTimer(timer_type=TimerType.NO_TIMER)
        if timer.hours or timer.minutes or timer.seconds:
            th, tm, ts = timer.hours, timer.minutes, timer.seconds
        else:
            th, tm, ts = _hms(timer.duration)
        ending = trigger.timer_ending or TriggerTimerEnding(enabled=False, seconds=0)
        ended = trigger.timer_ended or TriggerTimerEnded()
        counter = trigger.counter or TriggerCounter()
        return {
            "name": trigger.trigger_name,
            "search_text": trigger.search_text,
            "use_regex": trigger.effective_use_regex,
            "zone": (trigger.zone or "").lower(),
            "comments": trigger.comments,
            "basic_display_enabled": basic.display_text_enabled,
            "basic_display_text": basic.display_text,
            "basic_display_color": basic.display_text_color or "Red",
            "basic_tts_enabled": basic.audio_type == TriggerAudioType.TEXT_TO_SPEECH,
            "basic_tts_text": basic.tts_text,
            "timer_type": timer.timer_type.value,
            "timer_name": timer.timer_name,
            "timer_hours": th,
            "timer_minutes": tm,
            "timer_seconds": ts,
            "timer_color": timer.bar_color or "MediumPurple",
            "timer_overlay": timer.show_in_overlay,
            "timer_restart": timer.restart_behavior.value,
            "ending_enabled": ending.enabled,
            "ending_seconds": int(ending.threshold),
            "ending_text": ending.output.display_text,
            "ending_tts": ending.output.tts_text,
            "ended_enabled": ended.enabled,
            "ended_text": ended.output.display_text,
            "ended_tts": ended.output.tts_text,
            "counter_reset": counter.reset_enabled,
            "counter_hours": counter.hours,
            "counter_minutes": counter.minutes,
            "counter_seconds": counter.seconds,
        }

    def _load_form(self, trigger: Trigger | None) -> None:
        self._loading = True
        try:
            self._current = trigger
            self.editor_pane.setEnabled(trigger is not None)
            self.test_button.setEnabled(trigger is not None)
            self.duplicate_button.setEnabled(trigger is not None)
            self.delete_button.setEnabled(trigger is not None)
            self.delete_button.setText(
                "Disable" if trigger is not None and trigger.is_built_in else "Delete"
            )
            self.revert_button.setEnabled(trigger is not None and trigger.is_built_in)
            self.test_result.setText("")
            if trigger is None:
                self._loaded_values = None
                return
            values = self._values_from(trigger)
            self.name_edit.setText(values["name"])
            self.search_edit.setText(values["search_text"])
            self.regex_check.setChecked(values["use_regex"])
            _set_combo(self.zone_combo, values["zone"])
            self._populate_group_combo(trigger)
            self.comments_edit.setPlainText(values["comments"])
            self.basic_display_check.setChecked(values["basic_display_enabled"])
            self.basic_display_edit.setText(values["basic_display_text"])
            _set_combo(self.basic_color_combo, values["basic_display_color"])
            self.basic_tts_check.setChecked(values["basic_tts_enabled"])
            self.basic_tts_edit.setText(values["basic_tts_text"])
            _set_combo(self.timer_type_combo, values["timer_type"])
            self.timer_name_edit.setText(values["timer_name"])
            self.timer_hours_spin.setValue(values["timer_hours"])
            self.timer_minutes_spin.setValue(values["timer_minutes"])
            self.timer_seconds_spin.setValue(values["timer_seconds"])
            _set_combo(self.timer_color_combo, values["timer_color"])
            self.timer_overlay_check.setChecked(values["timer_overlay"])
            _set_combo(self.timer_restart_combo, values["timer_restart"])
            self.ending_enabled_check.setChecked(values["ending_enabled"])
            self.ending_seconds_spin.setValue(values["ending_seconds"])
            self.ending_text_edit.setText(values["ending_text"])
            self.ending_tts_edit.setText(values["ending_tts"])
            self.ended_enabled_check.setChecked(values["ended_enabled"])
            self.ended_text_edit.setText(values["ended_text"])
            self.ended_tts_edit.setText(values["ended_tts"])
            self.counter_reset_check.setChecked(values["counter_reset"])
            self.counter_hours_spin.setValue(values["counter_hours"])
            self.counter_minutes_spin.setValue(values["counter_minutes"])
            self.counter_seconds_spin.setValue(values["counter_seconds"])
            # Snapshot what the widgets actually hold so commit can detect edits.
            self._loaded_values = self._form_values()
        finally:
            self._loading = False

    def _populate_group_combo(self, trigger: Trigger) -> None:
        """Fill the group combo for the loaded trigger (called under _loading).

        Built-ins show their read-only ``built_in_folder``; user triggers get
        the editable list of user group names. This lives OUTSIDE the
        _form_values diff machinery — group moves rebuild the tree.
        """
        self.group_combo.clear()
        if trigger.is_built_in:
            label = trigger.built_in_folder or "Built-in"
            self.group_combo.addItem(label)
            self.group_combo.setEnabled(False)
            self.group_combo.setCurrentText(label)
            return
        self.group_combo.setEnabled(True)
        for name in self.user_group_names():
            self.group_combo.addItem(name)
        self.group_combo.setCurrentText(self._group_key(trigger))

    def _on_group_changed(self, *_args: Any) -> None:
        """Move the current user trigger when its group combo is edited.

        No-op while loading, for built-ins, or when the text is unchanged —
        never runs inside the _commit_form re-entrancy.
        """
        if self._loading:
            return
        trigger = self._current
        if trigger is None or trigger.is_built_in:
            return
        group = self.group_combo.currentText().strip()
        if not group or group == self._group_key(trigger):
            return
        self.move_trigger_to_group(trigger.trigger_id, group)

    def _form_values(self) -> dict[str, Any]:
        return {
            "name": self.name_edit.text(),
            "search_text": self.search_edit.text(),
            "use_regex": self.regex_check.isChecked(),
            "zone": self.zone_combo.currentData() or "",
            "comments": self.comments_edit.toPlainText(),
            "basic_display_enabled": self.basic_display_check.isChecked(),
            "basic_display_text": self.basic_display_edit.text(),
            "basic_display_color": self.basic_color_combo.currentData(),
            "basic_tts_enabled": self.basic_tts_check.isChecked(),
            "basic_tts_text": self.basic_tts_edit.text(),
            "timer_type": self.timer_type_combo.currentData(),
            "timer_name": self.timer_name_edit.text(),
            "timer_hours": self.timer_hours_spin.value(),
            "timer_minutes": self.timer_minutes_spin.value(),
            "timer_seconds": self.timer_seconds_spin.value(),
            "timer_color": self.timer_color_combo.currentData(),
            "timer_overlay": self.timer_overlay_check.isChecked(),
            "timer_restart": self.timer_restart_combo.currentData(),
            "ending_enabled": self.ending_enabled_check.isChecked(),
            "ending_seconds": self.ending_seconds_spin.value(),
            "ending_text": self.ending_text_edit.text(),
            "ending_tts": self.ending_tts_edit.text(),
            "ended_enabled": self.ended_enabled_check.isChecked(),
            "ended_text": self.ended_text_edit.text(),
            "ended_tts": self.ended_tts_edit.text(),
            "counter_reset": self.counter_reset_check.isChecked(),
            "counter_hours": self.counter_hours_spin.value(),
            "counter_minutes": self.counter_minutes_spin.value(),
            "counter_seconds": self.counter_seconds_spin.value(),
        }

    @staticmethod
    def _apply_form(trigger: Trigger, values: dict[str, Any]) -> None:
        trigger.trigger_name = values["name"]
        trigger.search_text = values["search_text"]
        trigger.use_regex = values["use_regex"]
        trigger.zone = values["zone"] or None
        trigger.comments = values["comments"]

        basic = trigger.basic
        if basic is None:
            basic = trigger.effective_basic().model_copy(deep=True)
        basic.display_text_enabled = values["basic_display_enabled"]
        basic.display_text = values["basic_display_text"]
        basic.display_text_color = values["basic_display_color"]
        basic.audio_type = (
            TriggerAudioType.TEXT_TO_SPEECH
            if values["basic_tts_enabled"]
            else TriggerAudioType.NONE
        )
        basic.tts_text = values["basic_tts_text"]
        trigger.basic = basic

        timer = trigger.timer if trigger.timer is not None else TriggerTimer()
        timer.timer_type = TimerType(values["timer_type"])
        timer.timer_name = values["timer_name"]
        timer.hours = values["timer_hours"]
        timer.minutes = values["timer_minutes"]
        timer.seconds = values["timer_seconds"]
        timer.duration_seconds = None  # the h/m/s parts are authoritative now
        timer.bar_color = values["timer_color"]
        timer.show_in_overlay = values["timer_overlay"]
        timer.restart_behavior = TimerRestartBehavior(values["timer_restart"])
        trigger.timer = timer

        ending = trigger.timer_ending if trigger.timer_ending is not None else TriggerTimerEnding()
        ending.enabled = values["ending_enabled"]
        ending.hours, ending.minutes, ending.seconds = _hms(values["ending_seconds"])
        _apply_output(ending.output, values["ending_text"], values["ending_tts"])
        trigger.timer_ending = ending

        ended = trigger.timer_ended if trigger.timer_ended is not None else TriggerTimerEnded()
        ended.enabled = values["ended_enabled"]
        _apply_output(ended.output, values["ended_text"], values["ended_tts"])
        trigger.timer_ended = ended

        counter = trigger.counter if trigger.counter is not None else TriggerCounter()
        counter.reset_enabled = values["counter_reset"]
        counter.hours = values["counter_hours"]
        counter.minutes = values["counter_minutes"]
        counter.seconds = values["counter_seconds"]
        trigger.counter = counter

    def _commit_form(self) -> bool:
        """Write pending form edits into the current working trigger.

        Returns True when something actually changed; a built-in gains the
        ``customized`` marker (enabled toggles live in the tree and never
        pass through here, matching EQTool's sync semantics).
        """
        trigger = self._current
        if trigger is None or self._loaded_values is None:
            return False
        values = self._form_values()
        if values == self._loaded_values:
            return False
        self._apply_form(trigger, values)
        if trigger.is_built_in:
            trigger.customized = True
        self._loaded_values = values
        self._dirty = True
        item = self.item_for(trigger.trigger_id)
        if item is not None:
            self._loading = True
            try:
                item.setText(0, self._item_label(trigger))
            finally:
                self._loading = False
        return True

    # -- actions ---------------------------------------------------------------

    def _selected_user_group(self) -> str | None:
        """The user group the current selection sits in, if any."""
        item = self.tree.currentItem()
        if item is None:
            return None
        if item.data(0, _ROLE_ID):
            item = item.parent()
        if item is not None and item.data(0, _ROLE_FOLDER_KIND) == "user":
            return item.text(0)
        return None

    def new_trigger(self) -> None:
        self._commit_form()
        trigger = Trigger(
            trigger_enabled=True,
            trigger_name="New Trigger",
            category=self._selected_user_group() or "Custom",
            search_text="",
            use_regex=False,
            basic=TriggerOutput(display_text_enabled=True, display_text_color="Red"),
            timer=TriggerTimer(timer_type=TimerType.NO_TIMER),
            timer_ending=TriggerTimerEnding(),
            timer_ended=TriggerTimerEnded(),
            counter=TriggerCounter(),
        )
        self._working.append(trigger)
        self._dirty = True
        self._rebuild_tree(select_id=trigger.trigger_id)

    def duplicate_current(self) -> None:
        self._commit_form()
        source = self._current
        if source is None:
            return
        copy = source.model_copy(deep=True)
        copy.trigger_id = str(uuid.uuid4())
        copy.trigger_name = f"{source.trigger_name} (copy)"
        copy.is_built_in = False
        copy.built_in_id = None
        copy.customized = False
        copy.built_in_folder = ""
        # Built-in copies land in Custom; a user copy keeps its source's group.
        copy.category = "Custom" if source.is_built_in else (source.category or "Custom")
        # Mark as an intentional copy so sync_builtin_triggers never merges it
        # back into the built-in it was copied from.
        copy.built_in_folder_path = "Custom"
        self._working.append(copy)
        self._dirty = True
        self._rebuild_tree(select_id=copy.trigger_id)

    def delete_current(self) -> None:
        trigger = self._current
        if trigger is None:
            return
        if trigger.is_built_in:
            # Built-ins cannot be deleted (EQTool parity) — disable instead.
            if trigger.trigger_enabled:
                trigger.trigger_enabled = False
                self._dirty = True
                item = self.item_for(trigger.trigger_id)
                if item is not None:
                    self._loading = True
                    try:
                        item.setCheckState(0, Qt.CheckState.Unchecked)
                    finally:
                        self._loading = False
            return
        self._working.remove(trigger)
        self._current = None
        self._loaded_values = None
        self._dirty = True
        self._rebuild_tree()
        self._load_form(None)

    def revert_current(self) -> None:
        self._commit_form()
        trigger = self._current
        if trigger is None or not trigger.is_built_in or not trigger.customized:
            return
        trigger.customized = False
        self._current = None
        self._loaded_values = None
        self._working, _changed = sync_builtin_triggers(self._working)
        self._dirty = True
        self._rebuild_tree(select_id=trigger.trigger_id)

    # -- export / import -------------------------------------------------------

    def _export_selection(self) -> tuple[list[Trigger], str]:
        """Triggers to export for the current tree selection + a file stem.

        A selected trigger exports alone (even a built-in); a selected folder
        exports its triggers; no selection exports everything. Folder/all
        scope drops pristine built-ins — every install already ships those.
        """
        item = self.tree.currentItem()
        if item is not None:
            trigger_id = item.data(0, _ROLE_ID)
            if trigger_id:
                trigger = self._trigger_by_id(trigger_id)
                if trigger is not None:
                    return [trigger], trigger.trigger_name or "trigger"
            elif item.parent() is None:
                ids = [item.child(j).data(0, _ROLE_ID) for j in range(item.childCount())]
                triggers = [t for tid in ids if (t := self._trigger_by_id(tid)) is not None]
                shareable = [t for t in triggers if not t.is_built_in or t.customized]
                return shareable, item.text(0)
        shareable = [t for t in self._working if not t.is_built_in or t.customized]
        return shareable, "triggers"

    def export_triggers(self) -> None:
        self._commit_form()
        triggers, stem = self._export_selection()
        if not triggers:
            QMessageBox.information(
                self,
                "Export Triggers",
                "Nothing to export here — unmodified built-in triggers are part of "
                "every install, so they are left out of exports.",
            )
            return
        path, _selected = QFileDialog.getSaveFileName(
            self, "Export Triggers", f"{stem}.json", "Trigger files (*.json)"
        )
        if not path:
            return
        try:
            Path(path).write_text(json.dumps(dump_triggers(triggers), indent=2), encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Export Triggers", f"Could not write the file:\n{exc}")
            return
        QMessageBox.information(
            self, "Export Triggers", f"Exported {len(triggers)} trigger(s) to {path}"
        )

    @staticmethod
    def _parse_import(raw: bytes) -> tuple[list[Trigger], int]:
        """Dispatch by content: GINA (.gtp zip or XML) vs our JSON format."""
        if raw[:4] == b"PK\x03\x04" or raw.lstrip()[:1] == b"<":
            return load_gina_triggers(raw)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError(f"not a trigger file: {exc}") from exc
        return parse_triggers(data), 0

    def import_triggers(self) -> None:
        self._commit_form()
        path, _selected = QFileDialog.getOpenFileName(
            self,
            "Import Triggers",
            "",
            "Trigger files (*.json *.gtp *.xml);;All files (*)",
        )
        if not path:
            return
        try:
            incoming, unreadable = self._parse_import(Path(path).read_bytes())
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Import Triggers", f"Could not import triggers:\n{exc}")
            return
        existing = {
            ((t.trigger_name or "").lower(), (t.search_text or "").lower()) for t in self._working
        }
        added = 0
        duplicates = 0
        for trigger in incoming:
            key = ((trigger.trigger_name or "").lower(), (trigger.search_text or "").lower())
            if key in existing:
                duplicates += 1
                continue
            existing.add(key)
            self._working.append(sanitize_imported(trigger))
            added += 1
        if added:
            self._dirty = True
            self._rebuild_tree()
        skipped = []
        if duplicates:
            skipped.append(f"{duplicates} duplicate(s)")
        if unreadable:
            skipped.append(f"{unreadable} unreadable entr{'y' if unreadable == 1 else 'ies'}")
        message = f"Imported {added} trigger(s)"
        if skipped:
            message += f" — skipped {', '.join(skipped)}"
        if added:
            message += ".\n\nClick Apply / Save to keep them."
        QMessageBox.information(self, "Import Triggers", message)

    def apply(self) -> None:
        """Push all in-memory edits into settings + engine and persist."""
        self._commit_form()
        self._settings.triggers = [t.model_copy(deep=True) for t in self._working]
        self._engine.set_triggers(list(self._settings.triggers))
        self._on_save()
        self._dirty = False

    def _discard_changes(self) -> None:
        self._working = [t.model_copy(deep=True) for t in self._settings.triggers]
        self._current = None
        self._loaded_values = None
        self._dirty = False
        self._rebuild_tree()
        self._load_form(None)

    # -- test box ----------------------------------------------------------------

    def _test_player_name(self) -> str:
        if self._settings.players:
            return self._settings.players[0].name
        return "You"

    def run_test(self) -> None:
        trigger = self._current
        if trigger is None:
            return
        # Probe a copy carrying the current (possibly uncommitted) form values
        # so the working trigger's runtime state is never disturbed.
        probe = trigger.model_copy(deep=True)
        self._apply_form(probe, self._form_values())
        probe.player_name = self._test_player_name()
        line = self.test_line_edit.text()
        if not probe.matches(line):
            self.test_result.setText("No match.")
            return
        probe.current_counter += 1
        basic = probe.effective_basic()
        parts = ["Matched."]
        display = probe.expand(basic.display_text)
        if display:
            parts.append(f"Display: {display}")
        tts = probe.expand(basic.tts_text)
        if tts:
            parts.append(f"TTS: {tts}")
        self.test_result.setText("  ".join(parts))

    # -- close handling -------------------------------------------------------------

    def closeEvent(self, event) -> None:
        self._commit_form()
        if self._dirty and self.confirm_unsaved:
            choice = QMessageBox.question(
                self,
                "Trigger Editor",
                "You have unsaved trigger changes. Save them?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if choice == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if choice == QMessageBox.StandardButton.Save:
                self.apply()
            else:
                self._discard_changes()
        self._persist_geometry()
        super().closeEvent(event)


def _duration_spin(maximum: int, suffix: str) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(0, maximum)
    spin.setSuffix(f" {suffix}")
    return spin


def _apply_output(output: TriggerOutput, display_text: str, tts_text: str) -> None:
    """Fill a Timer Ending/Ended output block from its two form fields."""
    output.display_text = display_text
    output.display_text_enabled = bool(display_text.strip())
    output.tts_text = tts_text
    output.audio_type = (
        TriggerAudioType.TEXT_TO_SPEECH if tts_text.strip() else TriggerAudioType.NONE
    )
