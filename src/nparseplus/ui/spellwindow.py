"""Spell timer overlay — the new-core replacement for the legacy spells window.

A small self-contained frameless overlay (same Qt flag recipe as
``helpers.parser.ParserWindow``, but reading/writing the NEW
``Settings.windows['spells']`` model instead of the legacy config dict).
It polls ``backend.timers.snapshot()`` on a 250 ms QTimer and renders the
rows grouped by target, YOU_GROUP first.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from nparseplus.config.settings import Settings, WindowState
from nparseplus.core.timers import YOU_GROUP, CounterRow, RollRow, Row, SpellRow

WINDOW_KEY = "spells"
REFRESH_INTERVAL_MS = 250
DEFAULT_GEOMETRY = (400, 0, 220, 400)

# Progress-bar chunk colors per row kind.
COLOR_BENEFICIAL = "#2f9e6e"  # green
COLOR_DETRIMENTAL = "#c0392b"  # red-ish
COLOR_COOLDOWN = "#3a7bd5"  # blue
COLOR_TIMER = "#8e5bd1"  # purple
COLOR_ROLL = "#d99b2b"  # amber

BAR_MAX = 1000


class TimersLike(Protocol):
    def snapshot(self) -> list[Row]: ...


class BackendLike(Protocol):
    """The slice of ``composition.Backend`` this window needs (test-fakeable)."""

    timers: TimersLike
    settings: Settings


def bar_color(row: Row) -> str:
    if isinstance(row, SpellRow):
        if row.is_cooldown:
            return COLOR_COOLDOWN
        return COLOR_DETRIMENTAL if row.detrimental else COLOR_BENEFICIAL
    if isinstance(row, RollRow):
        return COLOR_ROLL
    return COLOR_TIMER


def format_remaining(seconds: float) -> str:
    """mm:ss (or h:mm:ss past the hour), clamped at zero."""
    total = max(0, int(seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class _RowWidget(QFrame):
    """One timer row: name + remaining time above a thin progress bar."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SpellTimerRow")
        self.row_name = ""
        self._color = ""

        self._name = QLabel(self)
        self._name.setObjectName("SpellTimerRowName")
        self._value = QLabel(self)
        self._value.setObjectName("SpellTimerRowValue")
        self._value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        text_row = QHBoxLayout()
        text_row.setContentsMargins(0, 0, 0, 0)
        text_row.setSpacing(4)
        text_row.addWidget(self._name, 1)
        text_row.addWidget(self._value, 0)

        self._bar = QProgressBar(self)
        self._bar.setObjectName("SpellTimerRowBar")
        self._bar.setRange(0, BAR_MAX)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(5)

        layout = QVBoxLayout()
        layout.setContentsMargins(4, 1, 4, 1)
        layout.setSpacing(1)
        layout.addLayout(text_row)
        layout.addWidget(self._bar)
        self.setLayout(layout)

    def update_row(self, row: Row, now: datetime) -> None:
        """Render ``row`` — read-only; never mutates the model."""
        self.row_name = row.name
        self._name.setText(row.name)
        if isinstance(row, CounterRow):
            self._value.setText(f"x{row.count}")
            self._bar.setVisible(False)
            return
        remaining = max(0.0, (row.ends_at - now).total_seconds())
        if isinstance(row, RollRow):
            self._value.setText(f"{row.roll}/{row.max_roll}  {format_remaining(remaining)}")
        else:
            self._value.setText(format_remaining(remaining))
        total = max(row.total_duration_s, 0.001)
        self._bar.setValue(int(min(remaining / total, 1.0) * BAR_MAX))
        self._bar.setVisible(True)
        color = bar_color(row)
        if color != self._color:
            self._color = color
            self._bar.setStyleSheet(
                "QProgressBar { background-color: rgba(255, 255, 255, 35); border: none; }"
                f"QProgressBar::chunk {{ background-color: {color}; }}"
            )


class SpellTimerWindow(QWidget):
    """Frameless always-on-top overlay listing the backend's timer rows."""

    def __init__(
        self,
        backend: BackendLike,
        on_save: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._backend = backend
        self._on_save = on_save
        self._drag_offset: QPoint | None = None
        self._quitting = False
        self._headers: dict[str, QLabel] = {}
        self._row_widgets: dict[tuple[str, str, str, int], _RowWidget] = {}

        state = backend.settings.windows.get(WINDOW_KEY)
        if state is None:
            state = WindowState(shown=True)  # first run: show the window
            backend.settings.windows[WINDOW_KEY] = state
        self._state = state

        self.setObjectName("SpellTimerWindow")
        self.setWindowTitle("Spell Timers")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._apply_flags()
        self.setGeometry(*(state.geometry or DEFAULT_GEOMETRY))
        self.setWindowOpacity(state.opacity)

        font_size = max(8, backend.settings.general.font_size)
        self.setStyleSheet(
            "#SpellTimerContainer {"
            " background-color: rgba(0, 0, 0, 180); border-radius: 4px; }"
            f"QLabel {{ color: #dddddd; font-size: {font_size - 2}px; }}"
            "#SpellTimerGroup { color: #ffffff; font-weight: bold;"
            f" font-size: {font_size}px; background-color: rgba(0, 68, 0, 160);"
            " padding: 1px 4px; }"
        )

        self._title = QLabel("Spell Timers", self)
        self._title.setObjectName("SpellTimerTitle")
        self._title.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        self._rows_layout = QVBoxLayout()
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(1)

        container_layout = QVBoxLayout()
        container_layout.setContentsMargins(2, 2, 2, 2)
        container_layout.setSpacing(1)
        container_layout.addWidget(self._title, 0)
        container_layout.addLayout(self._rows_layout, 0)
        container_layout.addStretch(1)

        self._container = QFrame(self)
        self._container.setObjectName("SpellTimerContainer")
        self._container.setLayout(container_layout)

        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._container)
        self.setLayout(outer)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh)
        self._refresh_timer.start(REFRESH_INTERVAL_MS)

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._on_app_quit)

        if state.shown:
            self.show()

    # -- rendering -------------------------------------------------------------

    def refresh(self) -> None:
        """Re-render from ``timers.snapshot()`` (rows are never mutated).

        Rebuilds the layout order each tick but reuses the per-row widgets
        keyed by (kind, name, group, dup-index) — cheap at overlay scale.
        """
        now = datetime.now()
        rows = self._backend.timers.snapshot()
        if self._backend.settings.spellwindow.you_only_spells:
            rows = [row for row in rows if row.group == YOU_GROUP]

        grouped: dict[str, list[Row]] = {}
        for row in rows:
            grouped.setdefault(row.group, []).append(row)
        # YOU_GROUP first, then the other targets alphabetically.
        order = sorted(grouped, key=lambda g: (g != YOU_GROUP, g.casefold()))

        while self._rows_layout.count():
            self._rows_layout.takeAt(0)

        used_headers: set[str] = set()
        used_rows: set[tuple[str, str, str, int]] = set()
        dup_counter: dict[tuple[str, str, str], int] = {}
        for group in order:
            header = self._headers.get(group)
            if header is None:
                header = QLabel(group.strip() or group, self._container)
                header.setObjectName("SpellTimerGroup")
                header.setProperty("group_key", group)
                self._headers[group] = header
            self._rows_layout.addWidget(header)
            header.show()
            used_headers.add(group)
            for row in sorted(grouped[group], key=lambda r: r.name.casefold()):
                base = (type(row).__name__, row.name.casefold(), row.group.casefold())
                index = dup_counter.get(base, 0)
                dup_counter[base] = index + 1
                key = (*base, index)
                widget = self._row_widgets.get(key)
                if widget is None:
                    widget = _RowWidget(self._container)
                    self._row_widgets[key] = widget
                widget.update_row(row, now)
                self._rows_layout.addWidget(widget)
                widget.show()
                used_rows.add(key)

        for group in [g for g in self._headers if g not in used_headers]:
            self._headers.pop(group).deleteLater()
        for key in [k for k in self._row_widgets if k not in used_rows]:
            self._row_widgets.pop(key).deleteLater()

    def current_groups(self) -> list[str]:
        """Group keys in on-screen order (test/debug hook)."""
        out: list[str] = []
        for i in range(self._rows_layout.count()):
            widget = self._rows_layout.itemAt(i).widget()
            if isinstance(widget, QLabel):
                out.append(widget.property("group_key"))
        return out

    def current_row_names(self) -> list[str]:
        """Row names in on-screen order (test/debug hook)."""
        out: list[str] = []
        for i in range(self._rows_layout.count()):
            widget = self._rows_layout.itemAt(i).widget()
            if isinstance(widget, _RowWidget):
                out.append(widget.row_name)
        return out

    # -- window state ------------------------------------------------------------

    def _apply_flags(self) -> None:
        state = self._state
        if state.frameless:
            flags = Qt.WindowType.FramelessWindowHint
        else:
            flags = Qt.WindowType.WindowCloseButtonHint | Qt.WindowType.WindowMinMaxButtonsHint
        if state.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        if state.clickthrough:
            flags |= Qt.WindowType.WindowTransparentForInput
        self.setWindowFlags(flags)

    def toggle(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.show()
        self.persist_state()

    def persist_state(self, shown: bool | None = None) -> None:
        """Write geometry/opacity/shown into settings.windows['spells'] and save."""
        geo = self.geometry()
        self._state.geometry = (geo.x(), geo.y(), geo.width(), geo.height())
        self._state.opacity = min(1.0, max(0.0, round(self.windowOpacity(), 3)))
        self._state.shown = self.isVisible() if shown is None else shown
        if self._on_save is not None:
            self._on_save()

    def _on_app_quit(self) -> None:
        self._quitting = True
        self.persist_state(shown=self.isVisible())

    def closeEvent(self, event) -> None:
        if not self._quitting:
            self.persist_state(shown=False)
        super().closeEvent(event)

    # -- drag-to-move --------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None:
            self._drag_offset = None
            self.persist_state()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:
        event.accept()  # deliberately inert: no scroll-through to the game
