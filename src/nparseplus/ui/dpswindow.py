"""DPS meter overlay — renders ``FightTracker`` snapshots.

The UI analogue of EQTool's UI/DPSMeter.xaml + DPSWindowViewModel grouping:
one header per fight target (name + group total damage), one row per
attacker under it (name, total damage, trailing DPS, percent of the group
total), your own row highlighted, plus a session Best/Current/Last footer.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from nparseplus.config.settings import Settings
from nparseplus.core.dps import FightRow, SessionSummary
from nparseplus.ui import theme
from nparseplus.ui.overlaybase import OverlayWindowBase

WINDOW_KEY = "dps"
REFRESH_INTERVAL_MS = 500
DEFAULT_GEOMETRY = (640, 0, 280, 400)

# Row/header colors come from the active theme palette (ui/theme.py).


class FightsLike(Protocol):
    def snapshot(self, now: datetime) -> list[FightRow]: ...
    def session_summary(self) -> SessionSummary: ...


class BackendLike(Protocol):
    """The slice of ``composition.Backend`` this window needs (test-fakeable)."""

    fights: FightsLike
    settings: Settings


class _AttackerRow(QFrame):
    """One attacker's line: name | total dmg | trailing dps | % of total."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DpsRow")
        self.attacker_name = ""
        self.is_you = False

        self._name = QLabel(self)
        self._damage = QLabel(self)
        self._dps = QLabel(self)
        self._percent = QLabel(self)
        for label in (self._damage, self._dps, self._percent):
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        layout = QHBoxLayout()
        layout.setContentsMargins(6, 0, 4, 0)
        layout.setSpacing(6)
        layout.addWidget(self._name, 1)
        layout.addWidget(self._damage, 0)
        layout.addWidget(self._dps, 0)
        layout.addWidget(self._percent, 0)
        self.setLayout(layout)

    def update_row(self, row: FightRow) -> None:
        self.attacker_name = row.attacker_name
        name = row.attacker_name
        if row.level:
            name = f"{name} ({row.level})"
        self._name.setText(name)
        self._damage.setText(str(row.total_damage))
        self._dps.setText(f"{row.dps} dps")
        self._percent.setText(f"{row.percent_of_total}%")
        if row.is_your_damage != self.is_you:
            self.is_you = row.is_your_damage
            colors = theme.palette()
            color = colors.dps_you if row.is_your_damage else colors.text
            weight = "bold" if row.is_your_damage else "normal"
            self.setStyleSheet(f"QLabel {{ color: {color}; font-weight: {weight}; }}")


class DpsMeterWindow(OverlayWindowBase):
    """Frameless always-on-top overlay listing the tracker's fights."""

    def __init__(
        self,
        backend: BackendLike,
        on_save: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            settings=backend.settings,
            window_key=WINDOW_KEY,
            title="DPS Meter",
            default_geometry=DEFAULT_GEOMETRY,
            on_save=on_save,
            parent=parent,
        )
        self._backend = backend
        self._headers: dict[str, QLabel] = {}
        self._rows: dict[tuple[str, str], _AttackerRow] = {}

        self.setObjectName("DpsMeterWindow")
        self.setMinimumSize(220, 140)
        font_size = max(8, backend.settings.general.font_size)
        colors = theme.palette()
        self.setStyleSheet(
            "#DpsMeterContainer {"
            f" background-color: {colors.panel_bg}; border-radius: 4px; }}"
            f"QLabel {{ color: {colors.text}; font-size: {font_size - 2}px; }}"
            f"#DpsTargetHeader {{ color: {colors.heading}; font-weight: bold;"
            f" font-size: {font_size}px; padding: 1px 4px; }}"
            f"#DpsTitle, #DpsFooter {{ color: {colors.heading}; font-size: {font_size - 2}px; }}"
        )

        self._title = QLabel("DPS Meter", self)
        self._title.setObjectName("DpsTitle")
        self._title.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        self._rows_layout = QVBoxLayout()
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(1)

        self._footer = QLabel("", self)
        self._footer.setObjectName("DpsFooter")
        self._footer.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        container_layout = QVBoxLayout()
        container_layout.setContentsMargins(2, 2, 2, 2)
        container_layout.setSpacing(1)
        container_layout.addWidget(self._title, 0)
        container_layout.addLayout(self._rows_layout, 0)
        container_layout.addStretch(1)
        container_layout.addWidget(self._footer, 0)

        container = QFrame(self)
        container.setObjectName("DpsMeterContainer")
        container.setLayout(container_layout)
        self._container = container

        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(container)
        self.setLayout(outer)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._on_refresh_tick)
        self._refresh_timer.start(REFRESH_INTERVAL_MS)

        self.restore_visibility()

    # -- rendering -------------------------------------------------------------

    def _on_refresh_tick(self) -> None:
        """Poll-timer entry: no render work while hidden (showEvent re-renders
        on reopen); refresh() itself stays unguarded for tests/callers."""
        if self.isVisible():
            self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        """Re-render from ``fights.snapshot()`` (rows are never mutated)."""
        now = datetime.now()
        rows = self._backend.fights.snapshot(now)
        colors = theme.palette()

        # Preserve snapshot order: fights in start order, attackers by damage.
        grouped: dict[str, list[FightRow]] = {}
        for row in rows:
            grouped.setdefault(row.target_name, []).append(row)

        entries: list[QWidget] = []
        order: list[object] = []
        used_headers: set[str] = set()
        used_rows: set[tuple[str, str]] = set()
        for target, fight_rows in grouped.items():
            first = fight_rows[0]
            header = self._headers.get(target)
            if header is None:
                header = QLabel(self._container)
                header.setObjectName("DpsTargetHeader")
                header.setProperty("target_key", target)
                self._headers[target] = header
            suffix = "  (slain)" if first.is_dead else ""
            header.setText(f"{target} — {first.target_total_damage}{suffix}")
            # Restyle only on live/slain transitions — a per-tick setStyleSheet
            # invalidates the header's style cache for no visual change.
            if getattr(header, "_styled_dead", None) != first.is_dead:
                header._styled_dead = first.is_dead
                bg = colors.dps_dead_header if first.is_dead else colors.dps_live_header
                header.setStyleSheet(f"background-color: {bg};")
            entries.append(header)
            order.append(("H", target))
            used_headers.add(target)
            for row in fight_rows:
                key = (target, row.attacker_name.casefold())
                widget = self._rows.get(key)
                if widget is None:
                    widget = _AttackerRow(self._container)
                    self._rows[key] = widget
                widget.update_row(row)
                entries.append(widget)
                order.append(("R", key))
                used_rows.add(key)

        for target in [t for t in self._headers if t not in used_headers]:
            self._headers.pop(target).deleteLater()
        for key in [k for k in self._rows if k not in used_rows]:
            self._rows.pop(key).deleteLater()

        self._footer.setText(self._format_summary(self._backend.fights.session_summary()))

        # Only rebuild the layout when the widget sequence changed (same
        # dirty-check as the spell window — skip the per-tick relayout).
        if order == getattr(self, "_layout_order", None):
            return
        self._layout_order = order
        while self._rows_layout.count():
            self._rows_layout.takeAt(0)
        for widget in entries:
            self._rows_layout.addWidget(widget)
            widget.show()

    @staticmethod
    def _format_summary(summary: SessionSummary) -> str:
        last = f"{summary.last_session.highest_dps}" if summary.last_session else "—"
        return (
            f"Best {summary.best.highest_dps} dps | "
            f"Current {summary.current_session.highest_dps} dps | "
            f"Last {last}"
        )

    # -- test/debug hooks --------------------------------------------------------

    def current_targets(self) -> list[str]:
        """Target header keys in on-screen order."""
        out: list[str] = []
        for i in range(self._rows_layout.count()):
            widget = self._rows_layout.itemAt(i).widget()
            if isinstance(widget, QLabel):
                out.append(widget.property("target_key"))
        return out

    def current_attackers(self) -> list[str]:
        """Attacker row names in on-screen order."""
        out: list[str] = []
        for i in range(self._rows_layout.count()):
            widget = self._rows_layout.itemAt(i).widget()
            if isinstance(widget, _AttackerRow):
                out.append(widget.attacker_name)
        return out

    def your_rows(self) -> list[str]:
        """Attacker names of rows currently highlighted as yours."""
        out: list[str] = []
        for i in range(self._rows_layout.count()):
            widget = self._rows_layout.itemAt(i).widget()
            if isinstance(widget, _AttackerRow) and widget.is_you:
                out.append(widget.attacker_name)
        return out

    def footer_text(self) -> str:
        return self._footer.text()
