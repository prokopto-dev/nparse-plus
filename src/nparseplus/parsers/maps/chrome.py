"""Map chrome — the surfaces that sit over the map canvas.

The maps window used to spend a permanent strip of itself on a row of
single-glyph buttons, and the canvas was an opaque black rectangle under it.
This module replaces that with chrome that gets out of the way: a header and
a labelled toolbar that fade in on hover (the trick ``ParserWindow``'s
``auto_hide_menu`` already used for its menu strip), edge tabs naming the
zone's exits while the pointer is away, a recenter puck that lights up with
bearing and distance once you have panned off yourself, a summonable rail
(Tab) listing what the zone actually has, and a find palette (Ctrl+F).

Everything here is presentation. The widgets read values handed to them and
emit plain callbacks; the zone data, the player fix and the toggles all still
live in ``MapCanvas``/``Maps``. The geometry helpers at the top are pure so
the interesting parts (which edge a zone line projects onto, what the puck
should say) are testable without a live window.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

# The chrome's own palette. Deliberately NOT the overlay skin's: the map is a
# single surface with one look, and the frame tokens a skin carries (plate,
# notch, row style) have nothing to answer here. It matches the skins' shared
# tan/black EQ register so the windows still read as one app.
GOLD = "#c8a951"
GOLD_DIM = "#8a7549"
GOLD_BRIGHT = "#d4b675"
INK = "rgba(6, 7, 10, 0.86)"
INK_SOLID = "rgba(6, 7, 10, 0.96)"
RULE = "#2b2519"
EDGE = "#6b5a3a"
GREEN = "#2f9e6e"
GREEN_TEXT = "#7fe0b4"
AMBER = "#d99b2b"
AMBER_TEXT = "#f0dcae"
RED = "#e05a49"
TEXT = "#e4e7f5"
MUTED = "#595d6c"

#: Compass points, N first, clockwise — ``bearing_arrow`` indexes into this.
ARROWS = ("↑", "↗", "→", "↘", "↓", "↙", "←", "↖")
#: Cardinal names for the same eight sectors.
COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def bearing_index(dx: float, dy: float) -> int:
    """The eight-point sector of the vector ``(dx, dy)`` in SCREEN space.

    ``dy`` grows downward (Qt), so "north" is negative dy. Returns an index
    into :data:`ARROWS` / :data:`COMPASS`, 0 = N, going clockwise.
    """
    if dx == 0 and dy == 0:
        return 0
    # atan2(dx, -dy) puts 0 at north and grows clockwise.
    angle = math.degrees(math.atan2(dx, -dy)) % 360
    return int((angle + 22.5) % 360 // 45)


def bearing_arrow(dx: float, dy: float) -> str:
    return ARROWS[bearing_index(dx, dy)]


def compass_name(dx: float, dy: float) -> str:
    return COMPASS[bearing_index(dx, dy)]


def format_distance(units: float) -> str:
    """Game units as a short label: 840, 1.2k, 14k."""
    units = abs(units)
    if units < 1000:
        return str(int(round(units)))
    if units < 10_000:
        return f"{units / 1000:.1f}k"
    return f"{round(units / 1000)}k"


def edge_anchor(dx: float, dy: float, width: int, height: int, inset: int = 0) -> tuple[int, int]:
    """Where a point in direction ``(dx, dy)`` from the center meets the border.

    Used to park a zone-line tab on the window edge you would leave through.
    Returns a top-left position for a zero-size marker; callers offset by
    their own size. ``inset`` pulls the anchor in from the border.
    """
    half_w = max(1, width / 2)
    half_h = max(1, height / 2)
    if dx == 0 and dy == 0:
        return int(half_w), inset
    # Scale the vector until it hits whichever border it reaches first.
    scale = min(
        half_w / abs(dx) if dx else float("inf"),
        half_h / abs(dy) if dy else float("inf"),
    )
    x = half_w + dx * scale
    y = half_h + dy * scale
    x = min(max(x, inset), width - inset)
    y = min(max(y, inset), height - inset)
    return int(x), int(y)


def zone_line_label(text: str) -> str:
    """``to_Northern_Ro`` -> ``Northern Ro``; anything else comes back trimmed."""
    label = text.strip().lstrip("✪ ").strip()
    lowered = label.lower()
    for prefix in ("to_", "to "):
        if lowered.startswith(prefix):
            label = label[len(prefix) :]
            break
    return label.replace("_", " ").strip()


def is_zone_line(text: str) -> bool:
    lowered = text.strip().lstrip("✪ ").strip().lower()
    return lowered.startswith(("to_", "to "))


def format_respawn(value) -> str:
    """Respawn as m:ss (or h:mm:ss). ``None`` -> an em dash.

    Accepts either a seconds count (``core.zones``) or an already-formatted
    ``"6:40"`` string (``MapData.get_default_spawn_timer``, which reads
    map_timers.csv and hands back its literal) — the rail draws both.
    """
    if not value:
        return "—"
    if isinstance(value, str):
        return value.strip()
    seconds = int(value)
    if seconds >= 3600:
        return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60}:{seconds % 60:02d}"


def _chip(text: str, color: str, tint: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setStyleSheet(
        f"color: {color}; background-color: {tint}; font-size: 9px; font-weight: bold;"
        f" letter-spacing: 0.6px; padding: 1px 6px;"
    )
    return label


class _Fading(QWidget):
    """A chrome panel the map summons and dismisses.

    ``WA_StyledBackground`` is not optional here: a bare ``QWidget`` ignores a
    ``background-color`` rule, and these panels sit over the map — without a
    ground of their own their text would be unreadable against the geometry.
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.hide()


class MapHeader(_Fading):
    """Top strip: zone name, live loc chip, Z badge, exits, find/rail.

    Named exits are the reason the edge tabs stand down while this is up —
    two things pointing at the same doors is one thing too many.
    """

    def __init__(self, parent: QWidget, on_find, on_rail, on_exit) -> None:
        super().__init__(parent)
        self._on_exit = on_exit
        self.setObjectName("MapHeader")
        self.setStyleSheet(
            f"#MapHeader {{ background-color: {INK_SOLID}; border-bottom: 1px solid {RULE}; }}"
        )

        self._zone = QLabel("", self)
        self._zone.setStyleSheet(
            f"color: {GOLD_BRIGHT}; font-size: 10px; font-weight: bold; letter-spacing: 1.8px;"
        )
        self._loc = _chip("—", AMBER_TEXT, "rgba(217, 155, 43, 0.14)", self)
        self._z = _chip("Z —", GOLD, "rgba(200, 169, 81, 0.12)", self)

        top = QHBoxLayout()
        top.setContentsMargins(8, 5, 8, 3)
        top.setSpacing(8)
        top.addWidget(self._zone, 1)
        top.addWidget(self._loc, 0)
        top.addWidget(self._z, 0)

        self._exits_caption = QLabel("EXITS", self)
        self._exits_caption.setStyleSheet(
            f"color: {MUTED}; font-size: 8px; font-weight: bold; letter-spacing: 1.4px;"
        )
        self._exits_row = QHBoxLayout()
        self._exits_row.setContentsMargins(0, 0, 0, 0)
        self._exits_row.setSpacing(4)
        self._exit_chips: list[QLabel] = []

        find = self._action("⌕ FIND", on_find)
        rail = self._action("▤ RAIL", on_rail)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(8, 0, 8, 5)
        bottom.setSpacing(6)
        bottom.addWidget(self._exits_caption, 0)
        bottom.addLayout(self._exits_row, 0)
        bottom.addStretch(1)
        bottom.addWidget(find, 0)
        bottom.addWidget(rail, 0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(top)
        layout.addLayout(bottom)

    def _action(self, text: str, callback) -> QLabel:
        label = QLabel(text, self)
        label.setCursor(Qt.CursorShape.PointingHandCursor)
        label.setStyleSheet(
            f"color: {GOLD}; font-size: 9px; font-weight: bold; letter-spacing: 1px;"
        )
        label.mousePressEvent = lambda event, cb=callback: cb()
        return label

    def set_zone(self, name: str) -> None:
        self._zone.setText(name.upper())

    def set_location(self, text: str, age: str) -> None:
        self._loc.setText(f"{text}   {age}" if age else text)

    def set_z(self, text: str) -> None:
        self._z.setText(text)

    def set_exits(self, exits: list[tuple[str, tuple[float, float]]]) -> None:
        """``[(display name, (x, y)), …]`` — clicking a chip flashes it."""
        while self._exit_chips:
            chip = self._exit_chips.pop()
            self._exits_row.removeWidget(chip)
            chip.deleteLater()
        for name, location in exits[:4]:
            chip = _chip(name, GREEN_TEXT, "rgba(47, 158, 110, 0.14)", self)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.mousePressEvent = lambda event, loc=location: self._on_exit(loc)
            self._exits_row.addWidget(chip)
            self._exit_chips.append(chip)
        self._exits_caption.setVisible(bool(self._exit_chips))


class MapToolbar(_Fading):
    """Bottom strip: the map toggles, labelled instead of glyph-only."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("MapToolbar")
        self.setStyleSheet(
            f"#MapToolbar {{ background-color: {INK_SOLID}; border-top: 1px solid {RULE}; }}"
        )
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._buttons: dict[str, QLabel] = {}
        self._recording = QLabel("● REC TRAIL", self)
        self._recording.setStyleSheet(
            f"color: {RED}; font-size: 8px; font-weight: bold; letter-spacing: 1.4px;"
            f" padding: 5px 8px; border-left: 1px solid {RULE};"
        )
        self._recording.hide()

    def add_toggle(self, key: str, text: str, tooltip: str, callback) -> None:
        label = QLabel(text, self)
        label.setToolTip(tooltip)
        label.setCursor(Qt.CursorShape.PointingHandCursor)
        label.mousePressEvent = lambda event, cb=callback: cb()
        self._layout.addWidget(label)
        self._buttons[key] = label
        self.set_toggle(key, False)

    def finish(self) -> None:
        """Call once every toggle is added: adds the stretch + REC indicator."""
        self._layout.addStretch(1)
        self._layout.addWidget(self._recording)

    def set_toggle(self, key: str, on: bool) -> None:
        label = self._buttons.get(key)
        if label is None:
            return
        color = GOLD if on else MUTED
        label.setStyleSheet(
            f"color: {color}; font-size: 8px; font-weight: bold; letter-spacing: 1.4px;"
            f" padding: 5px 8px; border-right: 1px solid {RULE};"
        )

    def set_recording(self, on: bool) -> None:
        self._recording.setVisible(on)


class RecenterPuck(QWidget):
    """Bottom-right disc: muted when centred, lit with bearing + distance once
    you have panned off your own dot.

    Getting back to yourself used to mean another ``/loc`` (or a lucky drag);
    this makes the way back a single click that is always on screen.
    """

    SIZE = 42

    def __init__(self, parent: QWidget, on_click) -> None:
        super().__init__(parent)
        self._on_click = on_click
        self._arrow = ""
        self._distance = ""
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setToolTip("Recenter on you")

    def set_offset(self, arrow: str, distance: str) -> None:
        """``("", "")`` = centred (the muted state)."""
        if (arrow, distance) != (self._arrow, self._distance):
            self._arrow, self._distance = arrow, distance
            self.update()

    def is_lit(self) -> bool:
        return bool(self._arrow)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_click()
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        lit = self.is_lit()
        painter.setBrush(QColor(6, 7, 10, 220 if lit else 179))
        painter.setPen(QColor(GOLD) if lit else QColor(107, 90, 58, 140))
        painter.drawEllipse(rect)
        painter.setPen(QColor(GREEN_TEXT) if lit else QColor(107, 90, 58))
        font = painter.font()
        if lit:
            font.setPixelSize(15)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(
                QRect(rect.left(), rect.top() + 5, rect.width(), 16),
                Qt.AlignmentFlag.AlignHCenter,
                self._arrow,
            )
            font.setPixelSize(9)
            painter.setFont(font)
            painter.setPen(QColor(GOLD_BRIGHT))
            painter.drawText(
                QRect(rect.left(), rect.top() + 21, rect.width(), 12),
                Qt.AlignmentFlag.AlignHCenter,
                self._distance,
            )
        else:
            font.setPixelSize(15)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "⊙")
        painter.end()


class ZoneEdgeTab(QWidget):
    """A small tab parked on the window edge you would leave through.

    Only up while the header is down: the header names both exits in words,
    and two answers to "which way out" is one too many.
    """

    def __init__(self, parent: QWidget, name: str, arrow: str, vertical: bool) -> None:
        super().__init__(parent)
        text = f"{name} {arrow}" if not vertical else f"{arrow} {name}"
        self._label = QLabel(text.upper(), self)
        side = "border-bottom" if vertical else "border-right"
        self._label.setStyleSheet(
            f"color: {GREEN_TEXT}; background-color: {INK_SOLID};"
            f" font-size: 9px; font-weight: bold; letter-spacing: 1px;"
            f" padding: 2px 6px; {side}: 2px solid {GREEN};"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label)


class MapRail(_Fading):
    """The Tab panel: what this zone actually has.

    Deliberately shows the zone's real contents rather than a fixed set of
    headings — a zone with two zone lines and no notable NPCs shows two zone
    lines and no NPC section, instead of an empty box implying nParse+ lost
    them.
    """

    WIDTH = 190

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("MapRail")
        self.setStyleSheet(
            f"#MapRail {{ background-color: {INK_SOLID}; border-left: 1px solid {EDGE}; }}"
        )
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._rows: list[QWidget] = []

    def rebuild(self, zone: str, sections: list[tuple[str, list[tuple[str, str, str]]]]) -> None:
        """Re-render. ``sections`` is ``[(caption, [(name, value, accent), …])]``;
        an empty section is dropped rather than shown as a heading with nothing
        under it."""
        for row in self._rows:
            self._layout.removeWidget(row)
            row.deleteLater()
        self._rows.clear()

        title = QLabel(zone.upper(), self)
        title.setStyleSheet(
            f"color: {GOLD_BRIGHT}; font-size: 9px; font-weight: bold; letter-spacing: 1.8px;"
            f" padding: 6px 9px; border-bottom: 1px solid {RULE};"
        )
        self._add(title)
        for caption, entries in sections:
            if not entries:
                continue
            head = QLabel(f"{caption}   {len(entries)}", self)
            head.setStyleSheet(
                f"color: {GOLD_DIM}; font-size: 8px; font-weight: bold; letter-spacing: 1.6px;"
                " padding: 8px 9px 3px;"
            )
            self._add(head)
            for name, value, accent in entries:
                self._add(self._entry(name, value, accent))
        self._layout.addStretch(1)
        hint = QLabel("TAB TO DISMISS", self)
        hint.setStyleSheet(
            f"color: {MUTED}; font-size: 8px; letter-spacing: 1px; padding: 5px 9px;"
            f" border-top: 1px solid {RULE};"
        )
        self._add(hint)

    def _add(self, widget: QWidget) -> None:
        self._layout.addWidget(widget)
        self._rows.append(widget)

    def _entry(self, name: str, value: str, accent: str) -> QWidget:
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(9, 2, 9, 2)
        layout.setSpacing(6)
        label = QLabel(name, row)
        label.setStyleSheet(f"color: {TEXT}; font-size: 10px;")
        amount = QLabel(value, row)
        amount.setStyleSheet(f"color: {MUTED}; font-size: 9px;")
        layout.addWidget(label, 1)
        layout.addWidget(amount, 0)
        row.setStyleSheet(f"border-left: 2px solid {accent};")
        return row


def _panel_height(widget: QWidget) -> int:
    """A hidden panel's natural height.

    ``ensurePolished`` first: an unpolished widget reports a sizeHint computed
    before its stylesheet padding exists, which lays the strip out a third too
    short and clips its labels. A widget with no layout has no hint at all
    (Qt returns -1), so its current height is the answer.
    """
    widget.ensurePolished()
    candidates = [widget.sizeHint().height(), widget.minimumSizeHint().height()]
    layout = widget.layout()
    if layout is not None:
        candidates += [layout.sizeHint().height(), layout.minimumSize().height()]
    hint = max(candidates)
    return hint if hint > 0 else widget.height()


def place_chrome(
    window_rect: QRect,
    header: QWidget,
    toolbar: QWidget,
    rail: QWidget,
    puck: QWidget,
    rail_open: bool,
) -> None:
    """Lay the chrome over the canvas. Pure geometry, no state."""
    width, height = window_rect.width(), window_rect.height()
    rail_width = min(MapRail.WIDTH, max(0, width - 40)) if rail_open else 0
    # The rail owns the full right column while it is up, so the header and
    # toolbar stop at its edge rather than sliding underneath it.
    body = max(0, width - rail_width)
    header.setGeometry(0, 0, body, _panel_height(header))
    toolbar_height = _panel_height(toolbar)
    toolbar.setGeometry(0, height - toolbar_height, body, toolbar_height)
    full_rail = min(MapRail.WIDTH, max(0, width - 40))
    rail.setGeometry(width - full_rail, 0, full_rail, height)
    puck.move(QPoint(body - puck.width() - 10, height - toolbar_height - puck.height() - 8))
