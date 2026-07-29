"""Macro Editor — browse, edit, share, and copy a character's socials.

The EQ client keeps macros ("socials") per character, in the same
``<Name>_<ServerSuffix>.ini`` files that hold the friends list. This window
makes that set editable, portable between your own characters, and shareable
as a macro pack.

Like the trigger editor this is a normal framed tool window, not a frameless
overlay, so it skips ``OverlayWindowBase`` and rolls minimal geometry
persistence into ``Settings.windows["macroeditor"]``.

Two safety rules shape the UX, both stronger than the friends page's:

* The client **rewrites the whole character ini** when you camp or log out,
  silently discarding anything edited while it is running. Saving warns (and
  continues — never blocks) when :func:`eq_is_running` says so.
* Every file is copied into ``socials_backup/`` before its first write.

Edits live on a working copy; only **Save to character** touches the ini.
The one exception is **Copy to character(s)…**, which writes its targets
immediately, exactly like the friends push — its confirm dialog says so.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from nparseplus.config.settings import Settings, WindowState
from nparseplus.core import eqini, socialstore
from nparseplus.core import socials as socials_core
from nparseplus.core.eqprocess import eq_is_running
from nparseplus.core.socials import (
    DuplicateGroup,
    Placement,
    Social,
    SocialGrid,
)
from nparseplus.core.socials_exchange import (
    dump_socials,
    pack_label,
    parse_socials,
    sanitize_all,
)
from nparseplus.core.socialstore import SocialOrigin, SocialStore

WINDOW_KEY = "macroeditor"
DEFAULT_GEOMETRY = (220, 140, 940, 660)

HINT_STYLE = "color: #888888; font-size: 11px;"
GRID_COLUMNS = 2

#: A glyph rather than colour alone, so the badge survives both themes and
#: reads for colour-blind users.
ORIGIN_BADGES = {
    SocialOrigin.GAME: ("▢", "From game"),
    SocialOrigin.LOCAL: ("✎", "Created in nParse+"),
    SocialOrigin.IMPORTED: ("↧", "Imported"),
}
DUPLICATE_BADGE = "⧉"

ORIGIN_FILTERS: list[tuple[str, SocialOrigin | None]] = [
    ("All macros", None),
    ("From game", SocialOrigin.GAME),
    ("Created here", SocialOrigin.LOCAL),
    ("Imported", SocialOrigin.IMPORTED),
]

EXPORT_SCOPES: list[tuple[str, frozenset[SocialOrigin] | None]] = [
    ("Only what I authored", frozenset({SocialOrigin.LOCAL, SocialOrigin.IMPORTED})),
    ("Everything", None),
]

#: What to do with an imported macro whose slot is already taken.
CONFLICT_OVERWRITE = "overwrite"
CONFLICT_SKIP = "skip"
CONFLICT_FREE = "free"

#: What to do with an imported macro the character already has elsewhere.
DUPLICATE_SKIP = "skip"
DUPLICATE_PLACE = "place"

ConflictResolver = Callable[[Social, Social], str]
DuplicateResolver = Callable[[Social, Social], str]


def _slot_label(page: int, button: int) -> str:
    return f"P{page}·B{button}"


class MacroEditorWindow(QWidget):
    """Framed macro-editor tool window.

    Public API (for integration/tests): ``toggle()``, ``load()``,
    ``select_slot()``, ``social_at()``, ``grid()``, ``store()``,
    ``duplicate_groups()``, ``save_to_character()``, ``copy_to()``,
    ``import_pack()``, ``export_pack()``, ``restore_from_local_copy()``.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        on_save: Callable[[], None],
        store_dir: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._on_save = on_save
        self._store_dir = Path(store_dir) if store_dir is not None else None

        self._working: list[Social] = []
        self._grid = SocialGrid()
        self._store: SocialStore = socialstore.new_store("", "", now=datetime.now())
        self._store_error = ""
        self._current: tuple[int, int] | None = None
        self._loading = False
        self._dirty = False
        #: Slots edited in this window since the last save — they become
        #: origin LOCAL, while untouched pack arrivals stay IMPORTED.
        self._edited: set[tuple[int, int]] = set()
        self._imported: dict[tuple[int, int], str] = {}
        self._buttons: dict[tuple[int, int], QPushButton] = {}

        #: Set False (e.g. in tests) to skip the unsaved-changes prompt.
        self.confirm_unsaved = True
        #: Set False (e.g. in tests) to skip the EQ-is-running warning.
        self.warn_eq_running = True

        self.setWindowTitle("Macro Editor")
        self.setWindowFlags(Qt.WindowType.Window)
        self._restore_geometry()

        self._build_ui()
        self._refresh_characters()
        self._render()

    # -- window state ---------------------------------------------------------

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

    # -- UI construction ------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        top = QHBoxLayout()
        self.server_combo = QComboBox(self)
        self.server_combo.addItems(list(eqini.SERVER_SUFFIXES))
        self.server_combo.currentIndexChanged.connect(lambda _i: self._refresh_characters())
        self.character_combo = QComboBox(self)
        self.character_combo.setMinimumWidth(160)
        self.load_button = QPushButton("Load", self)
        self.load_button.clicked.connect(self.load)
        self.filter_combo = QComboBox(self)
        for label, _origin in ORIGIN_FILTERS:
            self.filter_combo.addItem(label)
        self.filter_combo.currentIndexChanged.connect(lambda _i: self._render())
        top.addWidget(QLabel("Server", self))
        top.addWidget(self.server_combo)
        top.addWidget(QLabel("Character", self))
        top.addWidget(self.character_combo)
        top.addWidget(self.load_button)
        top.addSpacing(12)
        top.addWidget(QLabel("Show", self))
        top.addWidget(self.filter_combo)
        top.addStretch(1)
        root.addLayout(top)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.left_tabs = QTabWidget(self)
        self.page_tabs = QTabWidget(self)
        self.left_tabs.addTab(self.page_tabs, "Grid")
        self.left_tabs.addTab(self._build_library_tab(), "Local library")
        splitter.addWidget(self.left_tabs)
        splitter.addWidget(self._build_form())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 1)

        buttons = QHBoxLayout()
        self.save_button = QPushButton("Save to character", self)
        self.save_button.clicked.connect(self.save_to_character)
        self.copy_button = QPushButton("Copy to character(s)…", self)
        self.copy_button.clicked.connect(self._prompt_copy)
        self.import_button = QPushButton("Import…", self)
        self.import_button.clicked.connect(self._prompt_import)
        self.export_button = QPushButton("Export…", self)
        self.export_button.clicked.connect(self._prompt_export)
        self.duplicates_button = QPushButton("Find duplicates…", self)
        self.duplicates_button.clicked.connect(self._show_duplicates)
        for widget in (
            self.save_button,
            self.copy_button,
            self.import_button,
            self.export_button,
            self.duplicates_button,
        ):
            buttons.addWidget(widget)
        buttons.addStretch(1)
        close_button = QPushButton("Close", self)
        close_button.clicked.connect(self.close)
        buttons.addWidget(close_button)
        root.addLayout(buttons)

        self.status = QLabel("Load a character to see their macros.", self)
        self.status.setWordWrap(True)
        self.status.setStyleSheet(HINT_STYLE)
        root.addWidget(self.status)

    def _build_library_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        self.library_tree = QTreeWidget(page)
        self.library_tree.setColumnCount(4)
        self.library_tree.setHeaderLabels(["Macro", "Slot", "Source", "Updated"])
        self.library_tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.library_tree.itemSelectionChanged.connect(self._library_selection_changed)
        layout.addWidget(self.library_tree, 1)
        row = QHBoxLayout()
        self.library_status = QLabel("", page)
        self.library_status.setWordWrap(True)
        self.library_status.setStyleSheet(HINT_STYLE)
        self.restore_button = QPushButton("Restore from local copy", page)
        self.restore_button.clicked.connect(self.restore_from_local_copy)
        row.addWidget(self.library_status, 1)
        row.addWidget(self.restore_button)
        layout.addLayout(row)
        return page

    def _build_form(self) -> QWidget:
        box = QGroupBox("Macro", self)
        form = QFormLayout(box)
        self.slot_label = QLabel("No macro selected", box)
        form.addRow(self.slot_label)
        self.name_edit = QLineEdit(box)
        self.name_edit.setMaxLength(64)
        self.name_edit.textChanged.connect(self._commit_form)
        form.addRow("Name", self.name_edit)
        self.color_spin = QSpinBox(box)
        self.color_spin.setRange(0, socials_core.MAX_COLOR)
        self.color_spin.setToolTip(
            "The client's own colour index for the button label. "
            "nParse+ shows the raw number because the P99 palette mapping "
            "is not verified — a wrong swatch would be worse than an honest index."
        )
        self.color_spin.valueChanged.connect(self._commit_form)
        form.addRow("Color (client index)", self.color_spin)

        self.line_edits: list[QLineEdit] = []
        for index in range(socials_core.MAX_LINES):
            edit = QLineEdit(box)
            edit.setPlaceholderText("/assist" if index == 0 else "")
            edit.textChanged.connect(self._commit_form)
            self.line_edits.append(edit)
            form.addRow(f"Line {index + 1}", edit)

        self.origin_label = QLabel("", box)
        self.origin_label.setStyleSheet(HINT_STYLE)
        self.origin_label.setWordWrap(True)
        form.addRow(self.origin_label)

        self.clear_button = QPushButton("Clear this macro", box)
        self.clear_button.clicked.connect(self._clear_current)
        form.addRow(self.clear_button)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(box)
        return scroll

    # -- character discovery --------------------------------------------------

    def _eq_dir(self) -> Path | None:
        install = self._settings.general.eq_install_dir
        return Path(install).expanduser() if install else None

    def _suffix(self) -> str:
        return eqini.SERVER_SUFFIXES[self.server_combo.currentText()]

    def _character_files(self) -> list[Path]:
        eq_dir = self._eq_dir()
        if eq_dir is None:
            return []
        return eqini.character_ini_files(eq_dir, self._suffix())

    def _refresh_characters(self) -> None:
        current = self.character_combo.currentText()
        self.character_combo.clear()
        suffix = self._suffix()
        for path in self._character_files():
            self.character_combo.addItem(eqini.character_name(path, suffix), str(path))
        index = self.character_combo.findText(current)
        if index >= 0:
            self.character_combo.setCurrentIndex(index)
        self._update_enabled()

    def _current_path(self) -> Path | None:
        data = self.character_combo.currentData()
        return Path(data) if data else None

    def _current_character(self) -> str:
        return self.character_combo.currentText()

    def _preflight_reason(self) -> str | None:
        return eqini.preflight(self._eq_dir())

    def _update_enabled(self) -> None:
        reason = self._preflight_reason()
        usable = reason is None and self._current_path() is not None
        for widget in (self.save_button, self.copy_button, self.import_button):
            widget.setEnabled(usable)
        self.export_button.setEnabled(bool(self._working))
        self.restore_button.setEnabled(usable and bool(self._store.lost()))

    # -- loading --------------------------------------------------------------

    def load(self) -> None:
        """Read the selected character's socials and sync the local mirror."""
        reason = self._preflight_reason()
        if reason is not None:
            self.status.setText(reason)
            self._update_enabled()
            return
        path = self._current_path()
        if path is None:
            self.status.setText("No character ini files found for this server.")
            self._update_enabled()
            return

        self._grid = socials_core.read_socials(path)
        self._working = [s.model_copy(deep=True) for s in self._grid.socials]
        self._edited.clear()
        self._imported.clear()
        self._dirty = False
        self._current = None

        now = datetime.now()
        self._store = self._load_store() or socialstore.new_store(
            self._current_character(), self.server_combo.currentText(), now=now
        )
        report = socialstore.sync_from_game(self._store, self._grid, now=now)
        self._save_store()

        self._render()
        detail = []
        if report.added:
            detail.append(f"{len(report.added)} new")
        if report.changed:
            detail.append(f"{len(report.changed)} changed since last seen")
        if report.lost:
            detail.append(f"{len(report.lost)} no longer in the file")
        suffix = f" ({', '.join(detail)})" if detail else ""
        self.status.setText(
            f"Loaded {len(self._working)} macro(s) from {path.name}{suffix}."
            + (f" {self._store_error}" if self._store_error else "")
        )

    # -- local mirror ---------------------------------------------------------

    def _store_path(self) -> Path | None:
        if self._store_dir is None:
            return None
        return socialstore.store_path(self._store_dir, self._current_character(), self._suffix())

    def _load_store(self) -> SocialStore | None:
        path = self._store_path()
        if path is None:
            return None
        return socialstore.load_store(path)

    def _save_store(self) -> None:
        """Persist the mirror. A failure is reported but never blocks a write."""
        path = self._store_path()
        if path is None:
            self._store_error = ""
            return
        try:
            socialstore.save_store(path, self._store)
        except OSError as exc:
            self._store_error = f"Local copy not updated: {exc}."
        else:
            self._store_error = ""

    # -- rendering ------------------------------------------------------------

    def grid(self) -> SocialGrid:
        """The working grid: discovered dimensions with the in-memory macros."""
        return self._grid.model_copy(update={"socials": list(self._working)})

    def store(self) -> SocialStore:
        return self._store

    def social_at(self, page: int, button: int) -> Social | None:
        for social in self._working:
            if social.slot == (page, button):
                return social
        return None

    def duplicate_groups(self) -> list[DuplicateGroup]:
        return socials_core.find_duplicates(self._working)

    def _duplicate_slots(self) -> dict[tuple[int, int], DuplicateGroup]:
        mapping: dict[tuple[int, int], DuplicateGroup] = {}
        for group in self.duplicate_groups():
            for social in group.socials:
                mapping[social.slot] = group
        return mapping

    def _active_filter(self) -> SocialOrigin | None:
        index = max(0, self.filter_combo.currentIndex())
        return ORIGIN_FILTERS[index][1]

    def _render(self) -> None:
        self._render_grid()
        self._render_library()
        self._render_status_counts()
        self._update_enabled()

    def _render_grid(self) -> None:
        selected = self._current
        self.page_tabs.clear()
        self._buttons.clear()
        duplicates = self._duplicate_slots()
        wanted = self._active_filter()

        for page in range(self._grid.page_origin, self._grid.page_origin + self._grid.pages):
            tab = QWidget(self)
            layout = QGridLayout(tab)
            buttons = range(
                self._grid.button_origin,
                self._grid.button_origin + self._grid.buttons_per_page,
            )
            for offset, button in enumerate(buttons):
                widget = self._make_slot_button(page, button, duplicates, wanted)
                layout.addWidget(widget, offset // GRID_COLUMNS, offset % GRID_COLUMNS)
                self._buttons[(page, button)] = widget
            self.page_tabs.addTab(tab, f"Page {page}")

        if selected is not None and selected in self._buttons:
            self.select_slot(*selected)
        else:
            self._load_form(None)

    def _make_slot_button(
        self,
        page: int,
        button: int,
        duplicates: dict[tuple[int, int], DuplicateGroup],
        wanted: SocialOrigin | None,
    ) -> QPushButton:
        social = self.social_at(page, button)
        origin = self._store.origin_at(page, button)
        badge, origin_text = ORIGIN_BADGES[origin]

        if social is None:
            label = f"{_slot_label(page, button)}   (empty)"
            tooltip = "Empty slot"
        else:
            marks = badge
            if (page, button) in duplicates:
                marks += DUPLICATE_BADGE
            label = f"{_slot_label(page, button)} {marks}  {social.name or '(unnamed)'}"
            tooltip = f"{origin_text}\n" + "\n".join(social.lines)
            group = duplicates.get((page, button))
            if group is not None:
                others = [
                    _slot_label(*other.slot)
                    for other in group.socials
                    if other.slot != (page, button)
                ]
                tooltip += (
                    f"\nDuplicate ({group.kind.value.replace('_', ' ')}) of {', '.join(others)}"
                )

        widget = QPushButton(label, self)
        widget.setToolTip(tooltip)
        widget.setCheckable(True)
        widget.clicked.connect(lambda _checked=False, p=page, b=button: self.select_slot(p, b))
        # The filter dims rather than hides, so slot positions stay readable.
        dimmed = wanted is not None and (social is None or origin is not wanted)
        if dimmed:
            widget.setStyleSheet("color: #777777;")
        widget.setProperty("dimmed", dimmed)
        return widget

    def _render_library(self) -> None:
        self.library_tree.clear()
        by_origin: dict[SocialOrigin, list] = {}
        for record in self._store.records:
            by_origin.setdefault(record.origin, []).append(record)

        for origin in (SocialOrigin.LOCAL, SocialOrigin.IMPORTED, SocialOrigin.GAME):
            records = by_origin.get(origin)
            if not records:
                continue
            parent = QTreeWidgetItem([ORIGIN_BADGES[origin][1], "", "", ""])
            self.library_tree.addTopLevelItem(parent)
            parent.setExpanded(True)
            for record in records:
                slot = _slot_label(*record.slot) if record.in_file else "not in file"
                child = QTreeWidgetItem(
                    [
                        record.social.name or "(unnamed)",
                        slot,
                        record.source_label,
                        record.updated_at.strftime("%Y-%m-%d %H:%M"),
                    ]
                )
                child.setData(0, Qt.ItemDataRole.UserRole, record.slot)
                parent.addChild(child)

        lost = self._store.lost()
        self.library_status.setText(
            f"{len(lost)} macro(s) this tool wrote are no longer in the character's file."
            if lost
            else "Every macro in the local copy is present in the character's file."
        )

    def _render_status_counts(self) -> None:
        if not self._store.records and not self._working:
            return
        groups = self.duplicate_groups()
        lost = self._store.lost()
        parts = [
            f"{self._grid.pages * self._grid.buttons_per_page} slots",
            f"{len(self._working)} macros",
            f"{len(groups)} duplicate group(s)",
        ]
        if lost:
            parts.append(f"{len(lost)} not in file")
        if self._dirty:
            parts.append("unsaved changes")
        if self._store_error:
            parts.append(self._store_error)
        self.status.setText(" · ".join(parts))

    def _library_selection_changed(self) -> None:
        items = self.library_tree.selectedItems()
        if not items:
            return
        slot = items[0].data(0, Qt.ItemDataRole.UserRole)
        if slot is not None and self.social_at(*slot) is not None:
            self.select_slot(*slot)

    # -- form -----------------------------------------------------------------

    def select_slot(self, page: int, button: int) -> None:
        self._current = (page, button)
        for slot, widget in self._buttons.items():
            widget.setChecked(slot == (page, button))
        self._load_form(self.social_at(page, button))
        if self.left_tabs.currentIndex() == 0:
            index = page - self._grid.page_origin
            if 0 <= index < self.page_tabs.count():
                self.page_tabs.setCurrentIndex(index)

    def _load_form(self, social: Social | None) -> None:
        self._loading = True
        try:
            if self._current is None:
                self.slot_label.setText("No macro selected")
            else:
                self.slot_label.setText(f"Slot {_slot_label(*self._current)}")
            self.name_edit.setText(social.name if social else "")
            self.color_spin.setValue(social.color if social else socials_core.DEFAULT_COLOR)
            lines = list(social.lines) if social else []
            for index, edit in enumerate(self.line_edits):
                edit.setText(lines[index] if index < len(lines) else "")
            enabled = self._current is not None
            self.name_edit.setEnabled(enabled)
            self.color_spin.setEnabled(enabled)
            self.clear_button.setEnabled(enabled and social is not None)
            for edit in self.line_edits:
                edit.setEnabled(enabled)
            self._render_origin_label()
        finally:
            self._loading = False

    def _render_origin_label(self) -> None:
        if self._current is None:
            self.origin_label.setText("")
            return
        record = self._store.at(*self._current)
        if record is None:
            self.origin_label.setText("Not in the local copy yet.")
            return
        _badge, text = ORIGIN_BADGES[record.origin]
        detail = f"Last written by: {text.lower()}"
        if record.source_label:
            detail += f" ({record.source_label})"
        if not record.in_file:
            detail += " — no longer in the character's file"
        self.origin_label.setText(detail)

    def _commit_form(self) -> None:
        if self._loading or self._current is None:
            return
        page, button = self._current
        lines = [edit.text() for edit in self.line_edits]
        candidate = Social(
            page=page,
            button=button,
            name=self.name_edit.text(),
            color=self.color_spin.value(),
            lines=lines,
        )
        cleaned = socials_core.normalize_socials([candidate])
        self._working = [s for s in self._working if s.slot != (page, button)]
        if cleaned:
            self._working.append(cleaned[0])
        self._working.sort(key=lambda s: s.slot)
        self._edited.add((page, button))
        self._imported.pop((page, button), None)
        self._dirty = True
        self._refresh_slot_button(page, button)
        self._render_status_counts()

    def _refresh_slot_button(self, page: int, button: int) -> None:
        widget = self._buttons.get((page, button))
        if widget is None:
            return
        social = self.social_at(page, button)
        duplicates = self._duplicate_slots()
        origin = self._store.origin_at(page, button)
        badge, origin_text = ORIGIN_BADGES[origin]
        if social is None:
            widget.setText(f"{_slot_label(page, button)}   (empty)")
            widget.setToolTip("Empty slot")
            return
        marks = badge + (DUPLICATE_BADGE if (page, button) in duplicates else "")
        widget.setText(f"{_slot_label(page, button)} {marks}  {social.name or '(unnamed)'}")
        widget.setToolTip(f"{origin_text}\n" + "\n".join(social.lines))

    def _clear_current(self) -> None:
        if self._current is None:
            return
        self._loading = True
        try:
            self.name_edit.setText("")
            self.color_spin.setValue(socials_core.DEFAULT_COLOR)
            for edit in self.line_edits:
                edit.setText("")
        finally:
            self._loading = False
        self._commit_form()
        self._load_form(None)

    # -- saving ---------------------------------------------------------------

    def _confirm_eq_running(self, action: str) -> bool:
        if not self.warn_eq_running or not eq_is_running():
            return True
        answer = QMessageBox.warning(
            self,
            "EverQuest looks like it is running",
            "The client rewrites these ini files when you camp or log out, "
            f"which will discard these edits. {action} anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Yes

    def save_to_character(self) -> bool:
        """Write the working copy to the selected character's ini."""
        path = self._current_path()
        if path is None:
            self.status.setText("No character selected.")
            return False
        if not self._confirm_eq_running("Save"):
            return False

        errors = socials_core.push_socials([path], self._working, clear_missing=True)
        if errors:
            self.status.setText("Save failed: " + "; ".join(errors))
            return False

        now = datetime.now()
        origins = {slot: SocialOrigin.LOCAL for slot in self._edited}
        origins.update({slot: SocialOrigin.IMPORTED for slot in self._imported})
        socialstore.mark_written(
            self._store,
            self._working,
            origin=SocialOrigin.GAME,
            now=now,
            origins=origins,
            source_labels=dict(self._imported),
        )
        written_slots = {s.slot for s in self._working}
        socialstore.forget_slots(
            self._store,
            [record.slot for record in self._store.records if record.slot not in written_slots],
            now=now,
        )
        self._save_store()

        self._edited.clear()
        self._imported.clear()
        self._dirty = False
        self._render()
        self.status.setText(
            f"Saved {len(self._working)} macro(s) to {path.name} "
            f"(original backed up to {socials_core.BACKUP_DIR_NAME}/)."
            + (f" {self._store_error}" if self._store_error else "")
        )
        return True

    # -- copy to other characters --------------------------------------------

    def copy_to(self, names: Sequence[str], *, replace: bool) -> list[str]:
        """Write the working copy straight to other characters' ini files."""
        suffix = self._suffix()
        source = self._current_path()
        targets = [
            path
            for path in self._character_files()
            if eqini.character_name(path, suffix) in set(names) and path != source
        ]
        if not targets:
            self.status.setText("No matching target characters.")
            return []

        errors = socials_core.push_socials(targets, self._working, clear_missing=replace)
        now = datetime.now()
        label = self._current_character()
        for path in targets:
            name = eqini.character_name(path, suffix)
            store = self._target_store(name, now)
            socialstore.mark_written(
                store,
                self._working,
                origin=SocialOrigin.IMPORTED,
                now=now,
                source_label=label,
            )
            self._save_target_store(name, store)

        if errors:
            self.status.setText("Some files failed: " + "; ".join(errors))
        else:
            verb = "Replaced" if replace else "Merged into"
            self.status.setText(
                f"{verb} {len(targets)} character(s) with {len(self._working)} macro(s) "
                f"(originals backed up to {socials_core.BACKUP_DIR_NAME}/)."
            )
        return errors

    def _target_store(self, character: str, now: datetime) -> SocialStore:
        if self._store_dir is None:
            return socialstore.new_store(character, self.server_combo.currentText(), now=now)
        path = socialstore.store_path(self._store_dir, character, self._suffix())
        return socialstore.load_store(path) or socialstore.new_store(
            character, self.server_combo.currentText(), now=now
        )

    def _save_target_store(self, character: str, store: SocialStore) -> None:
        if self._store_dir is None:
            return
        path = socialstore.store_path(self._store_dir, character, self._suffix())
        try:
            socialstore.save_store(path, store)
        except OSError as exc:
            self._store_error = f"Local copy not updated: {exc}."

    def _prompt_copy(self) -> None:
        suffix = self._suffix()
        source = self._current_path()
        others = [
            eqini.character_name(path, suffix) for path in self._character_files() if path != source
        ]
        if not others:
            QMessageBox.information(
                self, "Copy macros", "No other characters found on this server."
            )
            return
        dialog = _CopyDialog(others, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        chosen = dialog.selected()
        if not chosen:
            return
        replace = dialog.replace()
        verb = "replace" if replace else "merge into"
        answer = QMessageBox.question(
            self,
            "Copy macros",
            f"This writes immediately and will {verb} the macros of: "
            f"{', '.join(chosen)}.\n\nOriginals are backed up to "
            f"{socials_core.BACKUP_DIR_NAME}/. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if not self._confirm_eq_running("Copy"):
            return
        self.copy_to(chosen, replace=replace)

    # -- import / export ------------------------------------------------------

    def export_pack(self, path: Path, *, origins: frozenset[SocialOrigin] | None) -> int:
        """Write a macro pack; returns how many macros it holds."""
        socials = self._working
        if origins is not None:
            socials = [s for s in socials if self._store.origin_at(*s.slot) in origins]
        label = f"{self._current_character()} ({self.server_combo.currentText()})"
        payload = dump_socials(socials, label=label)
        Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return len(socials)

    def import_pack(
        self,
        raw: bytes,
        *,
        conflict_resolver: ConflictResolver | None = None,
        duplicate_resolver: DuplicateResolver | None = None,
    ) -> dict[str, int]:
        """Merge a macro pack into the working copy.

        Duplicates and conflicts are different things and are reported apart:
        a macro the character already has *somewhere* is a duplicate; a
        different macro occupying the target slot is a conflict.
        """
        data = json.loads(raw.decode("utf-8", errors="replace"))
        incoming = sanitize_all(parse_socials(data))
        label = pack_label(data)

        summary = {"imported": 0, "duplicates": 0, "overwritten": 0, "skipped": 0, "unplaceable": 0}
        existing_keys = {socials_core.social_key(s): s for s in self._working}

        survivors: list[Social] = []
        for social in incoming:
            twin = existing_keys.get(socials_core.social_key(social))
            if twin is None:
                survivors.append(social)
                continue
            summary["duplicates"] += 1
            choice = duplicate_resolver(social, twin) if duplicate_resolver else DUPLICATE_SKIP
            if choice == DUPLICATE_PLACE:
                survivors.append(social)
            else:
                summary["skipped"] += 1

        for social in survivors:
            result = socials_core.place_socials([social], self.grid(), strategy=Placement.EXACT)
            if result.unplaceable:
                summary["unplaceable"] += 1
                continue
            if result.conflicts:
                occupant = self.social_at(social.page, social.button)
                choice = conflict_resolver(social, occupant) if conflict_resolver else CONFLICT_SKIP
                if choice == CONFLICT_SKIP:
                    summary["skipped"] += 1
                    continue
                if choice == CONFLICT_FREE:
                    moved = socials_core.place_socials(
                        [social], self.grid(), strategy=Placement.FREE
                    )
                    if not moved.placed:
                        summary["unplaceable"] += 1
                        continue
                    social = moved.placed[0]
                else:
                    summary["overwritten"] += 1
            self._place(social, label)
            summary["imported"] += 1

        if summary["imported"]:
            self._dirty = True
        self._render()
        return summary

    def _place(self, social: Social, label: str) -> None:
        self._working = [s for s in self._working if s.slot != social.slot]
        self._working.append(social)
        self._working.sort(key=lambda s: s.slot)
        self._imported[social.slot] = label
        self._edited.discard(social.slot)

    def _prompt_export(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        scope = _pick_export_scope(self)
        if scope is None:
            return
        stem = self._current_character() or "macros"
        path, _selected = QFileDialog.getSaveFileName(
            self, "Export Macros", f"{stem}-macros.json", "Macro packs (*.json)"
        )
        if not path:
            return
        try:
            count = self.export_pack(Path(path), origins=scope)
        except OSError as exc:
            QMessageBox.warning(self, "Export Macros", f"Could not write the file:\n{exc}")
            return
        QMessageBox.information(self, "Export Macros", f"Exported {count} macro(s) to {path}")

    def _prompt_import(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path, _selected = QFileDialog.getOpenFileName(
            self, "Import Macros", "", "Macro packs (*.json);;All files (*)"
        )
        if not path:
            return
        try:
            raw = Path(path).read_bytes()
        except OSError as exc:
            QMessageBox.warning(self, "Import Macros", f"Could not read the file:\n{exc}")
            return
        try:
            summary = self.import_pack(
                raw,
                conflict_resolver=self._ask_conflict,
                duplicate_resolver=self._ask_duplicate,
            )
        except (ValueError, UnicodeDecodeError) as exc:
            QMessageBox.warning(self, "Import Macros", f"That is not a macro pack:\n{exc}")
            return

        parts = [f"Imported {summary['imported']} macro(s)"]
        if summary["duplicates"]:
            parts.append(f"{summary['duplicates']} already present")
        if summary["overwritten"]:
            parts.append(f"{summary['overwritten']} overwritten")
        if summary["skipped"]:
            parts.append(f"{summary['skipped']} skipped")
        if summary["unplaceable"]:
            parts.append(f"{summary['unplaceable']} did not fit")
        QMessageBox.information(
            self,
            "Import Macros",
            " — ".join(parts) + ".\n\nClick Save to character to write them.",
        )

    def _ask_conflict(self, incoming: Social, occupant: Social | None) -> str:
        held = occupant.name if occupant else "(unnamed)"
        box = QMessageBox(self)
        box.setWindowTitle("Slot already used")
        box.setText(
            f"{_slot_label(incoming.page, incoming.button)} already holds “{held}”.\n"
            f"Importing “{incoming.name}”."
        )
        overwrite = box.addButton("Overwrite", QMessageBox.ButtonRole.DestructiveRole)
        free = box.addButton("Move to free slot", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Skip", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is overwrite:
            return CONFLICT_OVERWRITE
        if clicked is free:
            return CONFLICT_FREE
        return CONFLICT_SKIP

    def _ask_duplicate(self, incoming: Social, twin: Social) -> str:
        answer = QMessageBox.question(
            self,
            "Macro already present",
            f"“{incoming.name}” is already on {_slot_label(*twin.slot)}.\n\n"
            "Place another copy anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return DUPLICATE_PLACE if answer == QMessageBox.StandardButton.Yes else DUPLICATE_SKIP

    # -- restore --------------------------------------------------------------

    def restore_from_local_copy(self) -> int:
        """Put back macros the local mirror has but the character's file lost."""
        lost = self._store.lost()
        if not lost:
            self.status.setText("Nothing to restore — the file has every macro we know about.")
            return 0
        for record in lost:
            self._working = [s for s in self._working if s.slot != record.slot]
            self._working.append(record.social)
            self._imported.setdefault(record.slot, "local copy")
        self._working.sort(key=lambda s: s.slot)
        self._dirty = True
        self._render()
        self.status.setText(
            f"Restored {len(lost)} macro(s) from the local copy. "
            "Click Save to character to write them."
        )
        return len(lost)

    # -- duplicates -----------------------------------------------------------

    def _show_duplicates(self) -> None:
        groups = self.duplicate_groups()
        if not groups:
            QMessageBox.information(self, "Find duplicates", "No duplicate macros found.")
            return
        dialog = _DuplicatesDialog(groups, self)
        dialog.exec()

    # -- close ----------------------------------------------------------------

    def closeEvent(self, event) -> None:
        if self._dirty and self.confirm_unsaved:
            choice = QMessageBox.question(
                self,
                "Macro Editor",
                "You have unsaved macro changes. Save them to the character?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if choice == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if choice == QMessageBox.StandardButton.Save:
                self.save_to_character()
            else:
                self._dirty = False
        self._persist_geometry()
        super().closeEvent(event)


def _pick_export_scope(parent: QWidget) -> frozenset[SocialOrigin] | None:
    """Ask what to export; None when cancelled."""
    box = QMessageBox(parent)
    box.setWindowTitle("Export Macros")
    box.setText("Which macros should the pack contain?")
    buttons = []
    for label, origins in EXPORT_SCOPES:
        buttons.append((box.addButton(label, QMessageBox.ButtonRole.AcceptRole), origins))
    box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    clicked = box.clickedButton()
    for button, origins in buttons:
        if clicked is button:
            return origins
    return None


class _CopyDialog(QDialog):
    """Pick target characters and whether to replace or merge."""

    def __init__(self, names: Sequence[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Copy macros to characters")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Copy this character's macros to:", self))
        self._list = QListWidget(self)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        for name in names:
            self._list.addItem(QListWidgetItem(name))
        layout.addWidget(self._list, 1)
        self._mode = QComboBox(self)
        self._mode.addItem("Replace their macros", True)
        self._mode.addItem("Merge into their macros", False)
        layout.addWidget(self._mode)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected(self) -> list[str]:
        return [item.text() for item in self._list.selectedItems()]

    def replace(self) -> bool:
        return bool(self._mode.currentData())


class _DuplicatesDialog(QDialog):
    """A finder, not a fixer — it lists groups and jumps to a slot."""

    def __init__(self, groups: Sequence[DuplicateGroup], parent: MacroEditorWindow) -> None:
        super().__init__(parent)
        self._editor = parent
        self.setWindowTitle("Duplicate macros")
        self.resize(460, 380)
        layout = QVBoxLayout(self)
        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Macro", "Slot"])
        for group in groups:
            kind = group.kind.value.replace("_", " ")
            parent_item = QTreeWidgetItem([f"{len(group.socials)} macros — {kind}", ""])
            self.tree.addTopLevelItem(parent_item)
            parent_item.setExpanded(True)
            for social in group.socials:
                child = QTreeWidgetItem([social.name or "(unnamed)", _slot_label(*social.slot)])
                child.setData(0, Qt.ItemDataRole.UserRole, social.slot)
                parent_item.addChild(child)
        self.tree.itemDoubleClicked.connect(self._jump)
        layout.addWidget(self.tree, 1)
        hint = QLabel(
            "Double-click a macro to open its slot. Nothing is changed here — "
            "clear a slot from the editor if you want it gone.",
            self,
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(HINT_STYLE)
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def _jump(self, item: QTreeWidgetItem, _column: int) -> None:
        slot = item.data(0, Qt.ItemDataRole.UserRole)
        if slot is not None:
            self._editor.select_slot(*slot)
