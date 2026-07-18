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
    QMenu,
    QProgressBar,
    QScrollArea,
    QSizeGrip,
    QVBoxLayout,
    QWidget,
)

from nparseplus.config.settings import Settings, WindowState, find_player
from nparseplus.core.handlers.boat import BOATS_GROUP
from nparseplus.core.player import ActivePlayer
from nparseplus.core.spells.matching import hide_spell
from nparseplus.core.timers import (
    MOB_TIMER_GROUP,
    ROLL_TIMER_GROUP,
    TRIGGER_TIMER_GROUP,
    YOU_GROUP,
    CounterRow,
    RollRow,
    Row,
    SpellRow,
)
from nparseplus.ui import theme
from nparseplus.ui.spellicons import ICON_SIZE, spell_icon_pixmap

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
    player: ActivePlayer


def row_sort_key(row: Row, now: datetime, mode: str) -> tuple:
    """Sort key for rows under one group header.

    ``"alphabetical"`` orders by name; ``"time_remaining"`` (default) orders
    soonest-to-expire first. Counters have no ``ends_at`` so they sort last
    under the time mode, name-tiebroken.
    """
    name_key = row.name.casefold()
    if mode == "alphabetical":
        return (name_key,)
    ends_at = getattr(row, "ends_at", None)
    if ends_at is None:
        return (float("inf"), name_key)
    return ((ends_at - now).total_seconds(), name_key)


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

    def __init__(
        self,
        parent: QWidget | None = None,
        warning_threshold: Callable[[], int] = lambda: 0,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SpellTimerRow")
        self.row_name = ""
        #: The last-rendered Row — snapshot() copies the list, not the rows,
        #: so this identity works for TimersService.remove_row (context menu).
        self.row: Row | None = None
        self._color = ""
        self._warning_threshold = warning_threshold
        self._warning = False

        self._icon = QLabel(self)
        self._icon.setObjectName("SpellTimerRowIcon")
        self._icon.setFixedSize(ICON_SIZE, ICON_SIZE)
        self._icon.setVisible(False)
        self._icon_index: int | None = None
        self._name = QLabel(self)
        self._name.setObjectName("SpellTimerRowName")
        self._value = QLabel(self)
        self._value.setObjectName("SpellTimerRowValue")
        self._value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        text_row = QHBoxLayout()
        text_row.setContentsMargins(0, 0, 0, 0)
        text_row.setSpacing(4)
        text_row.addWidget(self._icon, 0)
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
        self.row = row
        self._name.setText(row.name)
        self._update_icon(row)
        if isinstance(row, CounterRow):
            self._value.setText(f"x{row.count}")
            self._bar.setVisible(False)
            return
        remaining = max(0.0, (row.ends_at - now).total_seconds())
        if isinstance(row, RollRow):
            self._value.setText(f"{row.roll}/{row.max_roll}  {format_remaining(remaining)}")
        else:
            self._value.setText(format_remaining(remaining))
        self._update_warning(row, remaining)
        total = max(row.total_duration_s, 0.001)
        self._bar.setValue(int(min(remaining / total, 1.0) * BAR_MAX))
        self._bar.setVisible(True)
        color = bar_color(row)
        if color != self._color:
            self._color = color
            self._bar.setStyleSheet(
                f"QProgressBar {{ background-color: {theme.palette().bar_track}; border: none; }}"
                f"QProgressBar::chunk {{ background-color: {color}; }}"
            )

    def _update_warning(self, row: Row, remaining: float) -> None:
        """Buff-fade pre-warning: the time label turns red inside the window
        (visual side of core/handlers/buff_warning.py)."""
        threshold = self._warning_threshold()
        warning = (
            threshold > 0
            and isinstance(row, SpellRow)
            and row.group == YOU_GROUP
            and not row.is_cooldown
            and not row.detrimental
            and 0 < remaining <= threshold
        )
        if warning != self._warning:
            self._warning = warning
            self._value.setStyleSheet(
                f"color: {theme.palette().warning_text}; font-weight: bold;" if warning else ""
            )

    def _update_icon(self, row: Row) -> None:
        """Gem icon for spell rows (bundled sprite sheets); hidden otherwise."""
        icon_index = row.spell.spell_icon if isinstance(row, SpellRow) else None
        if icon_index == self._icon_index:
            return
        self._icon_index = icon_index
        pixmap = spell_icon_pixmap(icon_index) if icon_index else None
        if pixmap is None:
            self._icon.clear()
            self._icon.setVisible(False)
        else:
            self._icon.setPixmap(pixmap)
            self._icon.setVisible(True)


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
        colors = theme.palette()
        self.setStyleSheet(
            "#SpellTimerContainer {"
            f" background-color: {colors.panel_bg}; border-radius: 4px; }}"
            f"QLabel {{ color: {colors.text}; font-size: {font_size - 2}px; }}"
            f"#SpellTimerGroup {{ color: {colors.heading}; font-weight: bold;"
            f" font-size: {font_size}px; background-color: rgba(0, 68, 0, 160);"
            " padding: 1px 4px; }"
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
            "QScrollBar:vertical { background: transparent; width: 6px; margin: 0; }"
            "QScrollBar::handle:vertical {"
            " background: rgba(136, 136, 136, 120); border-radius: 3px; min-height: 20px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {"
            " background: transparent; }"
            "QSizeGrip { background: transparent; width: 12px; height: 12px; }"
        )

        self._title = QLabel("Spell Timers", self)
        self._title.setObjectName("SpellTimerTitle")
        self._title.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        self._rows_layout = QVBoxLayout()
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(1)

        # The rows live inside a scroll area so the WINDOW size is the user's
        # choice: it no longer inflates as rows arrive (and then sticks huge
        # after they leave) — overflow scrolls instead.
        rows_host = QWidget(self)
        host_layout = QVBoxLayout(rows_host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(0)
        host_layout.addLayout(self._rows_layout, 0)
        host_layout.addStretch(1)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setWidget(rows_host)

        container_layout = QVBoxLayout()
        container_layout.setContentsMargins(2, 2, 2, 2)
        container_layout.setSpacing(1)
        container_layout.addWidget(self._title, 0)
        container_layout.addWidget(self._scroll, 1)

        self._container = QFrame(self)
        self._container.setObjectName("SpellTimerContainer")
        self._container.setLayout(container_layout)

        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._container)
        self.setLayout(outer)
        self.setMinimumSize(140, 120)

        # Frameless windows have no OS resize border; the corner grip is the
        # resize affordance. Size changes persist (debounced) below.
        self._grip = QSizeGrip(self)
        self._grip.raise_()
        self._persist_resize = QTimer(self)
        self._persist_resize.setSingleShot(True)
        self._persist_resize.setInterval(400)
        self._persist_resize.timeout.connect(self.persist_state)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh)
        self._refresh_timer.start(REFRESH_INTERVAL_MS)

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._on_app_quit)

        if state.shown:
            self.show()

    # -- rendering -------------------------------------------------------------

    def _row_hidden(self, row: Row) -> bool:
        """Visibility pass — SpellWindowViewModel.cs order: the YOU group is
        always visible, NPC targets are never hidden, then you_only_spells,
        then the active character's class filter (HideSpell)."""
        if row.group == YOU_GROUP:
            return False
        sw = self._backend.settings.spellwindow
        if row.group == BOATS_GROUP and not sw.show_boats:
            return True
        if row.group == MOB_TIMER_GROUP and not sw.show_mob_timers:
            return True
        if row.group == ROLL_TIMER_GROUP and not sw.show_roll_timers:
            return True
        if row.group == TRIGGER_TIMER_GROUP and not sw.show_custom_timers:
            return True
        if isinstance(row, RollRow) and not sw.show_random_rolls:
            return True
        if isinstance(row, SpellRow) and not row.is_target_player:
            return False
        if sw.you_only_spells and isinstance(row, SpellRow):
            # Only OTHER PLAYERS' spell rows — boats, custom/respawn, trigger
            # timers, counters, and rolls are not "spells" and stay visible.
            return True
        if isinstance(row, SpellRow):
            info = self._active_player_info()
            show_classes = info.show_spells_for_classes if info is not None else None
            return hide_spell(show_classes, row.spell.class_levels)
        return False

    def _active_player_info(self):
        player = self._backend.player
        server_key = player.server_key
        if server_key is None or not player.name:
            return None
        return find_player(self._backend.settings, player.name, server_key)

    def _buff_fade_warning_seconds(self) -> int:
        return self._backend.settings.spellwindow.buff_fade_warning_seconds

    def refresh(self, now: datetime | None = None) -> None:
        """Re-render from ``timers.snapshot()`` (rows are never mutated).

        Rebuilds the layout order each tick but reuses the per-row widgets
        keyed by (kind, name, group, dup-index) — cheap at overlay scale.
        """
        now = now if now is not None else datetime.now()
        rows = self._backend.timers.snapshot()
        rows = [row for row in rows if not self._row_hidden(row)]

        grouped: dict[str, list[Row]] = {}
        for row in rows:
            grouped.setdefault(row.group, []).append(row)
        # YOU_GROUP first, then the other targets alphabetically.
        order = sorted(grouped, key=lambda g: (g != YOU_GROUP, g.casefold()))
        sort_mode = self._backend.settings.spellwindow.row_sort

        while self._rows_layout.count():
            self._rows_layout.takeAt(0)

        used_headers: set[str] = set()
        used_rows: set[tuple[str, str, str, int]] = set()
        dup_counter: dict[tuple[str, str, str], int] = {}
        for group in order:
            header = self._headers.get(group)
            if header is None:
                header = QLabel(self._group_label(group), self._container)
                header.setObjectName("SpellTimerGroup")
                header.setProperty("group_key", group)
                self._headers[group] = header
            else:
                # Target class can arrive later (PlayerTracker /who sync).
                label = self._group_label(group)
                if header.text() != label:
                    header.setText(label)
            self._rows_layout.addWidget(header)
            header.show()
            used_headers.add(group)
            for row in sorted(grouped[group], key=lambda r: row_sort_key(r, now, sort_mode)):
                base = (type(row).__name__, row.name.casefold(), row.group.casefold())
                index = dup_counter.get(base, 0)
                dup_counter[base] = index + 1
                key = (*base, index)
                widget = self._row_widgets.get(key)
                if widget is None:
                    widget = _RowWidget(
                        self._container,
                        warning_threshold=self._buff_fade_warning_seconds,
                    )
                    self._row_widgets[key] = widget
                widget.update_row(row, now)
                self._rows_layout.addWidget(widget)
                widget.show()
                used_rows.add(key)

        for group in [g for g in self._headers if g not in used_headers]:
            self._headers.pop(group).deleteLater()
        for key in [k for k in self._row_widgets if k not in used_rows]:
            self._row_widgets.pop(key).deleteLater()
        # Re-fit the scroll host to the rebuilt content: the scroll area's own
        # lazy relayout reliably grows it but not shrinks it, which would leave
        # a stale scroll range after rows leave. (The window itself never
        # resizes — the user's size is authoritative.)
        self._scroll.widget().adjustSize()

    def _group_label(self, group: str) -> str:
        """Header text: the target name, plus its class when the /who
        roster knows it (EQTool's TargetClassString next to the group)."""
        label = group.strip() or group
        tracker = getattr(self._backend, "player_tracker", None)
        if tracker is not None and group != YOU_GROUP:
            player_class = tracker.get_class(group)
            if player_class is not None:
                label = f"{label}  ({player_class.display_name})"
        return label

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

    def apply_window_state(self) -> None:
        """Re-apply opacity/flags from the (possibly just-edited) state.
        (Copy of OverlayWindowBase.apply_window_state — this window predates
        the base class.)"""
        self.setWindowOpacity(self._state.opacity)
        was_visible = self.isVisible()
        self._apply_flags()
        if was_visible:
            self.show()

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
        # Reaches here only when the scroll area didn't consume it (nothing
        # to scroll): stay inert so wheels never pass through to the game.
        event.accept()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        rect = self.rect()
        self._grip.move(rect.right() - self._grip.width(), rect.bottom() - self._grip.height())
        if self.isVisible():
            # Debounced: persists once the grip-drag (or layout change) settles.
            self._persist_resize.start()

    # -- context menu (manual timer clearing) -----------------------------------
    # Note: with click-through enabled the OS never delivers right-clicks
    # here, same as drag-to-move.

    def _context_target(self, pos: QPoint) -> tuple[Row | None, str | None]:
        """Resolve a click position: a row widget yields (row, its group), a
        group header yields (None, group), empty space yields (None, None)."""
        child = self.childAt(pos)
        while child is not None and child is not self:
            if isinstance(child, _RowWidget):
                row = child.row
                return row, (row.group if row is not None else None)
            if isinstance(child, QLabel):
                group = child.property("group_key")
                if group:
                    return None, group
            child = child.parentWidget()
        return None, None

    def _clear_row(self, row: Row) -> None:
        self._backend.timers.remove_row(row)
        self.refresh()

    def _clear_group(self, group: str) -> None:
        self._backend.timers.remove_group(group)
        self.refresh()

    def _clear_all(self) -> None:
        self._backend.timers.clear_all()
        self.refresh()

    def contextMenuEvent(self, event) -> None:
        row, group = self._context_target(event.pos())
        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        if row is not None:
            menu.addAction(f"Clear '{row.name}'", lambda r=row: self._clear_row(r))
        if group is not None:
            label = self._group_label(group)
            menu.addAction(f"Clear group '{label}'", lambda g=group: self._clear_group(g))
        if menu.actions():
            menu.addSeparator()
        menu.addAction("Clear all timers", self._clear_all)
        menu.exec(event.globalPos())
