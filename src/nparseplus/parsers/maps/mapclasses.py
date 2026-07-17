import datetime

import colorhash
from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItemGroup,
    QGraphicsLineItem,
    QGraphicsPixmapItem,
    QGraphicsPolygonItem,
    QGraphicsTextItem,
)

from nparseplus.helpers import config, format_time, get_degrees_from_line, to_eq_xy

# The local player's marker color — EQTool MapViewModelService.cs rgb(61,235,52).
YOU_COLOR = QColor(61, 235, 52)


def map_font_pct() -> int:
    """Current map label scale in percent (EQTool GlobalFontSize analogue)."""
    return int(config.data.get("maps", {}).get("map_font_scale", 100))


def scaled_font_size(base: int) -> int:
    """HTML <font size> (1-7) scaled by the map label setting, clamped."""
    return max(1, min(7, round(base * map_font_pct() / 100)))


# Marker geometry in group units (the group is counter-scaled to constant
# pixel size, so these are effectively pixels): EQTool draws a small circle
# with an arrow of ~length whose head is length/4.
_DOT_RADIUS = 6.0
_ARROW_LENGTH = 14.0
_ARROW_HEAD = _ARROW_LENGTH / 4


def _arrow_polygon() -> QPolygonF:
    """An upward arrow (shaft + head) from the dot's edge; rotation points it."""
    tip_y = -(_DOT_RADIUS + _ARROW_LENGTH)
    return QPolygonF(
        [
            # shaft (a thin rectangle)
            QPointF(-1.0, -_DOT_RADIUS),
            QPointF(1.0, -_DOT_RADIUS),
            QPointF(1.0, tip_y + _ARROW_HEAD),
            # head
            QPointF(_ARROW_HEAD, tip_y + _ARROW_HEAD),
            QPointF(0.0, tip_y),
            QPointF(-_ARROW_HEAD, tip_y + _ARROW_HEAD),
            QPointF(-1.0, tip_y + _ARROW_HEAD),
        ]
    )


class MouseLocation(QGraphicsTextItem):
    def __init__(self, **_):
        super().__init__()
        self.setZValue(100)

    def set_value(self, pos, scale, view):
        # pos = QGraphicsView.mapToScale return of mouse event pos()
        # view = QGraphicsView of the scene view
        x, y = to_eq_xy(pos.x(), pos.y())

        self.setHtml(
            f"<font color='white' size='{scaled_font_size(4)}'>{int(x)!s}, {int(y)!s}</font>"
        )

        # move hover to left if it goes out of view
        scene_rect = view.mapToScene(view.viewport().rect()).boundingRect()
        visible_x = -(scene_rect.x() + scene_rect.width())
        my_rect = self.mapRectToScene(self.boundingRect())
        if y + -(15 / scale + my_rect.width()) < visible_x:
            self.setPos(pos.x() - 15 / scale - my_rect.width(), pos.y())
        else:
            self.setPos(pos.x() + 15 / scale, pos.y())

        self.setScale(1 / scale)


class PointOfInterest:
    def __init__(self, **kwargs):
        super().__init__()
        self.location = MapPoint()
        self.__dict__.update(kwargs)
        self.text = QGraphicsTextItem()
        self._render()
        self.text.setZValue(2)
        self.text.setPos(self.location.x, self.location.y)

    def _render(self):
        self._rendered_pct = map_font_pct()
        self.text.setHtml(
            "<font color='{}' size='{}'>{}</font>".format(
                self.location.color.name(),
                scaled_font_size(1 + self.location.size),
                "\u272a" + self.location.text,
            )
        )

    def update_(self, scale):
        if self._rendered_pct != map_font_pct():
            self._render()
        self.text.setScale(scale)
        self.text.setPos(
            self.location.x - self.text.boundingRect().width() * 0.05 * scale,
            self.location.y - self.text.boundingRect().height() / 2 * scale,
        )


class Player(QGraphicsItemGroup):
    """A drawn player marker (EQTool style): colored circle + direction arrow.

    Local player is EQTool's green; other players keep colorhash colors (a
    deliberate divergence from EQTool's random-per-session RGB — stable
    colors across sessions).
    """

    def __init__(self, **kwargs):
        super().__init__()
        self.name = ""
        self.location = MapPoint()
        self.previous_location = None  # None until a second fix arrives
        self.timestamp = None  # datetime
        self.__dict__.update(kwargs)
        self.color = colorhash.ColorHash(self.name)
        if self.name == "__you__":
            marker_color = QColor(YOU_COLOR)
            self.setZValue(15)
        else:
            marker_color = QColor(self.color.hex)
            self.setZValue(10)
        fill = QColor(marker_color)
        fill.setAlpha(90)
        self.icon = QGraphicsEllipseItem(
            -_DOT_RADIUS, -_DOT_RADIUS, _DOT_RADIUS * 2, _DOT_RADIUS * 2
        )
        self.icon.setPen(QPen(marker_color, 2))
        self.icon.setBrush(QBrush(fill))
        self.directional = QGraphicsPolygonItem(_arrow_polygon())
        self.directional.setPen(QPen(marker_color, 1))
        self.directional.setBrush(QBrush(marker_color))
        self.directional.setVisible(False)
        self.nametag = QGraphicsTextItem()
        self.nametag.setPos(10, -15)
        self.addToGroup(self.icon)
        self.addToGroup(self.directional)
        self.addToGroup(self.nametag)
        self.z_level = 0

    def update_(self, scale):
        # (previous_location used to default to a truthy MapPoint(), making
        # the arrow point at (0,0) on the very first fix — hence the None
        # guard, and scale/pos applied unconditionally.)
        if self.previous_location is not None and (
            self.previous_location.x != self.location.x
            or self.previous_location.y != self.location.y
        ):
            self.directional.setRotation(
                get_degrees_from_line(
                    self.location.x,
                    self.location.y,
                    self.previous_location.x,
                    self.previous_location.y,
                )
            )
            self.directional.setVisible(True)
        self.setScale(scale)
        self.setPos(self.location.x, self.location.y)
        self.nametag.setHtml(
            "<font color='{}' size='{}'>{}</font>".format(
                self.color.hex if self.name != "__you__" else YOU_COLOR.name(),
                scaled_font_size(5),
                self.name if self.name != "__you__" else "You",
            )
        )


class SpawnPoint(QGraphicsItemGroup):
    def __init__(self, **kwargs):
        super().__init__()
        self.location = MapPoint()
        self.length = 10
        self.name = "pop"
        self.__dict__.update(**kwargs)
        self.setToolTip(self.name)

        pixmap = QGraphicsPixmapItem(QPixmap("data/maps/spawn.png"))
        text = QGraphicsTextItem("0")

        self.addToGroup(pixmap)
        self.addToGroup(text)
        self.setPos(self.location.x, self.location.y)

        self.setZValue(18)

        self.pixmap = pixmap
        self.text = text

        self.timer = QTimer()
        # The canvas persists running countdowns (double-click restarts happen
        # inside the item, out of the canvas's sight).
        self.on_state_change = None

    def _update(self):
        if self.timer:
            remaining = self._end_time - datetime.datetime.now()
            remaining_seconds = remaining.total_seconds()
            if remaining_seconds < 0:
                self.stop()
            elif remaining_seconds <= 30:
                self.text.setHtml(
                    f"<font color='red' size='{scaled_font_size(5)}'>"
                    f"{format_time(remaining)}</font>"
                )
            else:
                self.text.setHtml(f"<font color='white'>{format_time(remaining)}</font>")
            self.realign()

            if remaining_seconds > 0 and self.timer:
                self.timer.singleShot(1000, self._update)

    def realign(self, scale=None):
        if scale:
            self.setPos(
                self.location.x - self.boundingRect().width() / 2 * scale,
                self.location.y - self.boundingRect().height() / 2 * scale,
            )
        self.text.setPos(
            -self.text.boundingRect().width() / 2 + self.pixmap.boundingRect().width() / 2, 15
        )

    def start(self, _=None, timestamp=None):
        timestamp = timestamp if timestamp else datetime.datetime.now()
        self._end_time = timestamp + datetime.timedelta(seconds=self.length)
        if self.timer:
            self._update()
        if self.on_state_change:
            self.on_state_change()

    def stop(self):
        self.text.setHtml(f"<font color='green' align='center'>{self.name.upper()}</font>")

    def mouseDoubleClickEvent(self, _):
        self.start()


class MapPoint:
    def __init__(self, **kwargs):
        self.x = 0
        self.y = 0
        self.z = 0
        self.color = None  # QColor
        self.size = 0
        self.text = ""
        self.__dict__.update(kwargs)


class UserWaypoint(QGraphicsItemGroup):
    def __init__(self, name, icon, location):
        super().__init__()
        self.location = location
        self.name = name
        self.z_level = 0
        self.color = colorhash.ColorHash(self.name)

        self.pixmap = QGraphicsPixmapItem(QPixmap(icon))
        self.pixmap.setOffset(-10, -10)
        self.text = QGraphicsTextItem()
        self.text.setHtml(
            f"<font color='{self.color.hex}' size='{scaled_font_size(5)}'>{self.name}</font>"
        )
        self.text.setPos(10, -15)
        self.setToolTip(self.name)

        self.addToGroup(self.pixmap)
        self.addToGroup(self.text)
        self.setPos(self.location.x, self.location.y)

        self.setZValue(12)

    def update_(self, scale):
        self.setScale(scale)


class WayPoint:
    def __init__(self, **kwargs):
        super().__init__()
        self.location = MapPoint()
        self.__dict__.update(kwargs)

        self.pixmap = QGraphicsPixmapItem(QPixmap("data/maps/waypoint.png"))
        self.pixmap.setOffset(-10, -20)

        self.line = QGraphicsLineItem(0.0, 0.0, self.location.x, self.location.y)
        self.line.setPen(QPen(Qt.GlobalColor.green, 1, Qt.PenStyle.DashLine))
        self.line.setVisible(False)

        self.pixmap.setZValue(5)
        self.line.setZValue(4)

        self.pixmap.setPos(self.location.x, self.location.y)

    def update_(self, scale, location=None):
        self.pixmap.setScale(scale)
        if location:
            line = self.line.line()
            line.setP1(QPointF(location.x, location.y))
            self.line.setLine(line)

            pen = self.line.pen()
            pen.setWidth(int(1 / scale))
            self.line.setPen(pen)

            self.line.setVisible(True)


class MapLine:
    def __init__(self, **kwargs):
        self.x1 = 0
        self.x2 = 0
        self.y1 = 0
        self.y2 = 0
        self.z1 = 0
        self.color = None  # QColor
        self.__dict__.update(kwargs)


class MapGeometry:
    def __init__(self, **kwargs):
        self.lowest_x = 0
        self.highest_x = 0
        self.lowest_y = 0
        self.highest_y = 0
        self.highest_z = 0
        self.lowest_z = 0
        self.center_x = 0
        self.center_y = 0
        self.width = 0
        self.height = 0
        self.z_groups = []  # [(number:int, count:int)]
        self.__dict__.update(kwargs)
