"""Trigger activity log — "which trigger just fired, and why" (#31).

GINA has a trigger console; nParse+ had nothing, so with a large imported
trigger pack there was no way to tell which trigger went off or which folder
it came from. This view is the Activity tab of the Trigger Editor: a bounded,
newest-first table of :class:`TriggerFiredEvent`s with a filter, a pause, and
a double-click that jumps to the offending trigger in the editor's tree.

The single authoritative store is ``_rows`` (a bounded deque); the table is a
projection of it under the current filter. That's deliberately unlike
``ConsoleWindow``'s append-plus-hidden-backlog split — a filter needs a
re-render anyway, and one store removes a whole class of desync bugs.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from nparseplus.core.events import TriggerFiredEvent
from nparseplus.ui import chromewidgets

#: How many fires to keep. Bounded like the console's scrollback; a fire is
#: far rarer than a log line, so this covers a long raid.
MAX_ROWS = 500

COLUMNS = ("Time", "Trigger", "Group", "Action", "Matched line")

#: Prefix that tells a timer follow-up apart from the original match.
PHASE_PREFIXES = {
    "timer_ending": "Timer ending",
    "timer_ended": "Timer ended",
    "timer_cancelled": "Timer ended early",
}

_ROLE_TRIGGER_ID = Qt.ItemDataRole.UserRole


def format_action(event: TriggerFiredEvent) -> str:
    """One-line summary of what a fire actually did, token-expanded.

    Mirrors the wording of the editor's Test box ("Display:" / "TTS:") so the
    log reads the same as the tool people already use to check a trigger.
    """
    parts: list[str] = []
    if event.display_text:
        parts.append(f"Display: {event.display_text}")
    if event.tts_text:
        parts.append(f"TTS: {event.tts_text}")
    if event.sound_file:
        parts.append(f"Sound: {event.sound_file}")
    if event.timer_name:
        timer = f"Timer: {event.timer_name}"
        if event.timer_seconds > 0:
            timer += f" {_duration_label(event.timer_seconds)}"
        parts.append(timer)
    body = "  •  ".join(parts)
    prefix = PHASE_PREFIXES.get(event.phase)
    if prefix is None:
        # A match that emitted nothing is still worth a row — that silent
        # trigger is usually the one being hunted.
        return body or "(no output)"
    return f"{prefix} — {body}" if body else prefix


def _duration_label(seconds: int) -> str:
    if seconds >= 3600:
        return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"
    if seconds >= 60:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds}s"


@dataclass(frozen=True)
class ActivityRow:
    """One logged fire, already rendered to display strings."""

    time_text: str
    trigger_id: str
    trigger_name: str
    group: str
    action: str
    line: str
    phase: str = "match"

    @classmethod
    def from_event(cls, event: TriggerFiredEvent) -> ActivityRow:
        return cls(
            time_text=event.timestamp.strftime("%H:%M:%S"),
            trigger_id=event.trigger_id,
            trigger_name=event.trigger_name or "(unnamed)",
            group=event.group,
            action=format_action(event),
            line=event.line,
            phase=event.phase,
        )

    def cells(self) -> tuple[str, ...]:
        return (self.time_text, self.trigger_name, self.group, self.action, self.line)

    def matches(self, needle: str) -> bool:
        if not needle:
            return True
        needle = needle.lower()
        return any(needle in cell.lower() for cell in self.cells())


class TriggerActivityView(QWidget):
    """Newest-first log of trigger fires.

    Emits :attr:`jump_requested` with a ``trigger_id`` when the user asks to
    see the trigger behind a row; the Trigger Editor wires that to
    ``show_trigger``. Holds no ``Settings`` and never saves — it is pure
    session state, so it can be promoted to its own window later untouched.
    """

    jump_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: deque[ActivityRow] = deque(maxlen=MAX_ROWS)
        #: Rows arrived while this page was not visible; the table is rebuilt
        #: on showEvent rather than churning a widget nobody is looking at.
        self._stale = False

        self.filter_edit = QLineEdit(self)
        self.filter_edit.setPlaceholderText("Filter by trigger, group, or line…")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._on_filter_changed)

        self.pause_check = QCheckBox("Pause", self)
        self.clear_button = QPushButton("Clear", self)
        self.clear_button.clicked.connect(self.clear)
        self.count_label = QLabel("", self)

        header = QHBoxLayout()
        header.addWidget(self.filter_edit, 1)
        header.addWidget(self.pause_check)
        header.addWidget(self.clear_button)
        header.addWidget(self.count_label)

        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 70)
        self.table.setColumnWidth(1, 160)
        self.table.setColumnWidth(2, 150)
        self.table.setColumnWidth(3, 220)
        self.table.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)

        # Was setEnabled(False), which yields Qt's disabled-text role rather
        # than the app's hint colour — and silently blocked text selection.
        hint = chromewidgets.hint("Double-click a row to open that trigger in the editor.", self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addLayout(header)
        layout.addWidget(self.table, 1)
        layout.addWidget(hint)

        self._update_count()

    # -- event intake ----------------------------------------------------------

    def handle_events(self, events: list) -> None:
        """Bulk bridge slot (``events_batch``)."""
        if self.pause_check.isChecked():
            return
        rows = [
            ActivityRow.from_event(event)
            for event in events
            if isinstance(event, TriggerFiredEvent)
        ]
        if not rows:
            return
        visible = self.isVisible()
        needle = self.filter_edit.text()
        for row in rows:
            self._rows.appendleft(row)
            if visible and row.matches(needle):
                self._insert_row(0, row)
        if visible:
            self._trim_table()
        else:
            self._stale = True
        self._update_count()

    def handle_event(self, event: object) -> None:
        """Single-event slot (kept for tests/direct callers)."""
        self.handle_events([event])

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._stale:
            self._repopulate()

    # -- table rendering -------------------------------------------------------

    def _insert_row(self, index: int, row: ActivityRow) -> None:
        # Timer follow-ups are consequences of an earlier match; mute the text
        # so the matches themselves stay easy to scan. Colour, not the disabled
        # flag — the row must stay selectable and double-clickable.
        muted = (
            None
            if row.phase == "match"
            else self.palette().brush(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
        )
        self.table.insertRow(index)
        for column, text in enumerate(row.cells()):
            item = QTableWidgetItem(text)
            item.setData(_ROLE_TRIGGER_ID, row.trigger_id)
            item.setToolTip(text)
            if muted is not None:
                item.setForeground(muted)
            self.table.setItem(index, column, item)

    def _trim_table(self) -> None:
        while self.table.rowCount() > MAX_ROWS:
            self.table.removeRow(self.table.rowCount() - 1)

    def _repopulate(self) -> None:
        needle = self.filter_edit.text()
        self.table.setRowCount(0)
        for row in self._rows:  # already newest-first
            if row.matches(needle):
                self._insert_row(self.table.rowCount(), row)
        self._stale = False
        self._update_count()

    def _update_count(self) -> None:
        total = len(self._rows)
        shown = self.table.rowCount()
        if self.filter_edit.text() and shown != total:
            self.count_label.setText(f"{shown} of {total}")
        else:
            self.count_label.setText(f"{total} events")

    def _on_filter_changed(self, _text: str) -> None:
        self._repopulate()

    # -- interaction -----------------------------------------------------------

    def _trigger_id_at(self, row_index: int) -> str:
        item = self.table.item(row_index, 0)
        return "" if item is None else str(item.data(_ROLE_TRIGGER_ID) or "")

    def _on_item_double_clicked(self, item: QTableWidgetItem) -> None:
        trigger_id = self._trigger_id_at(item.row())
        if trigger_id:
            self.jump_requested.emit(trigger_id)

    def _on_context_menu(self, pos) -> None:
        item = self.table.itemAt(pos)
        if item is None:
            return
        row_index = item.row()
        menu = QMenu(self)
        show_action = menu.addAction("Show trigger in editor")
        copy_action = menu.addAction("Copy matched line")
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen is show_action:
            trigger_id = self._trigger_id_at(row_index)
            if trigger_id:
                self.jump_requested.emit(trigger_id)
        elif chosen is copy_action:
            from PySide6.QtWidgets import QApplication

            line_item = self.table.item(row_index, len(COLUMNS) - 1)
            QApplication.clipboard().setText("" if line_item is None else line_item.text())

    def clear(self) -> None:
        self._rows.clear()
        self.table.setRowCount(0)
        self._stale = False
        self._update_count()

    # -- test hooks ------------------------------------------------------------

    def set_paused(self, paused: bool) -> None:
        self.pause_check.setChecked(paused)

    def set_filter(self, text: str) -> None:
        self.filter_edit.setText(text)

    def record_count(self) -> int:
        """Fires held in the store (independent of filter/visibility)."""
        return len(self._rows)

    def row_count(self) -> int:
        """Rows currently rendered in the table."""
        return self.table.rowCount()

    def row_values(self, index: int) -> tuple[str, ...]:
        return tuple(
            (self.table.item(index, column).text() if self.table.item(index, column) else "")
            for column in range(len(COLUMNS))
        )
