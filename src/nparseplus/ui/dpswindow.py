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
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from nparseplus.config.settings import Settings
from nparseplus.core.dps import (
    DAMAGE_SOURCE_LABELS,
    DAMAGE_SOURCE_MARKS,
    DEFAULT_DAMAGE_SOURCES,
    FightRow,
    SessionSummary,
)
from nparseplus.ui import skins, theme
from nparseplus.ui.overlaybase import OverlayWindowBase
from nparseplus.ui.skinwidgets import (
    SkinPanel,
    SkinTitleBar,
    paint_full_row_bar,
    set_caps,
)

WINDOW_KEY = "dps"
REFRESH_INTERVAL_MS = 500
DEFAULT_GEOMETRY = (640, 0, 280, 400)

# Row/header colors come from the active theme palette (ui/theme.py); the
# frame, type hierarchy and bar geometry come from the active skin
# (ui/skins.py). Your own row keeps the palette's dps_you gold either way —
# finding yourself in the list is the meter's whole job.
#: Share-of-damage bar color for everyone who is not you.
OTHERS_BAR = "#75798c"

#: What a pet's row is called. The pet has its own name in the log and its
#: own row here; this only says whose it is.
PET_SUFFIX = " (pet)"


class FightsLike(Protocol):
    #: The live counting mode — read off the tracker rather than the settings
    #: object so the title marker follows an Apply without the window being
    #: told about it.
    damage_sources: str

    def snapshot(self, now: datetime) -> list[FightRow]: ...
    def session_summary(self) -> SessionSummary: ...


class BackendLike(Protocol):
    """The slice of ``composition.Backend`` this window needs (test-fakeable)."""

    fights: FightsLike
    settings: Settings


class _AttackerRow(QFrame):
    """One attacker's line: name | total dmg | trailing dps | % of total.

    The share-of-damage bar is the row's own background under the "full" row
    style (Ledger) and a thin rule beneath the text under the stacked ones —
    same numbers, same order, only where the eye lands changes.
    """

    def __init__(
        self, parent: QWidget | None = None, skin: skins.Skin | None = None, font_size: int = 12
    ) -> None:
        super().__init__(parent)
        self.setObjectName("DpsRow")
        self.attacker_name = ""
        self.is_you = False
        self._skin = skin if skin is not None else skins.skin()
        self._percent_fraction = 0.0

        self._name = QLabel(self)
        self._name.setObjectName("SkinRowName")
        self._damage = QLabel(self)
        self._damage.setObjectName("DpsRowDamage")
        self._dps = QLabel(self)
        self._dps.setObjectName("SkinRowValue")
        self._percent = QLabel(self)
        self._percent.setObjectName("DpsRowPercent")
        for label in (self._damage, self._dps, self._percent):
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        layout = QHBoxLayout()
        layout.setSpacing(6)
        layout.addWidget(self._name, 1)
        layout.addWidget(self._damage, 0)
        layout.addWidget(self._dps, 0)
        layout.addWidget(self._percent, 0)
        self.setLayout(layout)
        self.apply_skin(self._skin, font_size)

    def apply_skin(self, skin: skins.Skin, font_size: int) -> None:
        self._skin = skin
        top, right, bottom, left = skin.row_pad
        self.layout().setContentsMargins(left, top, right, bottom)
        if skin.row_style == "full":
            self.setFixedHeight(skins.full_row_height(skin, font_size) + 2)
        else:
            self.setMinimumHeight(0)
            self.setMaximumHeight(16777215)
        self._restyle()
        self.update()

    def _restyle(self) -> None:
        colors = theme.palette()
        color = colors.dps_you if self.is_you else self._skin.name_color
        weight = "bold" if self.is_you else "normal"
        self.setStyleSheet(f"QLabel {{ color: {color}; font-weight: {weight}; }}")

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._skin.row_style != "full" or self._percent_fraction <= 0:
            return
        color = theme.palette().dps_you if self.is_you else OTHERS_BAR
        painter = QPainter(self)
        paint_full_row_bar(painter, self.rect(), color, self._percent_fraction, self._skin.row_rule)
        painter.end()

    def update_row(self, row: FightRow) -> None:
        self.attacker_name = row.attacker_name
        name = row.attacker_name
        if row.level:
            name = f"{name} ({row.level})"
        if row.is_your_pet:
            name = f"{name}{PET_SUFFIX}"
        self._name.setText(name)
        self._damage.setText(str(row.total_damage))
        self._dps.setText(f"{row.dps} dps")
        self._percent.setText(f"{row.percent_of_total}%")
        fraction = max(0.0, min(1.0, row.percent_of_total / 100))
        if fraction != self._percent_fraction:
            self._percent_fraction = fraction
            if self._skin.row_style == "full":
                self.update()
        # Your pet's row wears your highlight: splitting a magician into two
        # rows is the point, treating one of them as a stranger's is not.
        mine = row.is_your_damage or row.is_your_pet
        if mine != self.is_you:
            self.is_you = mine
            self._restyle()


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
        self._skin = skins.skin()
        self._font_size = max(6, backend.settings.general.font_size)

        # The title bar's right-hand cell carries the counting mode. A meter
        # that is excluding spell damage has to say so: without it a caster
        # reads an empty row as a broken parser rather than a filter (#80).
        self._title_bar = SkinTitleBar(self._skin, "DPS METER", count=True, parent=self)
        self._mode_mark = ""

        self._rows_layout = QVBoxLayout()
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(1)

        # Session stats as three labelled cells (best / now / last) rather than
        # one run-on line — the design's footer strip. ``footer_text()`` still
        # returns the one-line form for callers and tests.
        self._footer_summary = ""
        self._footer_cells: dict[str, QLabel] = {}
        self._footer = QWidget(self)
        self._footer.setObjectName("DpsFooter")
        footer_layout = QHBoxLayout(self._footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(0)
        for key, caption in (("best", "BEST"), ("now", "NOW"), ("last", "LAST")):
            cell = QWidget(self._footer)
            cell.setObjectName("DpsFooterCell")
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(7, 3, 7, 3)
            cell_layout.setSpacing(0)
            caption_label = QLabel(caption, cell)
            caption_label.setObjectName("DpsFooterCaption")
            value_label = QLabel("—", cell)
            value_label.setObjectName("DpsFooterValue")
            cell_layout.addWidget(caption_label)
            cell_layout.addWidget(value_label)
            footer_layout.addWidget(cell, 1)
            self._footer_cells[key] = value_label

        self._container = SkinPanel(self._skin, parent=self)
        self._container.setObjectName("DpsMeterContainer")
        container_layout = QVBoxLayout(self._container)
        container_layout.setSpacing(1)
        container_layout.addWidget(self._title_bar, 0)
        container_layout.addLayout(self._rows_layout, 0)
        container_layout.addStretch(1)
        container_layout.addWidget(self._footer, 0)

        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._container)
        self.setLayout(outer)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._on_refresh_tick)
        self._refresh_timer.start(REFRESH_INTERVAL_MS)

        self.apply_skin()
        self.restore_visibility()

    def apply_skin(self) -> None:
        """Re-style from the active skin — live, no restart (see spellwindow)."""
        self._skin = skins.skin()
        self._font_size = max(6, self._backend.settings.general.font_size)
        colors = theme.palette()
        footer_caption = skins.typography_style(
            self._font_size,
            skins.TypographyRole(0.68, "bold", 0.18),
            color=self._skin.plate_border,
        )
        footer_value = skins.typography_style(
            self._font_size, skins.NUMERIC_TEXT, color=self._skin.title_color
        )
        self.setStyleSheet(
            skins.overlay_window_style(self._skin, colors, self._font_size)
            + skins.title_bar_style(self._skin, self._font_size)
            + f"#DpsRowDamage {{ color: {colors.text}; background: transparent;"
            f" {skins.typography_style(self._font_size, skins.TypographyRole(0.88))} }}"
            f"#DpsRowPercent {{ color: {self._skin.plate_border}; background: transparent;"
            f" {skins.typography_style(self._font_size, skins.TypographyRole(0.82))} }}"
            f"#DpsFooter {{ background: {skins.gradient(tuple(reversed(self._skin.title_fill)))};"
            f" border-top: 1px solid {self._skin.title_rule}; }}"
            f"#DpsFooterCaption {{ background: transparent;"
            f" {footer_caption}"
            " }"
            f"#DpsFooterValue {{ background: transparent;"
            f" {footer_value}"
            " }"
        )
        self._container.apply_skin(self._skin, self._backend.settings.general.frame_opacity / 100)
        self._title_bar.apply_skin(self._skin)
        for header in self._headers.values():
            self._style_header(header, bool(getattr(header, "_styled_dead", False)))
        for row in self._rows.values():
            row.apply_skin(self._skin, self._font_size)
        self.refresh()

    def _style_header(self, header: QLabel, is_dead: bool) -> None:
        """A fight header wears the detrimental accent while the mob lives and
        drops to the muted 'player' one once it is slain."""
        kind = skins.KIND_PLAYER if is_dead else skins.KIND_COOLDOWN
        header.setStyleSheet(skins.header_style(self._skin, self._font_size, kind))

    # -- rendering -------------------------------------------------------------

    def _on_refresh_tick(self) -> None:
        """Poll-timer entry: no render work while hidden (showEvent re-renders
        on reopen); refresh() itself stays unguarded for tests/callers."""
        if self.isVisible():
            self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()

    def _refresh_mode_mark(self) -> None:
        """Show which damage the meter is counting, live off the tracker."""
        mode = getattr(self._backend.fights, "damage_sources", DEFAULT_DAMAGE_SOURCES)
        mark = DAMAGE_SOURCE_MARKS.get(mode, "")
        if mark == self._mode_mark:
            return
        self._mode_mark = mark
        self._title_bar.set_count(mark)
        self._title_bar.setToolTip(
            f"Counting {DAMAGE_SOURCE_LABELS.get(mode, mode)} "
            "— Settings > DPS Meter > Count damage from"
        )

    def refresh(self) -> None:
        """Re-render from ``fights.snapshot()`` (rows are never mutated)."""
        now = datetime.now()
        rows = self._backend.fights.snapshot(now)
        self._refresh_mode_mark()

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
                set_caps(header)
                self._headers[target] = header
            suffix = "  (slain)" if first.is_dead else ""
            header.setText(f"{target} — {first.target_total_damage}{suffix}")
            # Restyle only on live/slain transitions — a per-tick setStyleSheet
            # invalidates the header's style cache for no visual change.
            if getattr(header, "_styled_dead", None) != first.is_dead:
                header._styled_dead = first.is_dead
                self._style_header(header, first.is_dead)
            entries.append(header)
            order.append(("H", target))
            used_headers.add(target)
            for row in fight_rows:
                key = (target, row.attacker_name.casefold())
                widget = self._rows.get(key)
                if widget is None:
                    widget = _AttackerRow(self._container, self._skin, self._font_size)
                    self._rows[key] = widget
                widget.update_row(row)
                entries.append(widget)
                order.append(("R", key))
                used_rows.add(key)

        for target in [t for t in self._headers if t not in used_headers]:
            self._headers.pop(target).deleteLater()
        for key in [k for k in self._rows if k not in used_rows]:
            self._rows.pop(key).deleteLater()

        summary = self._backend.fights.session_summary()
        self._footer_summary = self._format_summary(summary)
        self._footer_cells["best"].setText(str(summary.best.highest_dps))
        self._footer_cells["now"].setText(str(summary.current_session.highest_dps))
        self._footer_cells["last"].setText(
            str(summary.last_session.highest_dps) if summary.last_session else "—"
        )

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
        """The session summary as one line (the footer renders it as cells)."""
        return self._footer_summary

    def mode_text(self) -> str:
        """The counting-mode marker in the title bar ("MELEE", "ALL", …)."""
        return self._mode_mark

    def row_names(self) -> list[str]:
        """Attacker row labels as rendered, pet suffix included."""
        out: list[str] = []
        for i in range(self._rows_layout.count()):
            widget = self._rows_layout.itemAt(i).widget()
            if isinstance(widget, _AttackerRow):
                out.append(widget._name.text())
        return out
