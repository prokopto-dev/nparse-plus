"""Character Dumps — browse the stored ``/outputfile`` snapshots.

The library itself is Qt-free (:mod:`nparseplus.core.dumps`); this is the
menu over it. Left: a tree of characters, each with an Inventory and a
Spellbook branch, each holding that character's snapshots newest-first — one
current version per character *per kind*, with the previous few behind it.
Right: the selected snapshot's contents, filterable, above a line saying what
changed since the snapshot before it.

Like the trigger and macro editors this is a normal framed tool window rather
than a frameless overlay, so it takes ``OverlayWindowBase`` for the geometry
persistence and overrides the body-drag mouse handlers away.

**The window never imports anything itself.** Both import buttons hand the
request to :class:`~nparseplus.core.dumps.DumpWatcher`, whose driver-thread
tick does the work and publishes the bus events; the tree just refreshes off
a timer. That keeps every publish on the driver thread, which is the rule the
bus depends on. With no watcher (tests, or a backend built without one) the
window falls back to importing directly and no events fire.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from nparseplus.config.settings import Settings, WindowState
from nparseplus.core.dumps import (
    CharacterDump,
    DumpKind,
    DumpLibrary,
    DumpWatcher,
    SnapshotRef,
    diff_dumps,
    read_dump_file,
    render_dump_text,
)
from nparseplus.core.handlers.inventory_upload import InventoryUploadHandler
from nparseplus.ui import chromewidgets
from nparseplus.ui.overlaybase import OverlayWindowBase

WINDOW_KEY = "dumps"
DEFAULT_GEOMETRY = (240, 160, 900, 600)

#: How often the tree re-reads the library while the window is open. Cheap (a
#: directory scan, no file reads) and it is what makes an auto-import that
#: lands while you are looking at the window simply appear.
REFRESH_MS = 2000

#: Qt.ItemDataRole slots for what a tree row points at.
_ROLE_REF = int(Qt.ItemDataRole.UserRole)
_ROLE_CHARACTER = int(Qt.ItemDataRole.UserRole) + 1
_ROLE_KIND = int(Qt.ItemDataRole.UserRole) + 2

_INVENTORY_HEADERS = ("Location", "Item", "Count", "ID")
_SPELLBOOK_HEADERS = ("Level", "Spell")


class CharacterDumpsWindow(chromewidgets.ChromeMixin, OverlayWindowBase):
    """The dump library's browser.

    Public API (integration/tests): ``toggle()``, ``refresh()``,
    ``select_snapshot()``, ``current_dump()``, ``import_now()``,
    ``import_file()``, ``delete_selected()``, ``export_selected()``.
    """

    def __init__(
        self,
        settings: Settings,
        library: DumpLibrary,
        *,
        on_save: Callable[[], None],
        watcher: DumpWatcher | None = None,
        uploader: InventoryUploadHandler | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            settings=settings,
            window_key=WINDOW_KEY,
            title="Character Dumps",
            default_geometry=DEFAULT_GEOMETRY,
            on_save=on_save,
            # Framed and never on top, like the other tool windows.
            default_state=WindowState(frameless=False, always_on_top=False, shown=False),
            translucent=False,
            parent=parent,
        )
        self.library = library
        self.watcher = watcher
        self.uploader = uploader
        self._current: SnapshotRef | None = None
        self._dump: CharacterDump | None = None
        self._loading = False
        #: Set False in tests to skip confirmation dialogs.
        self.confirm_destructive = True

        self._build_ui()
        self.refresh()
        self.apply_chrome()

        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._on_timer)
        self._timer.start()

    # -- window state -----------------------------------------------------

    def toggle(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.refresh()
            self.show()
            self.raise_()
            self.activateWindow()
        self.persist_state()

    # A tree, a filter field and a table: dragging the window by its body
    # would eat selection and scrolling. Same recipe as the macro editor.
    def mousePressEvent(self, event) -> None:
        QWidget.mousePressEvent(self, event)

    def mouseMoveEvent(self, event) -> None:
        QWidget.mouseMoveEvent(self, event)

    def mouseReleaseEvent(self, event) -> None:
        QWidget.mouseReleaseEvent(self, event)

    def mouseDoubleClickEvent(self, event) -> None:
        QWidget.mouseDoubleClickEvent(self, event)

    # -- construction -----------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        root.addLayout(self._build_toolbar())

        self._tree = QTreeWidget(self)
        self._tree.setHeaderLabels(["Character", "Taken"])
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._tree.currentItemChanged.connect(lambda *_: self._on_tree_selection())
        tree_header = self._tree.header()
        if tree_header is not None:
            # The timestamp is fixed-width and the whole point of the column;
            # give it exactly what it needs and let the names take the rest.
            tree_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            tree_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)

        detail = QWidget(self)
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        self._detail_title = QLabel("", detail)
        self._detail_title.setObjectName(chromewidgets.chrome.TITLE)
        detail_layout.addWidget(self._detail_title)
        self._filter = QLineEdit(detail)
        self._filter.setPlaceholderText("Filter entries…")
        self._filter.textChanged.connect(lambda _text: self._render_entries())
        detail_layout.addWidget(self._filter)
        self._entries = QTreeWidget(detail)
        self._entries.setRootIsDecorated(False)
        self._entries.setAlternatingRowColors(True)
        self._entries.setHeaderLabels(list(_INVENTORY_HEADERS))
        detail_layout.addWidget(self._entries, 1)
        self._change_label = chromewidgets.hint("", detail)
        self._change_label.setWordWrap(True)
        detail_layout.addWidget(self._change_label)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(self._tree)
        splitter.addWidget(detail)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        # Enough for a character name plus a full "YYYY-MM-DD HH:MM" stamp;
        # the stretch factors take over once the user resizes.
        splitter.setSizes([320, 580])
        root.addWidget(splitter, 1)

        self._status = chromewidgets.hint("", self)
        self._status.setWordWrap(True)
        root.addWidget(self._status)

    def _build_toolbar(self) -> QHBoxLayout:
        """The menu strip: the two toggles, retention, and the actions.

        The toggles live here rather than in Settings because this is where
        you are when you wonder why a dump did or didn't show up. They write
        straight through to ``settings.dumps`` and save on the spot.
        """
        dumps = self._settings.dumps
        bar = QHBoxLayout()

        self.auto_import_box = QCheckBox("Auto-import", self)
        self.auto_import_box.setChecked(dumps.auto_import)
        self.auto_import_box.setToolTip(
            "Watch the EQ directory for /outputfile inventory and spellbook "
            "dumps and pull in ones this library has never seen."
        )
        self.auto_import_box.toggled.connect(self._on_auto_import_toggled)
        bar.addWidget(self.auto_import_box)

        self.auto_update_box = QCheckBox("Auto-update", self)
        self.auto_update_box.setChecked(dumps.auto_update)
        self.auto_update_box.setToolTip(
            "When a dump this library already tracks changes, store the new "
            "version as another snapshot. Off keeps the first one."
        )
        self.auto_update_box.toggled.connect(self._on_auto_update_toggled)
        bar.addWidget(self.auto_update_box)

        bar.addWidget(QLabel("Keep", self))
        self.keep_spin = QSpinBox(self)
        self.keep_spin.setRange(1, 100)
        self.keep_spin.setValue(dumps.keep_per_character)
        self.keep_spin.setToolTip("Snapshots kept per character, per dump type.")
        self.keep_spin.valueChanged.connect(self._on_keep_changed)
        bar.addWidget(self.keep_spin)

        bar.addStretch(1)

        self.import_button = QPushButton("Import now", self)
        self.import_button.setToolTip("Rescan the EQ directory immediately.")
        self.import_button.clicked.connect(self.import_now)
        bar.addWidget(self.import_button)

        self.import_file_button = QPushButton("Import file…", self)
        self.import_file_button.clicked.connect(self._prompt_import_file)
        bar.addWidget(self.import_file_button)

        self.upload_button = QPushButton("Upload inventory", self)
        self.upload_button.setToolTip(
            "Send inventory snapshots to the destination picked in "
            "Settings > Sharing. Works whether or not auto-import is on."
        )
        self.upload_button.clicked.connect(self.upload_selected)
        bar.addWidget(self.upload_button)

        self.export_button = QPushButton("Export…", self)
        self.export_button.clicked.connect(self._prompt_export)
        bar.addWidget(self.export_button)

        self.delete_button = QPushButton("Delete", self)
        self.delete_button.clicked.connect(self.delete_selected)
        bar.addWidget(self.delete_button)
        return bar

    # -- toggles ----------------------------------------------------------

    def _on_auto_import_toggled(self, checked: bool) -> None:
        self._settings.dumps.auto_import = checked
        self._save()

    def _on_auto_update_toggled(self, checked: bool) -> None:
        self._settings.dumps.auto_update = checked
        self._save()

    def _on_keep_changed(self, value: int) -> None:
        self._settings.dumps.keep_per_character = value
        self._save()

    def _save(self) -> None:
        if self._on_save is not None:
            self._on_save()

    # -- the tree ---------------------------------------------------------

    def refresh(self) -> None:
        """Rebuild the tree from the library, keeping the selection."""
        wanted = self._current
        self._loading = True
        try:
            self._tree.clear()
            for character in self.library.characters():
                character_item = QTreeWidgetItem([character, ""])
                character_item.setData(0, _ROLE_CHARACTER, character)
                self._tree.addTopLevelItem(character_item)
                for kind in DumpKind:
                    refs = self.library.snapshots(character, kind)
                    if not refs:
                        continue
                    kind_item = QTreeWidgetItem([kind.label, refs[0].label])
                    kind_item.setData(0, _ROLE_CHARACTER, character)
                    kind_item.setData(0, _ROLE_KIND, str(kind))
                    character_item.addChild(kind_item)
                    for index, ref in enumerate(refs):
                        # The newest snapshot IS the kind row's own snapshot,
                        # so selecting "Inventory" shows the current one and
                        # the children are the history behind it.
                        if index == 0:
                            kind_item.setData(0, _ROLE_REF, ref.model_dump(mode="json"))
                            continue
                        snapshot_item = QTreeWidgetItem(["", ref.label])
                        snapshot_item.setData(0, _ROLE_REF, ref.model_dump(mode="json"))
                        kind_item.addChild(snapshot_item)
                character_item.setExpanded(True)
        finally:
            self._loading = False
        self._restore_selection(wanted)
        self._render_status()

    def _restore_selection(self, wanted: SnapshotRef | None) -> None:
        if wanted is not None and self.select_snapshot(wanted):
            return
        # Nothing selected yet (or what was selected is gone): land on the
        # first character's newest snapshot so the window is never blank.
        first = self._tree.topLevelItem(0)
        if first is None:
            self._current = None
            self._dump = None
            self._render_detail()
            return
        child = first.child(0)
        self._tree.setCurrentItem(child if child is not None else first)

    def select_snapshot(self, ref: SnapshotRef) -> bool:
        """Select the row pointing at ``ref``; False if it is not in the tree."""
        item = self._find_item(ref)
        if item is None:
            return False
        self._tree.setCurrentItem(item)
        return True

    def _find_item(self, ref: SnapshotRef) -> QTreeWidgetItem | None:
        target = str(ref.path)
        stack = [self._tree.topLevelItem(i) for i in range(self._tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            if item is None:
                continue
            found = self._ref_of(item)
            if found is not None and str(found.path) == target:
                return item
            stack.extend(item.child(i) for i in range(item.childCount()))
        return None

    @staticmethod
    def _ref_of(item: QTreeWidgetItem | None) -> SnapshotRef | None:
        if item is None:
            return None
        data = item.data(0, _ROLE_REF)
        if not data:
            return None
        return SnapshotRef.model_validate(data)

    def _on_tree_selection(self) -> None:
        if self._loading:
            return
        self._current = self._ref_of(self._tree.currentItem())
        self._dump = self.library.load(self._current) if self._current is not None else None
        self._render_detail()

    def current_dump(self) -> CharacterDump | None:
        return self._dump

    def current_ref(self) -> SnapshotRef | None:
        return self._current

    # -- the detail pane --------------------------------------------------

    def _render_detail(self) -> None:
        dump, ref = self._dump, self._current
        if dump is None or ref is None:
            self._detail_title.setText("No snapshot selected")
            self._entries.clear()
            self._change_label.setText("")
            return
        self._detail_title.setText(
            f"{dump.character} — {dump.kind.label} · {ref.label} · {dump.entry_count} entries"
        )
        headers = _INVENTORY_HEADERS if dump.kind is DumpKind.INVENTORY else _SPELLBOOK_HEADERS
        # setHeaderLabels only ever GROWS the column count, so switching from
        # an inventory to a spellbook would leave the inventory's Count and ID
        # columns standing empty. Set the count explicitly first.
        self._entries.setColumnCount(len(headers))
        self._entries.setHeaderLabels(list(headers))
        self._render_entries()
        self._render_change(dump, ref)

    def _render_entries(self) -> None:
        dump = self._dump
        self._entries.clear()
        if dump is None:
            return
        needle = self._filter.text().strip().lower()
        if dump.kind is DumpKind.INVENTORY:
            rows = [
                (item.location_name, item.name, str(item.count), str(item.item_id))
                for item in dump.items
                if not needle or needle in item.name.lower()
            ]
        else:
            rows = [
                (str(spell.level), spell.name)
                for spell in dump.spells
                if not needle or needle in spell.name.lower()
            ]
        for row in rows:
            self._entries.addTopLevelItem(QTreeWidgetItem(list(row)))
        header = self._entries.header()
        if header is not None:
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

    def _render_change(self, dump: CharacterDump, ref: SnapshotRef) -> None:
        """What this snapshot changed relative to the one before it."""
        history = self.library.snapshots(dump.character, dump.kind)
        previous_ref = None
        for index, candidate in enumerate(history):
            if str(candidate.path) == str(ref.path):
                previous_ref = history[index + 1] if index + 1 < len(history) else None
                break
        if previous_ref is None:
            self._change_label.setText("Oldest snapshot held for this character.")
            return
        change = diff_dumps(self.library.load(previous_ref), dump)
        if change.empty:
            self._change_label.setText(f"No change since {previous_ref.label}.")
            return
        parts = []
        if change.added:
            parts.append(f"+{len(change.added)}: {_join(change.added)}")
        if change.removed:
            parts.append(f"-{len(change.removed)}: {_join(change.removed)}")
        self._change_label.setText(f"Since {previous_ref.label} — " + "; ".join(parts))

    def _render_status(self) -> None:
        if self.watcher is not None:
            scan = self.watcher.status_text()
        else:
            total = self.library.total_snapshots()
            scan = f"{total} snapshot{'s' if total != 1 else ''} stored."
        # The upload line is the newer news when there is any, so it wins the
        # tail of the line rather than being buried.
        upload = self.uploader.status_text() if self.uploader is not None else ""
        self._status.setText(f"{scan}  {upload}".rstrip() if upload else scan)

    def _on_timer(self) -> None:
        if self.isVisible():
            self.refresh()

    # -- actions ----------------------------------------------------------

    def import_now(self) -> None:
        """Rescan the EQ directory. Always allowed, toggles or not."""
        if self.watcher is not None:
            self.watcher.request_scan()
            self._status.setText("Scanning the EverQuest directory…")
            # The driver tick does the work; the refresh timer shows it.
            QTimer.singleShot(REFRESH_MS, self.refresh)
            return
        self._render_status()

    def import_file(self, path: Path) -> bool:
        """Import one hand-picked file. False if it is not a dump we read."""
        dump = read_dump_file(Path(path))
        if dump is None:
            return False
        if self.watcher is not None:
            self.watcher.request_import(Path(path))
            QTimer.singleShot(REFRESH_MS, self.refresh)
            return True
        # No watcher (tests): store directly. Nothing is published, which is
        # the honest outcome — there is no driver thread to publish from.
        self.library.store(dump, keep=self._settings.dumps.keep_per_character)
        self.refresh()
        return True

    def _prompt_import_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import a dump file", "", "Dump files (*.txt);;All files (*)"
        )
        if not path:
            return
        if not self.import_file(Path(path)):
            QMessageBox.warning(
                self,
                "Not a dump",
                "That file is not an /outputfile inventory or spellbook dump.\n\n"
                "In game: /outputfile inventory — or /outputfile spellbook.",
            )

    # -- upload ------------------------------------------------------------

    def upload_scope(self) -> list[CharacterDump]:
        """Which inventories the Upload button would send, from the selection.

        Three levels, narrowest first, because that is how the tree reads:
        an inventory snapshot uploads itself, a character row uploads that
        character's current inventory, and no selection at all uploads every
        character's current inventory — p99planner takes a whole mule roster
        in one call, and that is exactly the tedious case worth having.
        """
        dump = self._dump
        if dump is not None and dump.kind is DumpKind.INVENTORY:
            return [dump]
        item = self._tree.currentItem()
        character = self._character_of(item)
        characters = [character] if character else self.library.characters()
        found = [self.library.load_latest(name, DumpKind.INVENTORY) for name in characters]
        return [dump for dump in found if dump is not None]

    def _character_of(self, item: QTreeWidgetItem | None) -> str:
        """Walk up to whichever row carries a character name."""
        while item is not None:
            name = item.data(0, _ROLE_CHARACTER)
            if name:
                return str(name)
            item = item.parent()
        return ""

    def upload_selected(self) -> str:
        """Upload per :meth:`upload_scope`; returns the status line shown."""
        if self.uploader is None:
            message = "Inventory upload is unavailable."
        else:
            dumps = self.upload_scope()
            if not dumps:
                message = "No inventory snapshot to upload."
            else:
                message = self.uploader.upload_now(dumps)
        self._status.setText(message)
        # The send finishes on the net worker; let its outcome land in the
        # status line without making the user click anything.
        QTimer.singleShot(REFRESH_MS, self._render_status)
        return message

    def delete_selected(self) -> None:
        """Delete the selected snapshot, or a whole character at the root."""
        item = self._tree.currentItem()
        ref = self._ref_of(item)
        if ref is not None:
            if not self._confirm(f"Delete the {ref.kind.label} snapshot from {ref.label}?"):
                return
            self.library.delete(ref)
            self._current = None
            self.refresh()
            return
        character = item.data(0, _ROLE_CHARACTER) if item is not None else None
        if not character:
            return
        if not self._confirm(f"Forget every stored dump for {character}?"):
            return
        self.library.delete_character(str(character))
        self._current = None
        self.refresh()

    def export_selected(self, path: Path) -> bool:
        """Write the selected snapshot back out in the client's own format."""
        dump = self._dump
        if dump is None:
            return False
        try:
            Path(path).write_text(render_dump_text(dump), encoding="utf-8")
        except OSError:
            return False
        return True

    def _prompt_export(self) -> None:
        dump = self._dump
        if dump is None:
            return
        suggested = f"{dump.character}-{dump.kind.label}.txt"
        path, _ = QFileDialog.getSaveFileName(self, "Export snapshot", suggested, "Text (*.txt)")
        if not path:
            return
        if not self.export_selected(Path(path)):
            QMessageBox.warning(self, "Export failed", "Could not write that file.")

    def _confirm(self, question: str) -> bool:
        if not self.confirm_destructive:
            return True
        answer = QMessageBox.question(
            self,
            "Character Dumps",
            question,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes


def _join(names: list[str], limit: int = 6) -> str:
    """Names for a one-line summary, truncated rather than wrapped forever."""
    if len(names) <= limit:
        return ", ".join(names)
    return ", ".join(names[:limit]) + f" and {len(names) - limit} more"
