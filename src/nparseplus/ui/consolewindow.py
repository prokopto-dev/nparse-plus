"""Console window — scrollback of raw log lines (EQTool UI/Console.xaml).

A normal (non-clickthrough) tool window: read-only scrollback of LineEvents
with timestamps, a pause checkbox, capped at MAX_LINES.

Right-clicking a row also offers "Create trigger from this line…" (#82) —
the shortest path from seeing something happen to having a trigger for it.
The window is only the renderer: ``core.triggers.suggest`` decides what the
pattern should be, and the Trigger Editor owns creating the trigger.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from nparseplus.config.settings import Settings, WindowState
from nparseplus.core.events import LineEvent
from nparseplus.core.triggers.suggest import TriggerSuggestion, suggest_trigger_text
from nparseplus.ui import chromewidgets
from nparseplus.ui.overlaybase import OverlayWindowBase

WINDOW_KEY = "console"
DEFAULT_GEOMETRY = (200, 200, 560, 320)
MAX_LINES = 2000

CREATE_TRIGGER_LABEL = "Create trigger from this line…"
CREATE_TRIGGER_EXACT_LABEL = "Create trigger from exact text…"

#: Console text is a log, so it wants a fixed pitch. Qt resolves the first
#: family that exists on the host, which is why this is a list and not one
#: name — the old hardcoded "Menlo" is macOS-only.
MONOSPACE_FAMILIES = ["Menlo", "Consolas", "DejaVu Sans Mono", "monospace"]


class ConsoleWindow(chromewidgets.ChromeMixin, OverlayWindowBase):
    #: (console row, use the tokenised form) — app.py wires this to the
    #: Trigger Editor, the only place that can create the trigger.
    create_trigger_requested = Signal(str, bool)

    def __init__(
        self,
        settings: Settings,
        on_save: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            settings=settings,
            window_key=WINDOW_KEY,
            title="Console",
            default_geometry=DEFAULT_GEOMETRY,
            on_save=on_save,
            default_state=WindowState(frameless=False, always_on_top=False),
            translucent=False,
            parent=parent,
        )
        self.setObjectName("ConsoleWindow")

        # Lines received while hidden are buffered (bounded) instead of
        # churning the hidden QPlainTextEdit document per log line; the
        # backlog is flushed into the widget on show.
        self._hidden_backlog: deque[str] = deque(maxlen=MAX_LINES)

        #: Resolves the character whose name becomes ``{c}``. app.py points
        #: this at the Trigger Editor's own answer, so the prefilled trigger
        #: and the editor's test box can never disagree about the name.
        self.player_name: Callable[[], str] = lambda: ""

        self._pause = QCheckBox("Pause", self)
        header = QHBoxLayout()
        header.addWidget(QLabel("Log console", self))
        header.addStretch(1)
        header.addWidget(self._pause)

        self._text = QPlainTextEdit(self)
        self._text.setReadOnly(True)
        self._text.setMaximumBlockCount(MAX_LINES)
        # A family list, not "Menlo": that exists only on macOS, so Windows
        # and Linux silently fell back to the default proportional face.
        self._text.setFont(QFont(MONOSPACE_FAMILIES))
        # Custom policy rather than a QPlainTextEdit subclass: the standard
        # menu (Copy / Select All) is rebuilt below and extended, so nothing
        # the user already had is lost.
        self._text.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._text.customContextMenuRequested.connect(self._on_text_context_menu)

        layout = QVBoxLayout()
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addLayout(header)
        layout.addWidget(self._text)
        self.setLayout(layout)

        self.apply_chrome()
        self.restore_visibility()

    def handle_event(self, event: object) -> None:
        """Single-event slot (kept for tests/direct callers)."""
        self.handle_events([event])

    def handle_events(self, events: list) -> None:
        """Bulk bridge slot (``events_batch``): one document append per
        coalesced flush instead of one per log line."""
        if self._pause.isChecked():
            return
        lines = [
            f"[{event.timestamp.strftime('%H:%M:%S')}] {event.line}"
            for event in events
            if isinstance(event, LineEvent)
        ]
        if not lines:
            return
        if self.isVisible():
            self._text.appendPlainText("\n".join(lines))
        else:
            self._hidden_backlog.extend(lines)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._hidden_backlog:
            # One append; embedded newlines still split into one block per
            # line, and the block cap keeps the scrollback bounded.
            self._text.appendPlainText("\n".join(self._hidden_backlog))
            self._hidden_backlog.clear()

    # -- create trigger from a line (#82) --------------------------------------

    def line_at(self, pos: QPoint) -> str:
        """The console row under a widget-coordinate point."""
        # customContextMenuRequested hands out coordinates in the QPlainTextEdit's
        # own space; cursorForPosition wants the viewport's, which differ by the
        # frame width. Map rather than assume they are the same.
        viewport_pos = self._text.viewport().mapFrom(self._text, pos)
        return self._text.cursorForPosition(viewport_pos).block().text()

    def suggestion_at(self, pos: QPoint) -> TriggerSuggestion:
        return suggest_trigger_text(self.line_at(pos), self.player_name())

    def build_context_menu(self, pos: QPoint) -> QMenu:
        """The row's context menu: the standard one plus the trigger actions."""
        menu = self._text.createStandardContextMenu()
        suggestion = self.suggestion_at(pos)
        if not suggestion.message:
            return menu
        line = self.line_at(pos)
        menu.setToolTipsVisible(True)
        menu.addSeparator()
        tokenized = menu.addAction(CREATE_TRIGGER_LABEL)
        tokenized.setToolTip(suggestion.pattern)
        tokenized.triggered.connect(
            lambda _checked=False, row=line: self.create_trigger_requested.emit(row, True)
        )
        # Only worth a second item when the two forms actually differ; with no
        # token applied the "exact text" offer would be the same trigger.
        if suggestion.has_tokens:
            exact = menu.addAction(CREATE_TRIGGER_EXACT_LABEL)
            exact.setToolTip(suggestion.literal)
            exact.triggered.connect(
                lambda _checked=False, row=line: self.create_trigger_requested.emit(row, False)
            )
        return menu

    def _on_text_context_menu(self, pos: QPoint) -> None:
        menu = self.build_context_menu(pos)
        menu.exec(self._text.mapToGlobal(pos))

    # -- test hooks ------------------------------------------------------------

    def line_count(self) -> int:
        return self._text.document().blockCount()

    def set_paused(self, paused: bool) -> None:
        self._pause.setChecked(paused)

    # dragging the window body would fight with text selection; only the
    # base-class drag on the margins applies. Keep default mouse handling.
    def mousePressEvent(self, event) -> None:
        QWidget.mousePressEvent(self, event)

    def mouseMoveEvent(self, event) -> None:
        QWidget.mouseMoveEvent(self, event)

    def mouseReleaseEvent(self, event) -> None:
        QWidget.mouseReleaseEvent(self, event)
