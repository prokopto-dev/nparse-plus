"""The painted half of the skin layer.

``ui/skins.py`` is data and stylesheet strings; three parts of the design
cannot be expressed in a Qt stylesheet at all, and live here:

- the Velious plate's **notched corners** (QSS has no ``clip-path``),
- the Ledger row's **full-row draining bar**, which sits *behind* the row's
  own labels rather than under them, and
- the **gem mark** beside a title — a rotated square with a soft glow.

Everything here paints; nothing here decides. Colors, sizes and geometry all
arrive from a :class:`~nparseplus.ui.skins.Skin`.
"""

from __future__ import annotations

import re

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from nparseplus.ui import chrome
from nparseplus.ui.skins import Skin

_RGBA = re.compile(
    r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)", re.IGNORECASE
)


def qcolor(spec: str, opacity: float = 1.0) -> QColor:
    """Parse a skin color into a ``QColor``, scaling its alpha by ``opacity``.

    Handles the three forms the skins use: ``#rrggbb``, ``rgba(r, g, b, a)``
    with either a 0-255 integer alpha (Qt stylesheet style) or a 0-1 float
    (CSS style), and ``transparent``. Unknown values come back transparent
    rather than raising — a skin typo must not crash an overlay repaint.
    """
    spec = (spec or "").strip()
    if not spec or spec == "transparent":
        return QColor(0, 0, 0, 0)
    match = _RGBA.match(spec)
    if match:
        red, green, blue, alpha = match.groups()
        # "0.5" is CSS-style (0-1); "180" is Qt-stylesheet style (0-255).
        if alpha is None:
            value = 255
        elif "." in alpha:
            value = round(float(alpha) * 255)
        else:
            value = int(alpha)
        color = QColor(int(red), int(green), int(blue), max(0, min(255, value)))
    else:
        color = QColor(spec)
        if not color.isValid():
            return QColor(0, 0, 0, 0)
    color.setAlpha(round(color.alpha() * max(0.0, min(1.0, opacity))))
    return color


def set_caps(label: QWidget, on: bool = True) -> None:
    """Render ``label``'s text in caps without changing the text itself.

    Qt stylesheets have no ``text-transform``, and uppercasing the string
    would leak into every read of ``label.text()`` (the window's group-order
    test hooks, the context menu's "Clear group '…'"). A font capitalization
    is purely a rendering property, so the model stays the model.
    """
    font = label.font()
    font.setCapitalization(
        QFont.Capitalization.AllUppercase if on else QFont.Capitalization.MixedCase
    )
    label.setFont(font)


def notched_path(rect: QRectF, notch: int) -> QPainterPath:
    """An octagonal path: ``rect`` with each corner cut back by ``notch`` px.

    ``notch <= 0`` (or a rect too small to cut) returns the plain rectangle,
    so the same call sites serve the square-cornered skins.
    """
    path = QPainterPath()
    cut = min(notch, int(min(rect.width(), rect.height()) / 2)) if notch > 0 else 0
    if cut <= 0:
        path.addRect(rect)
        return path
    left, top, right, bottom = rect.left(), rect.top(), rect.right(), rect.bottom()
    path.moveTo(left, top + cut)
    path.lineTo(left + cut, top)
    path.lineTo(right - cut, top)
    path.lineTo(right, top + cut)
    path.lineTo(right, bottom - cut)
    path.lineTo(right - cut, bottom)
    path.lineTo(left + cut, bottom)
    path.lineTo(left, bottom - cut)
    path.closeSubpath()
    return path


def _fill(rect: QRectF, stops: tuple[str, ...], opacity: float) -> QBrush:
    """A flat or vertical-gradient brush from a skin's color stops."""
    if len(stops) == 1:
        return QBrush(qcolor(stops[0], opacity))
    grad = QLinearGradient(QPointF(rect.left(), rect.top()), QPointF(rect.left(), rect.bottom()))
    for index, color in enumerate(stops):
        grad.setColorAt(index / (len(stops) - 1), qcolor(color, opacity))
    return QBrush(grad)


class SkinPanel(QFrame):
    """The container every skinned overlay puts its content in.

    Paints the skin's two-layer frame — an outer plate (the stone/black
    edge) and the inner glass the content sits on — honoring the plate's
    corner notch. ``frame_opacity`` scales *only* these fills, so a user can
    turn the frame down to a whisper and keep countdowns at full contrast;
    that split is the point (window opacity fades everything, including the
    numbers you are trying to read).
    """

    def __init__(self, skin: Skin, frame_opacity: float = 1.0, parent: QWidget | None = None):
        super().__init__(parent)
        self._skin = skin
        self._frame_opacity = frame_opacity
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def apply_skin(self, skin: Skin, frame_opacity: float = 1.0) -> None:
        self._skin = skin
        self._frame_opacity = frame_opacity
        if self.layout() is not None:
            inset = self.frame_inset()
            self.layout().setContentsMargins(inset, inset, inset, inset)
        self.update()

    def frame_inset(self) -> int:
        """Padding a content layout needs so children clear the frame: the
        plate border, its padding, and the glass border."""
        return self._skin.plate_padding + 2

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, bool(self._skin.notch))
        skin = self._skin
        opacity = self._frame_opacity

        outer = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        plate = notched_path(outer, skin.notch)
        painter.fillPath(plate, _fill(outer, skin.plate, opacity))
        border = qcolor(skin.plate_border, opacity)
        if border.alpha():
            painter.strokePath(plate, QPen(border, 1))

        pad = skin.plate_padding
        inner = outer.adjusted(pad, pad, -pad, -pad)
        if inner.width() <= 0 or inner.height() <= 0:
            painter.end()
            return
        # The inner notch shrinks with the padding so the two outlines stay
        # parallel instead of the inner one cutting through the outer bevel.
        glass = notched_path(inner, max(0, skin.notch - pad))
        painter.fillPath(glass, _fill(inner, skin.glass, opacity))
        glass_border = qcolor(skin.glass_border, opacity)
        if glass_border.alpha():
            painter.strokePath(glass, QPen(glass_border, 1))
        painter.end()


class GemMark(QWidget):
    """The rotated-square gem beside a skinned title (Duxa's tan pip, the
    Velious socket gem). Zero-sized and invisible when the skin has no mark."""

    def __init__(self, skin: Skin, parent: QWidget | None = None):
        super().__init__(parent)
        self._skin = skin
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.apply_skin(skin)

    def apply_skin(self, skin: Skin) -> None:
        self._skin = skin
        size = skin.mark_size
        if not size or not skin.mark_color:
            self.setFixedSize(0, 0)
            self.setVisible(False)
        else:
            # Room for the glow halo around the gem itself.
            self.setFixedSize(size + 6, size + 6)
            self.setVisible(True)
        self.update()

    def paintEvent(self, event) -> None:
        skin = self._skin
        if not skin.mark_size or not skin.mark_color:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(self.width() / 2, self.height() / 2)
        half = skin.mark_size / 2
        if skin.mark_glow:
            glow = qcolor(skin.mark_glow)
            for step, radius in enumerate((half + 3, half + 1.5)):
                glow.setAlpha(max(0, glow.alpha() // (3 - step)))
                painter.setBrush(QBrush(glow))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawPolygon(_diamond(center, radius))
        painter.setBrush(QBrush(qcolor(skin.mark_color)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(_diamond(center, half))
        painter.end()


def _diamond(center: QPointF, radius: float) -> QPolygonF:
    """A square rotated 45° about ``center``.

    Built as a ``QPolygonF``, not a Python list: PySide6's ``drawPolygon``
    overload resolution on a bare list of ``QPointF`` crashes the interpreter.
    """
    return QPolygonF(
        [
            QPointF(center.x(), center.y() - radius),
            QPointF(center.x() + radius, center.y()),
            QPointF(center.x(), center.y() + radius),
            QPointF(center.x() - radius, center.y()),
        ]
    )


class SkinTitleBar(QWidget):
    """The gem + caps strip every skinned overlay wears.

    Three windows built this by hand, identically, down to the layout margins
    and the object names — the *styling* was already shared through
    ``skins.title_bar_style``, only the construction was not. The object names
    stay exactly as they were so that stylesheet is untouched.
    """

    def __init__(
        self,
        skin: Skin,
        title: str,
        *,
        count: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SkinTitleBar")
        self.mark = GemMark(skin, self)
        self.title = QLabel(title, self)
        self.title.setObjectName("SkinTitle")
        #: Only the spell window shows a count; the others leave it out
        #: entirely rather than carrying an empty label around.
        self.count: QLabel | None = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(6)
        layout.addWidget(self.mark, 0)
        layout.addWidget(self.title, 1)
        if count:
            self.count = QLabel("", self)
            self.count.setObjectName("SkinTitleCount")
            layout.addWidget(self.count, 0)

    def set_title(self, text: str) -> None:
        self.title.setText(text)

    def set_count(self, text: str) -> None:
        if self.count is not None:
            self.count.setText(text)

    def apply_skin(self, skin: Skin) -> None:
        self.mark.apply_skin(skin)


def paint_full_row_bar(
    painter: QPainter, rect: QRect, color: str, fraction: float, rule: int
) -> None:
    """Ledger's row background: a draining block with a solid left rule.

    ``fraction`` is the row's remaining share (1.0 = full width). The block
    fades left-to-right so a long row never becomes a slab of color, and the
    rule stays full-strength at the left edge where the eye lands.
    """
    fraction = max(0.0, min(1.0, fraction))
    width = round(rect.width() * fraction)
    if width <= 0:
        return
    block = QRect(rect.left(), rect.top(), width, rect.height())
    base = qcolor(color)
    grad = QLinearGradient(QPointF(block.left(), 0.0), QPointF(block.right(), 0.0))
    start = QColor(base)
    start.setAlpha(94)
    end = QColor(base)
    end.setAlpha(28)
    grad.setColorAt(0.0, start)
    grad.setColorAt(1.0, end)
    painter.fillRect(block, QBrush(grad))
    if rule > 0:
        painter.fillRect(QRect(rect.left(), rect.top(), rule, rect.height()), base)


#: Row colors in a preview thumbnail — one beneficial, one custom timer, one
#: detrimental, so every skin's three bar treatments are visible at a glance.
#: Read from the semantic tokens so a preview cannot drift from the real rows.
PREVIEW_ROW_COLORS = (chrome.GOOD, chrome.TIMER, chrome.BAD)


class SkinPreview(QWidget):
    """A thumbnail of one skin, for the Settings picker and the tray menu.

    Paints the same two-layer frame, title strip and row treatment the real
    overlays use, at ~1/5 scale — so what the picker shows is what the
    windows do, not a hand-drawn approximation that can drift.
    """

    def __init__(self, skin: Skin, parent: QWidget | None = None):
        super().__init__(parent)
        self._skin = skin
        self.setMinimumSize(70, 46)

    def set_skin(self, skin: Skin) -> None:
        self._skin = skin
        self.update()

    def paintEvent(self, event) -> None:
        skin = self._skin
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, bool(skin.notch))
        outer = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        notch = min(skin.notch, 5)
        plate = notched_path(outer, notch)
        painter.fillPath(plate, _fill(outer, skin.plate, 1.0))
        painter.strokePath(plate, QPen(qcolor(skin.plate_border), 1))
        pad = min(skin.plate_padding, 3)
        inner = outer.adjusted(pad, pad, -pad, -pad)
        glass = notched_path(inner, max(0, notch - pad))
        painter.fillPath(glass, _fill(inner, skin.glass, 1.0))
        painter.strokePath(glass, QPen(qcolor(skin.glass_border), 1))

        body = inner.toRect().adjusted(1, 1, -1, -1)
        title_height = 8
        title = QRect(body.left(), body.top(), body.width(), title_height)
        painter.fillRect(title, _fill(QRectF(title), skin.title_fill, 1.0))
        painter.fillRect(
            QRect(title.left(), title.bottom(), title.width(), 1), qcolor(skin.title_rule)
        )

        top = title.bottom() + 3
        available = body.bottom() - top - 2
        if available <= 0:
            painter.end()
            return
        slot = max(4, available // len(PREVIEW_ROW_COLORS))
        for index, color in enumerate(PREVIEW_ROW_COLORS):
            row_top = top + index * slot
            if row_top + 3 > body.bottom():
                break
            fraction = (1.0, 0.7, 0.42)[index]
            if skin.row_style == "full":
                paint_full_row_bar(
                    painter,
                    QRect(body.left(), row_top, body.width(), slot - 2),
                    color,
                    fraction,
                    min(skin.row_rule, 2),
                )
            else:
                width = max(2, round((body.width() - 8) * fraction))
                height = max(2, min(4, skin.bar_height))
                painter.fillRect(
                    QRect(body.left() + 4, row_top + (slot - 2 - height) // 2, width, height),
                    qcolor(color),
                )
        painter.end()


def paint_hairline(painter: QPainter, rect: QRect, color: str) -> None:
    """A rule that fades to nothing at both ends (the Nocturne edge rule)."""
    tint = qcolor(color)
    if not tint.alpha() or rect.width() <= 0:
        return
    grad = QLinearGradient(QPointF(rect.left(), 0.0), QPointF(rect.right(), 0.0))
    clear = QColor(tint)
    clear.setAlpha(0)
    grad.setColorAt(0.0, clear)
    grad.setColorAt(0.5, tint)
    grad.setColorAt(1.0, clear)
    painter.fillRect(rect, QBrush(grad))
