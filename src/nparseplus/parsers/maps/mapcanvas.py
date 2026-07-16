# testing
import math
import os
import traceback
from datetime import datetime

import colorhash
import pathvalidate
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor, QPainter, QPen, QTransform
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsView,
    QInputDialog,
    QLineEdit,
    QMenu,
)

from nparseplus.helpers import config, text_time_to_seconds, to_range
from nparseplus.parsers.maps.mapclasses import (
    YOU_COLOR,
    MapPoint,
    MouseLocation,
    Player,
    PointOfInterest,
    SpawnPoint,
    UserWaypoint,
    WayPoint,
)
from nparseplus.parsers.maps.mapdata import ICON_MAP, MAP_FILES_PATHLIB, MapData
from nparseplus.parsers.maps.zfade import fade_opacity


class MapCanvas(QGraphicsView):
    """Map Widget for Everquest Map Files."""

    def __init__(self):

        self._data = None
        # UI Init
        super().__init__()
        self.setObjectName("MapCanvas")
        self.setAutoFillBackground(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setContentsMargins(0, 0, 0, 0)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._scene = QGraphicsScene()
        self.setScene(self._scene)
        self._scale = config.data["maps"]["scale"]
        self._mouse_location = MouseLocation()
        self._path_recording = False
        self._path_recording_name = ""
        self._path_file = None
        self._path_last_loc = None
        self._flash_item = None
        self._flash_ticks = 0
        self._flash_timer = QTimer(self)
        self._flash_timer.setInterval(100)
        self._flash_timer.timeout.connect(self._flash_pulse)
        self._tracking_circles = {}  # name -> QGraphicsEllipseItem (true radius)
        self.map_loaded_callback = None  # set by the Maps window

    def load_map(self, map_name, keep_loc=False):
        self.clear_flash()
        old_player_data = None
        try:
            try:
                old_player_data = self._data.players["__you__"]
            except:
                pass  # no old location for player
            map_data = MapData(str(map_name))

        except:
            traceback.print_exc()

        else:
            self._data = map_data
            self._scene.clear()
            self._tracking_circles = {}  # items died with the scene
            self._z_index = 0
            self._pen_state = None  # force pen-width refresh for the new map
            self._draw()
            rect = self._scene.sceneRect()
            rect.adjust(
                -self._data.geometry.width * 2,
                -self._data.geometry.height * 2,
                self._data.geometry.width * 2,
                self._data.geometry.height * 2,
            )
            self.setSceneRect(rect)
            self.update()
            self.update_()

            self.centerOn(self._data.geometry.center_x, self._data.geometry.center_y)
            self._mouse_location = MouseLocation()
            self._scene.addItem(self._mouse_location)
            config.data["maps"]["last_zone"] = self._data.zone
            config.save()
            if keep_loc and old_player_data:
                self.add_player("__you__", old_player_data.timestamp, old_player_data.location)
            if self.map_loaded_callback:
                self.map_loaded_callback()

    def _draw(self):
        for z in self._data.keys():
            self._scene.addItem(self._data[z]["paths"])
            for p in self._data[z]["poi"]:
                self._scene.addItem(p.text)

        self._scene.addItem(self._data.grid)

    def update_(self, ratio=None):
        if not ratio:
            ratio = self._scale

        current_alpha = config.data["maps"]["current_z_alpha"] / 100
        other_alpha = config.data["maps"]["other_z_alpha"] / 100
        closest_alpha = config.data["maps"]["closest_z_alpha"] / 100

        # scene
        self.setTransform(QTransform())  # reset transform object
        self._scale = to_range(ratio, 0.0006, 5.0)
        config.data["maps"]["scale"] = self._scale
        self.scale(self._scale, self._scale)

        # lines and points of interest
        use_z_layers = config.data["maps"]["use_z_layers"]
        current_z_level = self._data.geometry.z_groups[self._z_index]
        closest_z_levels = set()
        for x in [i for i in [self._z_index - 1, self._z_index + 1] if i > -1]:
            try:
                closest_z_levels.add(self._data.geometry.z_groups[x])
            except:
                pass

        # Smooth EQTool-style z fading (default when z layers are off):
        # fade each z-band by its distance from the player's z.
        you = self._data.players.get("__you__", None)
        smooth_fade = (
            not use_z_layers
            and not self._data.show_all_map_levels
            and bool(self._data.zone_level_height)
            and you is not None
        )
        player_z = you.location.z if you else None

        # Per-line pen updates are only needed when the scale or layer mode
        # changes — never on a plain player-location update (bands only).
        pen_state = (self._scale, use_z_layers, current_z_level, config.data["maps"]["line_width"])
        update_pens = getattr(self, "_pen_state", None) != pen_state
        self._pen_state = pen_state

        for band_key in self._data.keys():
            band = self._data[band_key]
            z_group = band["z_group"]
            if use_z_layers:
                if z_group == current_z_level:
                    alpha = current_alpha
                elif z_group in closest_z_levels:
                    alpha = closest_alpha
                else:
                    alpha = other_alpha
            elif smooth_fade:
                alpha = fade_opacity(abs(band["center_z"] - player_z), self._data.zone_level_height)
            else:
                alpha = 1.0
            # lines
            bolded = 0.5 if use_z_layers else 0.0
            if update_pens:
                for path in band["paths"].childItems():
                    if z_group == current_z_level or not use_z_layers:
                        pen = path.pen()
                        pen.setWidth(
                            int(
                                max(
                                    config.data["maps"]["line_width"] + bolded,
                                    (config.data["maps"]["line_width"] + bolded) / self._scale,
                                )
                            )
                        )
                        path.setPen(pen)
                    else:
                        pen = path.pen()
                        pen.setWidth(
                            int(
                                max(
                                    config.data["maps"]["line_width"] - 0.8,
                                    (config.data["maps"]["line_width"] - 0.8) / self._scale,
                                )
                            )
                        )
                        path.setPen(pen)

            band["paths"].setOpacity(alpha)

            # points of interest (per-item opacity by their own z)
            for p in band["poi"]:
                p.update_(min(5, self.to_scale()))
                if not config.data["maps"]["show_poi"]:
                    p.text.setOpacity(0)
                elif use_z_layers:
                    if z_group == current_z_level:
                        p.text.setOpacity(current_alpha)
                    else:
                        p.text.setOpacity(other_alpha)
                elif smooth_fade:
                    p.text.setOpacity(
                        fade_opacity(abs(p.location.z - player_z), self._data.zone_level_height)
                    )
                else:
                    p.text.setOpacity(1.0)

        # players (always fully opaque unless z layers are on)
        for player in self._data.players.values():
            player.update_(self.to_scale())
            if use_z_layers:
                if player.z_level == current_z_level:
                    player.setOpacity(current_alpha)
                else:
                    player.setOpacity(other_alpha)
            else:
                player.setOpacity(1.0)

        # waypoint
        if self._data.way_point:
            self._data.way_point.update_(self.to_scale())
            if use_z_layers:
                self._data.way_point.pixmap.setOpacity(
                    current_alpha
                    if (self._data.way_point.location.z == current_z_level)
                    else other_alpha
                )
                player = self._data.players.get("__you__", None)
                if player and current_z_level in [self._data.way_point.location.z, player.z_level]:
                    self._data.way_point.line.setOpacity(current_alpha)
                else:
                    self._data.way_point.line.setOpacity(other_alpha)

            else:
                self._data.way_point.pixmap.setOpacity(1.0)
                self._data.way_point.line.setOpacity(1.0)

        # user waypoints
        for waypoint in self._data.waypoints.values():
            waypoint.update_(self.to_scale())
            if use_z_layers:
                if waypoint.z_level == current_z_level:
                    waypoint.setOpacity(current_alpha)
                else:
                    waypoint.setOpacity(other_alpha)
            else:
                waypoint.setOpacity(1.0)

        # spawns
        for spawn in self._data.spawns:
            spawn.setScale(self.to_scale())
            spawn.realign(self.to_scale())
            if use_z_layers:
                spawn.setOpacity(
                    current_alpha if (spawn.location.z == current_z_level) else other_alpha
                )
            else:
                spawn.setOpacity(1.0)

        # grid lines
        if config.data["maps"]["show_grid"]:
            pen = self._data.grid.pen()
            pen.setWidth(
                int(
                    max(
                        config.data["maps"]["grid_line_width"],
                        self.to_scale(config.data["maps"]["grid_line_width"]),
                    )
                )
            )
            self._data.grid.setPen(pen)
            self._data.grid.setVisible(True)
        else:
            self._data.grid.setVisible(False)

    def to_scale(self, float_value=1.0):
        return float_value / self._scale

    def center(self):
        player = None
        if self._data:
            player = self._data.players.get("__you__", None)
        if config.data["maps"]["auto_follow"] and player:
            self.centerOn(player.location.x, player.location.y)

    def remove_player(self, name):
        player = self._data.players.pop(name, None)
        if player:
            self._scene.removeItem(player)
        self._remove_tracking_circle(name)

    def _remove_tracking_circle(self, name):
        circle = self._tracking_circles.pop(name, None)
        if circle:
            self._scene.removeItem(circle)

    def _update_tracking_circle(self, name, location, tracking_distance):
        """The tracking-skill radius (EQTool TrackingEllipse): a TRUE-radius
        circle in scene units — it scales with the map, unlike the player
        marker group which is counter-scaled to constant pixel size."""
        if tracking_distance is None or tracking_distance <= 0:
            self._remove_tracking_circle(name)
            return
        radius = float(tracking_distance)
        circle = self._tracking_circles.get(name)
        if circle is None:
            color = (
                QColor(YOU_COLOR) if name == "__you__" else QColor(colorhash.ColorHash(name).hex)
            )
            pen = QPen(color, 1)
            pen.setCosmetic(True)  # constant stroke width at any zoom
            fill = QColor(color)
            fill.setAlpha(5 if name == "__you__" else 3)  # EQTool alphas
            circle = QGraphicsEllipseItem(-radius, -radius, radius * 2, radius * 2)
            circle.setPen(pen)
            circle.setBrush(fill)
            circle.setZValue(9)  # just under the player markers
            self._scene.addItem(circle)
            self._tracking_circles[name] = circle
        else:
            circle.setRect(-radius, -radius, radius * 2, radius * 2)
        circle.setPos(location.x, location.y)

    def expire_players(self, max_age_s=60.0):
        """Drop remote dots not refreshed within max_age_s (EQTool: 1 minute).

        "__you__" never expires; remote dots are stamped with datetime.now()
        on arrival (see Maps.handle_remote_event).
        """
        if not self._data:
            return
        now = datetime.now()
        stale = [
            name
            for name, player in self._data.players.items()
            if name != "__you__"
            and isinstance(player.timestamp, datetime)
            and (now - player.timestamp).total_seconds() > max_age_s
        ]
        for name in stale:
            self.remove_player(name)
        if stale:
            self.update_()

    def add_player(self, name, timestamp, location, tracking_distance=None):
        if name not in self._data.players:
            self._data.players[name] = Player(name=name, location=location, timestamp=timestamp)
            self._scene.addItem(self._data.players[name])
        else:
            self._data.players[name].previous_location = self._data.players[name].location
            self._data.players[name].location = location
            self._data.players[name].timestamp = timestamp
        self._update_tracking_circle(name, location, tracking_distance)
        self._data.players[name].z_level = self._data.get_closest_z_group(
            self._data.players[name].location.z
        )

        if name == "__you__" and config.data["maps"]["use_z_layers"]:
            self._z_index = self._data.geometry.z_groups.index(
                self._data.get_closest_z_group(self._data.players["__you__"].location.z)
            )

        self.update_()

        if self._data.way_point and name == "__you__":
            self._data.way_point.update_(self.to_scale(), location=location)

        if name == "__you__" and config.data["maps"]["auto_follow"]:
            self.center()

    def remove_waypoint(self, name):
        waypoint = self._data.waypoints.pop(name)
        if waypoint:
            self._scene.removeItem(waypoint)

    def add_waypoint(self, name, location, icon):
        if name not in self._data.waypoints:
            self._data.waypoints[name] = UserWaypoint(
                name=name.rsplit(":", 1)[0],
                icon=ICON_MAP.get(icon, "data/maps/waypoint.png"),
                location=location,
            )
            self._scene.addItem(self._data.waypoints[name])

        self._data.waypoints[name].z_level = self._data.get_closest_z_group(
            self._data.waypoints[name].location.z
        )

        self.update_()

    def enterEvent(self, event):
        if config.data["maps"]["show_mouse_location"]:
            self._mouse_location.setVisible(True)
        QGraphicsView.enterEvent(self, event)

    def leaveEvent(self, event):
        self._mouse_location.setVisible(False)
        QGraphicsView.leaveEvent(self, event)

    def mouseMoveEvent(self, event):
        self._mouse_location.set_value(self.mapToScene(event.pos()), self._scale, self)
        QGraphicsView.mouseMoveEvent(self, event)

    def wheelEvent(self, event):
        # Scale based on scroll wheel direction
        movement = event.angleDelta().y()
        if self.dragMode() == QGraphicsView.DragMode.NoDrag:
            if movement > 0:
                self.update_(self._scale + self._scale * 0.1)
            else:
                self.update_(self._scale - self._scale * 0.1)
        else:
            if self._data:
                if movement > 0:
                    self._z_index = max(self._z_index - 1, 0)
                else:
                    self._z_index = min(self._z_index + 1, len(self._data.geometry.z_groups) - 1)
                self.update_()

        # Update Mouse Location
        mouse_pos = int(event.position().x()), int(event.position().y())
        self._mouse_location.set_value(self.mapToScene(*mouse_pos), self._scale, self)

    def keyPressEvent(self, event):
        # Enable drag mode while control button is being held down
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        QGraphicsView.keyPressEvent(self, event)

    def keyReleaseEvent(self, event):
        # Disable drag mode when control button released
        if event.key() == Qt.Key.Key_Control:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
        QGraphicsView.keyPressEvent(self, event)

    def resizeEvent(self, event):
        self.center()
        QGraphicsView.resizeEvent(self, event)

    def contextMenuEvent(self, event):
        # create menu
        pos = self.mapToScene(event.pos())
        menu = QMenu(self)
        # remove from memory after usage
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)  # remove from memory
        spawn_point_menu = menu.addMenu("Spawn Point")
        spawn_point_create = spawn_point_menu.addAction("Create on Cursor")
        spawn_point_delete = spawn_point_menu.addAction("Delete on Cursor")
        spawn_point_delete_all = spawn_point_menu.addAction("Delete All")
        way_point_menu = menu.addMenu("Way Point")
        way_point_create = way_point_menu.addAction("Create on Cursor")
        way_point_delete = way_point_menu.addAction("Clear")
        pathing_menu = menu.addMenu("Custom Pathing")
        pathing_start_recording = QAction("Start Recording")
        pathing_rename_recording = QAction("Rename Path")
        pathing_stop_recording = QAction("Stop Recording")
        if not self._path_recording:
            pathing_menu.addAction(pathing_start_recording)
        else:
            current = pathing_menu.addAction(self._path_recording_name)
            current.setEnabled(False)
            pathing_menu.addSeparator()
            pathing_menu.addAction(pathing_rename_recording)
            pathing_menu.addAction(pathing_stop_recording)
        load_map = menu.addAction("Load Map")

        # execute
        action = menu.exec(self.mapToGlobal(event.pos()))

        # parse response

        if action == spawn_point_create:
            dialog = QInputDialog(self)
            dialog.setWindowTitle("Create Spawn Point")
            dialog.setLabelText("Respawn Time (hh:mm:ss):")
            dialog.setTextValue(self._data.get_default_spawn_timer())

            if dialog.exec():
                spawn_time = text_time_to_seconds(dialog.textValue())
                spawn = SpawnPoint(
                    location=MapPoint(
                        x=pos.x(), y=pos.y(), z=self._data.geometry.z_groups[self._z_index]
                    ),
                    length=spawn_time,
                )

                self._scene.addItem(spawn)
                self._data.spawns.append(spawn)
                spawn.start()
            dialog.deleteLater()

        if action == spawn_point_delete:
            pixmap = self._scene.itemAt(pos.x(), pos.y(), QTransform())
            if pixmap:
                group = pixmap.parentItem()
                if group:
                    self._data.spawns.remove(group)
                    self._scene.removeItem(group)

        if action == spawn_point_delete_all:
            for spawn in self._data.spawns:
                self._scene.removeItem(spawn)
            self._data.spawns = []

        if action == way_point_create:
            if self._data.way_point:
                self._scene.removeItem(self._data.way_point.pixmap)
                self._scene.removeItem(self._data.way_point.line)
                self._data.way_point = None

            self._data.way_point = WayPoint(
                location=MapPoint(
                    x=pos.x(), y=pos.y(), z=self._data.geometry.z_groups[self._z_index]
                )
            )

            self._scene.addItem(self._data.way_point.pixmap)
            self._scene.addItem(self._data.way_point.line)

        if action == way_point_delete:
            if self._data.way_point:
                self._scene.removeItem(self._data.way_point.pixmap)
                self._scene.removeItem(self._data.way_point.line)
            self._data.way_point = None

        if action == pathing_start_recording:
            self.start_path_recording()

        if action == pathing_rename_recording:
            self.rename_path_recording()

        if action == pathing_stop_recording:
            self.stop_path_recording()

        if action == load_map:
            dialog = QInputDialog(self)
            dialog.setStyleSheet("QFrame { background-color: #f0f0f0 }")
            dialog.setWindowTitle("Load Map")
            dialog.setLabelText("Select map to load:")
            dialog.setComboBoxItems(sorted([map.title() for map in MapData.get_zone_dict()]))
            if dialog.exec():
                self.load_map(dialog.textValue().lower())
            dialog.deleteLater()

        self.update_()

    def _get_path_filename(self, custom_name=None, relative=False):
        custom_name = custom_name or self._path_recording_name
        clean_name = pathvalidate.sanitize_filename(custom_name)
        clean_name = clean_name.replace(" ", "_")
        if relative:
            return clean_name
        zone_key = MapData.get_zone_dict().get(self._data.zone.strip().lower())
        filename = f"{zone_key}_{clean_name}.txt"

        # Make sure the directory exists
        record_dir = MAP_FILES_PATHLIB.joinpath("recordings")
        if not os.path.exists(record_dir):
            try:
                print("Creating custom map directory.")
                os.makedirs(record_dir)
            except Exception as e:
                print("Failed to make custom map directory: %s" % e)
        return record_dir.joinpath(filename)

    def start_path_recording(self, name=None):
        print("Start recording!")
        if self._path_recording:
            return

        if name:
            path_name = name
            ok_pressed = True
        else:
            path_name, ok_pressed = QInputDialog.getText(
                self,  # parent
                "Start Recording Path",  # title
                "Name of path to record:",  # label
                echo=QLineEdit.EchoMode.Normal,
                text="",
            )
        if ok_pressed:
            self._path_recording_name = path_name
            try:
                self._path_file = open(self._get_path_filename(), "a")
                self._path_recording = True
                self._path_last_loc = None
            except Exception as e:
                print("Failed to open pathfile: %s" % e)

    def rename_path_recording(self, new_name=None):
        print("Rename recording!")
        if not self._path_recording:
            return

        if new_name:
            path_name = new_name
            ok_pressed = True
        else:
            path_name, ok_pressed = QInputDialog.getText(
                self,  # parent
                "Rename Path",  # title
                "New path name:",  # label
                echo=QLineEdit.EchoMode.Normal,
                text=self._path_recording_name,
            )

        if ok_pressed:
            old_path_name = self._path_recording_name
            new_path_name = path_name
            try:
                self._path_file.close()
                self._path_file = None
            except Exception as e:
                print("Failed to close path recording file: %s" % e)
                return
            try:
                os.rename(
                    self._get_path_filename(custom_name=old_path_name),
                    self._get_path_filename(custom_name=new_path_name),
                )
                self._path_recording_name = new_path_name
            except Exception as e:
                print("Failed to rename path recording file: %s" % e)
                self._path_recording = False
                return
            try:
                self._path_file = open(self._get_path_filename(), "a")
            except Exception as e:
                print("Failed to open renamed path recording file: %s" % e)
                self._path_recording = False
                return

    def stop_path_recording(self):
        print("Stop recording!")
        if not self._path_recording:
            return

        if self._path_last_loc is not None:
            print("Recording final path point.")
            self.record_path_point(self._path_last_loc, "%s (end)" % self._path_recording_name)

        try:
            self._path_file.close()
        except Exception as e:
            print("Failed to stop recording: %s" % e)
            return
        self._path_file = None
        self._path_recording = False
        self._path_last_loc = None

    def record_path_loc(self, loc):
        if not self._path_recording:
            return

        print("Recording loc: %s" % str(loc))
        if self._path_last_loc is None:
            print("Recording first path point.")
            self.record_path_point(loc, "%s (start)" % self._path_recording_name)
        else:
            line = f"L {self._path_last_loc[0]}, {self._path_last_loc[1]}, {self._path_last_loc[2]}, {loc[0]}, {loc[1]}, {loc[2]}, {255}, {0}, {0}\n"
            try:
                self._path_file.write(line)
                self._path_file.flush()
            except Exception as e:
                print("Failed to write loc to pathfile: %s" % e)

            # Also add line to the active map
            band = self._ensure_band_in_scene(loc[2])
            color = MapData.color_transform(QColor(255, 0, 0))
            map_line = QGraphicsPathItem()
            map_line.setPen(QPen(color, config.data["maps"]["line_width"]))
            map_path = map_line.path()
            map_path.moveTo(self._path_last_loc[0], self._path_last_loc[1])
            map_path.lineTo(loc[0], loc[1])
            map_line.setPath(map_path)
            band["paths"].addToGroup(map_line)
            self._pen_state = None  # new line item needs a pen refresh
            self.update_()

        # Update past loc to current loc
        self._path_last_loc = loc

    def record_path_point(self, loc, desc):
        if not self._path_recording:
            return
        point = f"P {loc[0]}, {loc[1]}, {loc[2]}, {255}, {0}, {0}, {3}, {desc}\n"
        try:
            self._path_file.write(point)
            self._path_file.flush()
        except Exception as e:
            print("Failed to write point to pathfile: %s" % e)

        # Also add point to the active map
        band = self._ensure_band_in_scene(loc[2])
        color = MapData.color_transform(QColor(255, 0, 0))
        map_poi = MapPoint(x=loc[0], y=loc[1], z=loc[2], color=color, size=3, text=desc)
        poi = PointOfInterest(location=map_poi)
        band["poi"].append(poi)
        self._scene.addItem(poi.text)
        self.update_()

    def _ensure_band_in_scene(self, z):
        """Band entry for z; adds a newly created band's group to the scene."""
        band_key = self._data.band_key_for_z(z)
        created = band_key not in self._data
        band = self._data.ensure_band(z)
        if created:
            self._scene.addItem(band["paths"])
        return band

    # NPC finder support ---------------------------------------------------

    def flash_location(self, x, y):
        """Center on a point and flash-highlight it for ~3 seconds."""
        self.clear_flash()
        self.centerOn(x, y)
        radius = 15.0 * self.to_scale()
        item = QGraphicsEllipseItem(-radius, -radius, radius * 2, radius * 2)
        pen = QPen(QColor(255, 215, 0), 3)
        pen.setCosmetic(True)
        item.setPen(pen)
        item.setBrush(Qt.BrushStyle.NoBrush)
        item.setPos(x, y)
        item.setZValue(30)
        item.setOpacity(1.0)  # always fully opaque
        self._scene.addItem(item)
        self._flash_item = item
        self._flash_ticks = 0
        self._flash_timer.start()

    def clear_flash(self):
        self._flash_timer.stop()
        if self._flash_item is not None:
            try:
                self._scene.removeItem(self._flash_item)
            except RuntimeError:
                pass  # scene already cleared the item
            self._flash_item = None

    def _flash_pulse(self):
        self._flash_ticks += 1
        if self._flash_ticks >= 30 or self._flash_item is None:
            self.clear_flash()
            return
        try:
            self._flash_item.setScale(1.0 + 0.4 * abs(math.sin(self._flash_ticks * 0.45)))
        except RuntimeError:
            self.clear_flash()
