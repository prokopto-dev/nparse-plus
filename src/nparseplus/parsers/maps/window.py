"""Map parser for nparse."""

import re
from datetime import datetime

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
)

from nparseplus.core.events import (
    OtherPlayerLocationReceivedRemoteEvent,
    PlayerDisconnectReceivedRemoteEvent,
)
from nparseplus.core.npc_search import NpcSearchIndex, normalize_name, search_all_zones
from nparseplus.core.zones import load_zone_database
from nparseplus.helpers import config, to_real_xy
from nparseplus.helpers.parser import ParserWindow
from nparseplus.parsers.maps.mapcanvas import MapCanvas
from nparseplus.parsers.maps.mapclasses import MapPoint
from nparseplus.parsers.maps.mapdata import MapData

ZONE_MATCHER = re.compile(r"There (is|are) \d+ players? in (?P<zone>.+)\.")

SEARCH_BOX_STYLE = (
    "QLineEdit { background-color: #050505; color: white; border: none;"
    " border-radius: 3px; padding: 2px 4px; font-size: 12px; }"
)
SEARCH_RESULTS_STYLE = (
    "QListWidget { background-color: black; color: rgb(200, 200, 200);"
    " border: 1px solid #333; font-size: 12px; }"
    " QListWidget::item { padding: 2px; }"
    " QListWidget::item:selected, QListWidget::item:hover { background: darkgreen;"
    " color: white; }"
)


def format_respawn(seconds):
    """Respawn seconds -> mm:ss (or h:mm:ss for long spawns)."""
    if seconds is None:
        return "?"
    seconds = int(seconds)
    if seconds >= 3600:
        return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60}:{seconds % 60:02d}"


class MapsSignals(QObject):
    zoning = Signal()
    new_zone = Signal(str)
    location = Signal(str, str)
    death = Signal(str, str)
    start_recording = Signal(str)
    rename_recording = Signal(str)
    stop_recording = Signal()


class WikiSearchBridge(QObject):
    """Marshals P99-wiki worker-thread results onto the GUI thread."""

    results_ready = Signal(str, list)  # (query, list[WikiNpc])


class Maps(ParserWindow):
    def __init__(self):
        self.name = "maps"
        super().__init__()
        # interface
        self._map = MapCanvas()
        self._map.map_loaded_callback = self._rebuild_search_index
        self.content.addWidget(self._map, 1)
        # NPC finder state
        try:
            self._zone_db = load_zone_database()
        except Exception:
            self._zone_db = None
        self._search_index = None
        self._transient_timer = QTimer(self)
        self._transient_timer.setSingleShot(True)
        self._transient_timer.timeout.connect(self._hide_search_results)
        # buttons
        button_layout = QHBoxLayout()
        # NPC/label search box
        self._search_box = QLineEdit()
        self._search_box.setObjectName("MapSearchBox")
        self._search_box.setPlaceholderText("Find NPC/label…")
        self._search_box.setFixedWidth(130)
        self._search_box.setStyleSheet(SEARCH_BOX_STYLE)
        self._search_box.textChanged.connect(self._search_text_changed)
        button_layout.addWidget(self._search_box)
        # notable NPCs quick list
        self._npc_button = QPushButton("☰ NPCs")
        self._npc_button.setCheckable(True)
        self._npc_button.setToolTip("Notable NPCs in this zone")
        self._npc_button.setStyleSheet("QPushButton { min-width: 50px; }")
        self._npc_button.clicked.connect(self._toggle_npc_list)
        button_layout.addWidget(self._npc_button)
        # results dropdown (child overlay, styled like the dark menu)
        self._search_results = QListWidget(self)
        self._search_results.setObjectName("MapSearchResults")
        self._search_results.setStyleSheet(SEARCH_RESULTS_STYLE)
        self._search_results.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._search_results.itemClicked.connect(self._search_hit_selected)
        self._search_results.hide()
        # P99 wiki lookup (lazy client; results marshalled back via signal).
        self._wiki_client = None
        self._wiki_bridge = WikiSearchBridge()
        self._wiki_bridge.results_ready.connect(self._wiki_results_ready)
        show_poi = QPushButton("\u272a")
        show_poi.setCheckable(True)
        show_poi.setChecked(config.data["maps"]["show_poi"])
        show_poi.setToolTip("Show Points of Interest")
        show_poi.clicked.connect(self._toggle_show_poi)
        button_layout.addWidget(show_poi)
        auto_follow = QPushButton("\u25ce")
        auto_follow.setCheckable(True)
        auto_follow.setChecked(config.data["maps"]["auto_follow"])
        auto_follow.setToolTip("Auto Center")
        auto_follow.clicked.connect(self._toggle_auto_follow)
        button_layout.addWidget(auto_follow)
        toggle_z_layers = QPushButton("\u24cf")
        toggle_z_layers.setCheckable(True)
        toggle_z_layers.setChecked(config.data["maps"]["use_z_layers"])
        toggle_z_layers.setToolTip("Show Z Layers")
        toggle_z_layers.clicked.connect(self._toggle_z_layers)
        button_layout.addWidget(toggle_z_layers)
        show_grid_lines = QPushButton("#")
        show_grid_lines.setCheckable(True)
        show_grid_lines.setChecked(config.data["maps"]["show_grid"])
        show_grid_lines.setToolTip("Show Grid")
        show_grid_lines.clicked.connect(self._toggle_show_grid)
        button_layout.addWidget(show_grid_lines)
        show_mouse_location = QPushButton("\U0001f6c8")
        show_mouse_location.setCheckable(True)
        show_mouse_location.setChecked(config.data["maps"]["show_mouse_location"])
        show_mouse_location.setToolTip("Show Loc Under Mouse Pointer")
        show_mouse_location.clicked.connect(self._toggle_show_mouse_location)
        button_layout.addWidget(show_mouse_location)

        self.menu_area.addLayout(button_layout)

        if config.data["maps"]["last_zone"]:
            self._map.load_map(config.data["maps"]["last_zone"])
        else:
            self._map.load_map("west freeport")

        # Remote (shared) player dots go stale after 1 minute without a
        # refresh — EQTool sweeps every second (MappingWindow UITimer).
        self._remote_expiry_timer = QTimer(self)
        self._remote_expiry_timer.setInterval(1000)
        self._remote_expiry_timer.timeout.connect(self._map.expire_players)
        self._remote_expiry_timer.start()

    def handle_remote_event(self, event):
        """Shared-player events from the backend bus (queued Qt bridge).

        Wire coordinates are in the raw ``/loc`` print order (see
        core.events.RemotePlayer); map scene space is ``(-second, -first)``
        of that order — the same transform ``to_real_xy`` applies to the
        local ``Your Location is`` line above.
        """
        if isinstance(event, OtherPlayerLocationReceivedRemoteEvent):
            remote = event.player
            zone_key = self._map._data.short_zone_key if self._map._data else None
            if remote.zone and zone_key and remote.zone != zone_key:
                return  # another zone (nparse-mode state spans zones)
            point = MapPoint(x=-remote.y, y=-remote.x, z=remote.z)
            self._map.add_player(remote.name, datetime.now(), point)
        elif isinstance(event, PlayerDisconnectReceivedRemoteEvent):
            self._map.remove_player(event.player.name)

    def parse(self, timestamp, text):
        if text[:23] == "LOADING, PLEASE WAIT...":
            QApplication.instance()._signals["maps"].zoning.emit()
        elif text[:16] == "You have entered":
            QApplication.instance()._signals["maps"].new_zone.emit(text[17:-1])
            self._map.load_map(text[17:-1])
        elif ZONE_MATCHER.match(text):
            new_zone = ZONE_MATCHER.match(text).groupdict()["zone"].lower()
            new_zone = MapData.translate_who_zone(new_zone)
            if new_zone not in (self._map._data.zone.lower(), "everquest"):
                QApplication.instance()._signals["maps"].new_zone.emit(new_zone)
                self._map.load_map(new_zone, keep_loc=True)
        elif text[:16] == "Your Location is":
            QApplication.instance()._signals["maps"].location.emit(timestamp.isoformat(), text[17:])
            x, y, z = [float(value) for value in text[17:].strip().split(",")]
            x, y = to_real_xy(x, y)
            self._map.add_player("__you__", timestamp, MapPoint(x=x, y=y, z=z))
            self._map.record_path_loc((x, y, z))
        elif text[:16] == "start_recording_":
            QApplication.instance()._signals["maps"].start_recording.emit(text.split()[0][16:])
            recording_name = text.split()[0][16:]
            if recording_name:
                recording_name = recording_name.replace("_", " ")
                self._map.start_path_recording(recording_name)
        elif text[:17] == "rename_recording_":
            QApplication.instance()._signals["maps"].rename_recording.emit(text.split()[0][17:])
            recording_name = text.split()[0][17:]
            if recording_name:
                recording_name = recording_name.replace("_", " ")
                self._map.rename_path_recording(new_name=recording_name)
        elif text[:14] == "stop_recording":
            QApplication.instance()._signals["maps"].stop_recording.emit()
            self._map.stop_path_recording()
        elif text[:19] == "You have been slain":
            QApplication.instance()._signals["maps"].death.emit(timestamp.isoformat(), text)

    # events
    def _toggle_show_poi(self, _):
        config.data["maps"]["show_poi"] = not config.data["maps"]["show_poi"]
        config.save()
        self._map.update_()

    def _toggle_auto_follow(self, _):
        config.data["maps"]["auto_follow"] = not config.data["maps"]["auto_follow"]
        config.save()
        self._map.center()

    def _toggle_z_layers(self, _):
        config.data["maps"]["use_z_layers"] = not config.data["maps"]["use_z_layers"]
        config.save()
        self._map.update_()

    def _toggle_show_grid(self, _):
        config.data["maps"]["show_grid"] = not config.data["maps"]["show_grid"]
        config.save()
        self._map.update_()

    def _toggle_show_mouse_location(
        self,
    ):
        config.data["maps"]["show_mouse_location"] = not config.data["maps"]["show_mouse_location"]
        config.save()

    # NPC finder -----------------------------------------------------------

    def _rebuild_search_index(self):
        self._hide_search_results()
        map_data = self._map._data
        if map_data is None:
            self._search_index = None
            return
        self._search_index = NpcSearchIndex(
            zone_key=map_data.short_zone_key,
            labels=map_data.poi_entries(),
            zones=self._zone_db,
        )

    def _search_text_changed(self, text):
        if self._npc_button.isChecked():
            self._npc_button.setChecked(False)
        query = text.strip()
        if len(query) < 2:
            self._hide_search_results()
            return
        hits = self._search_index.search(query) if self._search_index else []
        current_zone = self._search_index.zone_key if self._search_index else None
        cross = []
        if self._zone_db is not None:
            seen = {normalize_name(hit.name) for hit in hits}
            cross = [
                hit
                for hit in search_all_zones(query, self._zone_db)
                if hit.zone_key != current_zone and normalize_name(hit.name) not in seen
            ][:15]
        self._search_results.clear()
        for hit in hits:
            if hit.location is not None:
                label = f"✪ {hit.name} — {format_respawn(hit.respawn_seconds)}"
            else:
                label = f"{hit.name} — {format_respawn(hit.respawn_seconds)} (no location)"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, hit)
            self._search_results.addItem(item)
        for hit in cross:
            item = QListWidgetItem(f"{hit.name} — elsewhere: {hit.zone_display}")
            item.setData(Qt.ItemDataRole.UserRole, hit)
            self._search_results.addItem(item)
        wiki_row = QListWidgetItem(f"🌐 Search P99 wiki for '{query}'…")
        wiki_row.setData(Qt.ItemDataRole.UserRole, ("wiki-search", query))
        self._search_results.addItem(wiki_row)
        self._show_search_results()

    def _start_wiki_search(self, query):
        import threading

        if self._wiki_client is None:
            from nparseplus.net.p99wiki import P99WikiClient

            self._wiki_client = P99WikiClient(zones=self._zone_db)
        self._show_transient(f"Searching P99 wiki for '{query}'…")
        client, bridge = self._wiki_client, self._wiki_bridge

        def work():
            bridge.results_ready.emit(query, client.find_npcs(query))

        threading.Thread(target=work, name="wiki-search", daemon=True).start()

    def _wiki_results_ready(self, query, npcs):
        if self._search_box.text().strip() != query:
            return  # user typed something else meanwhile
        if not npcs:
            self._show_transient(f"No wiki results for '{query}'")
            return
        self._search_results.clear()
        current_zone = self._search_index.zone_key if self._search_index else None
        for npc in npcs:
            here = " (here)" if npc.zone_short and npc.zone_short == current_zone else ""
            level = f" — lvl {npc.level}" if npc.level else ""
            item = QListWidgetItem(f"wiki: {npc.name} — {npc.zone_display or '?'}{here}{level}")
            item.setData(Qt.ItemDataRole.UserRole, npc)
            self._search_results.addItem(item)
        self._show_search_results()

    def _wiki_hit_selected(self, npc):
        current_zone = self._search_index.zone_key if self._search_index else None
        if npc.map_location is not None and npc.zone_short == current_zone:
            self._map.flash_location(*npc.map_location)
            self._hide_search_results()
            return
        where = npc.zone_display or "unknown zone"
        loc = f" at loc ({npc.location[0]:g}, {npc.location[1]:g})" if npc.location else ""
        self._show_transient(f"{npc.name} — in {where}{loc}")

    def _search_hit_selected(self, item):
        hit = item.data(Qt.ItemDataRole.UserRole)
        if hit is None:
            return  # transient message row
        if isinstance(hit, tuple) and hit[0] == "wiki-search":
            self._start_wiki_search(hit[1])
            return
        if not hasattr(hit, "kind"):  # WikiNpc result
            self._wiki_hit_selected(hit)
            return
        if hit.kind == "zone-notable":
            self._show_transient(
                f"{hit.name} — in {hit.zone_display} — respawn "
                f"{format_respawn(hit.respawn_seconds)}"
            )
        elif hit.location is not None:
            self._map.flash_location(hit.location[0], hit.location[1])
            self._hide_search_results()
        else:
            self._show_transient(
                f"{hit.name} — no location known — respawn {format_respawn(hit.respawn_seconds)}"
            )

    def _toggle_npc_list(self, checked):
        if not checked:
            self._hide_search_results()
            return
        notables = self._search_index.notables() if self._search_index else []
        if not notables:
            self._show_transient("No notable NPCs for this zone")
            return
        self._search_results.clear()
        for hit in notables:
            item = QListWidgetItem(f"{hit.name} — {format_respawn(hit.respawn_seconds)}")
            item.setData(Qt.ItemDataRole.UserRole, hit)
            self._search_results.addItem(item)
        self._show_search_results()

    def _show_transient(self, message):
        self._search_results.clear()
        item = QListWidgetItem(message)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        self._search_results.addItem(item)
        self._show_search_results()
        self._transient_timer.start(2500)

    def _show_search_results(self):
        self._transient_timer.stop()
        top_left = self._search_box.mapTo(self, QPoint(0, self._search_box.height() + 2))
        rows = self._search_results.count()
        height = min(24 * rows + 6, 220)
        width = max(self._search_box.width() + 90, 220)
        self._search_results.setGeometry(top_left.x(), top_left.y(), width, height)
        self._search_results.raise_()
        self._search_results.show()

    def _hide_search_results(self):
        self._transient_timer.stop()
        self._search_results.hide()
        if self._npc_button.isChecked():
            self._npc_button.setChecked(False)
