"""Map parser for nparse."""

import math
import re
from datetime import datetime

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
)

from nparseplus.config.settings import WaypointMarker
from nparseplus.core.events import (
    CorpseMarkerEvent,
    OtherPlayerLocationReceivedRemoteEvent,
    PlayerDisconnectReceivedRemoteEvent,
    WaypointsReceivedRemoteEvent,
)
from nparseplus.core.npc_search import NpcSearchIndex, normalize_name, search_all_zones
from nparseplus.core.zones import load_zone_database
from nparseplus.helpers import config, to_real_xy
from nparseplus.helpers.parser import ParserWindow
from nparseplus.parsers.maps import chrome
from nparseplus.parsers.maps.chrome import (
    MapHeader,
    MapRail,
    MapToolbar,
    RecenterPuck,
    ZoneEdgeTab,
)
from nparseplus.parsers.maps.mapcanvas import MapCanvas
from nparseplus.parsers.maps.mapclasses import MapPoint
from nparseplus.parsers.maps.mapdata import MapData
from nparseplus.ui import theme

ZONE_MATCHER = re.compile(r"There (is|are) \d+ players? in (?P<zone>.+)\.")

# How often the live chrome (loc chip, Z badge, recenter puck, edge tabs)
# re-reads the canvas. Cheap — it only touches label text and a few moves.
CHROME_TICK_MS = 250
# A player fix this far off the view center lights the recenter puck. Below
# it you are effectively centred and a lit puck would just be noise.
RECENTER_DEAD_ZONE = 30
# Interactions that count as "you are using the map" for the idle backdrop fade.
_WAKE_EVENTS = frozenset(
    {
        QEvent.Type.MouseMove,
        QEvent.Type.MouseButtonPress,
        QEvent.Type.Wheel,
        QEvent.Type.Enter,
        QEvent.Type.KeyPress,
    }
)


def _search_box_style():
    colors = theme.palette()
    return (
        f"QLineEdit {{ background-color: {colors.map_input_bg};"
        f" color: {colors.map_input_text}; border: none;"
        " border-radius: 3px; padding: 2px 4px; font-size: 12px; }"
    )


def _search_results_style():
    colors = theme.palette()
    return (
        f"QListWidget {{ background-color: {colors.map_input_bg};"
        f" color: {colors.map_input_text};"
        f" border: 1px solid {colors.map_input_border}; font-size: 12px; }}"
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


class WikiSearchBridge(QObject):
    """Marshals P99-wiki worker-thread results onto the GUI thread."""

    results_ready = Signal(str, list)  # (query, list[WikiNpc])


class Maps(ParserWindow):
    #: Sentinel for _chrome_ready(): ParserWindow.__init__ can deliver a
    #: resize/enter before this subclass has built any chrome.
    _header = None

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
        # NPC/label search: the input is the find palette (Ctrl+F) now, not a
        # permanent box in a button strip.
        self._search_box = QLineEdit(self)
        self._search_box.setObjectName("MapSearchBox")
        self._search_box.setPlaceholderText("Find NPC, label or zone…")
        self._search_box.setStyleSheet(_search_box_style())
        self._search_box.textChanged.connect(self._search_text_changed)
        self._search_box.hide()
        # results dropdown (child overlay, styled like the dark menu)
        self._search_results = QListWidget(self)
        self._search_results.setObjectName("MapSearchResults")
        self._search_results.setStyleSheet(_search_results_style())
        self._search_results.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # Long "elsewhere: <zone>" rows must elide, not grow a horizontal
        # scrollbar under a palette that is only as wide as the map.
        self._search_results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._search_results.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._search_results.itemClicked.connect(self._search_hit_selected)
        self._search_results.hide()
        # P99 wiki lookup (lazy client; results marshalled back via signal).
        self._wiki_client = None
        self._wiki_bridge = WikiSearchBridge()
        self._wiki_bridge.results_ready.connect(self._wiki_results_ready)

        # The single-glyph button strip is gone; its toggles live in the
        # hover-revealed toolbar, in words. The legacy ParserWindow menu strip
        # stays built (other code reads it) but never shows for maps.
        self._auto_hide_menu = True
        self._menu.setVisible(False)
        self._build_chrome()

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

        # Local tracking radius (game units) — app.py assigns a callable
        # computing it from the backend player's class + track skill.
        self.tracking_radius_provider = None

        # Settings-window changes to show_other_players (ParserWindow already
        # connects this signal for its own opacity/flag keys).
        QApplication.instance()._signals["settings"].config_updated.connect(self.sync_show_others)
        QApplication.instance()._signals["settings"].config_updated.connect(self.sync_map_chrome)

        self._chrome_timer = QTimer(self)
        self._chrome_timer.setInterval(CHROME_TICK_MS)
        self._chrome_timer.timeout.connect(self._refresh_chrome)
        self._chrome_timer.start()
        self._refresh_chrome()

    # -- chrome ---------------------------------------------------------------

    def _set_flags(self):
        """Claim the alpha channel before the native window is created.

        The window has to be translucent for a backdrop below 100% to show the
        game at all, and ``WA_TranslucentBackground`` is only honoured by the
        platform window that is created *after* it is set: ``QWidget``
        re-requests the surface format when the attribute changes, but
        ``QWindow::setFormat()`` after ``create()`` does not recreate the
        window, so a late request is simply never granted. Setting it in
        ``_build_chrome`` (where it used to live) is late — ``ParserWindow``
        shows the window at the end of its own ``__init__``, before this
        subclass runs, whenever the map was open at last quit — and the map
        then had no alpha channel to composite into: every backdrop value
        below 100% still read as opaque black (#99).

        ``_set_flags`` is the right home because ``setWindowFlags`` is the
        thing that (re)creates the native window, so this runs before every
        creation rather than before one of them. It is also why the backdrop
        used to start working after a Settings Apply or two: Apply reaches
        here through ``apply_window_state``, and the recreation was granting
        the alpha channel that construction had not.

        Since #65 the backdrop composites with ``CompositionMode_Source``,
        which writes colour AND alpha — into a surface with no alpha channel
        that discards the alpha outright, where the old ``SourceOver`` fill
        had accumulated it over the previous frame instead. That accumulation
        was the ghosting; removing it exposed the missing channel underneath.
        """
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        super()._set_flags()

    def _build_chrome(self):
        """Build the over-canvas chrome and the backdrop idle-fade timer.

        The window itself goes translucent so a backdrop below 100% actually
        shows the game: the legacy stylesheet painted ``#ParserWindow`` opaque
        black, which would have swallowed the canvas's new alpha. The
        attribute half of that lives in ``_set_flags`` — it has to be set
        before the platform window exists.
        """
        self.setStyleSheet("#ParserWindow { background: transparent; }")

        self._header = MapHeader(
            self, on_find=self.open_find, on_rail=self.toggle_rail, on_exit=self._flash_point
        )
        self._toolbar = MapToolbar(self)
        for key, text, tooltip in (
            ("show_poi", "✪ POI", "Show Points of Interest"),
            ("show_other_players", "◉ OTHERS", "Show other players' shared dots"),
            ("auto_follow", "⊙ FOLLOW", "Auto-center on your own location"),
            ("use_z_layers", "Ⓩ LAYERS", "Show Z layers"),
            ("show_grid", "# GRID", "Show the grid"),
            ("show_mouse_location", "⌖ LOC", "Show the loc under the mouse pointer"),
            ("show_zone_lines", "⇥ EXITS", "Show zone-line exits (edge tabs, header chips)"),
        ):
            self._toolbar.add_toggle(key, text, tooltip, lambda k=key: self._toggle_map_option(k))
        self._toolbar.add_toggle(
            "frame", "▣ FRAME", "Show the window title bar", self._toggle_frame
        )
        self._toolbar.finish()
        self._sync_toolbar()

        self._rail = MapRail(self)
        self._rail_open = False
        self._puck = RecenterPuck(self, on_click=self._recenter)
        self._puck.show()
        self._edge_tabs = []
        self._chrome_shown = False
        # The canvas decides whether a press starts a pan, and it cannot see
        # the chrome: these panels are the WINDOW's children, sitting over the
        # canvas rather than inside it.
        self._map.chrome_hit_test = self._chrome_covers

        # Idle fade: drop the backdrop to nothing when the map is just sitting
        # there, and restore the user's value the moment they touch it again.
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(self._fade_backdrop)
        self._backdrop_faded = False
        # Both: the viewport sees the mouse, the view itself holds the focus
        # and so receives the key presses (see eventFilter).
        self._map.installEventFilter(self)
        self._map.viewport().installEventFilter(self)
        self._arm_idle_fade()

    def _chrome_panels(self):
        """Every summoned surface that sits over the canvas, up or not."""
        return [
            self._header,
            self._toolbar,
            self._rail,
            self._puck,
            self._search_box,
            self._search_results,
            *self._edge_tabs,
        ]

    def _chrome_covers(self, point):
        """True when a CANVAS-local point lands under visible chrome.

        Qt's own hit-testing is the first line here — a press on the header
        goes to the header, not to the canvas underneath — so this exists to
        make the rule the canvas enforces ("a drag that starts on chrome does
        not pan") true on its own terms, including for a panel that is later
        made transparent for mouse events.
        """
        return chrome.covers_point(
            self._map.mapTo(self, point),
            [panel.geometry() for panel in self._chrome_panels() if panel.isVisible()],
        )

    @property
    def _map_colors(self):
        """The map chrome's colours for the active skin."""
        return chrome.map_colors()

    def apply_skin(self):
        """Re-dress the map chrome for the active skin.

        ``window_handles["maps"]`` is already wired, so app._apply_appearance's
        duck-typed loop finds this with no new plumbing — and no ParserWindow
        or legacy-config path is touched.
        """
        for panel in (self._header, self._toolbar, self._rail, self._puck):
            panel.apply_skin()
        for tab in self._edge_tabs:
            tab.apply_skin()
        self._map.update_()

    def sync_map_chrome(self):
        """Settings-window changes reach the chrome through config_updated."""
        self._sync_toolbar()
        self._backdrop_faded = False
        self._map.apply_backdrop_opacity(config.data["maps"].get("backdrop_opacity", 100))
        self._arm_idle_fade()

    def _sync_toolbar(self):
        for key in (
            "show_poi",
            "show_other_players",
            "auto_follow",
            "use_z_layers",
            "show_grid",
            "show_mouse_location",
            "show_zone_lines",
        ):
            self._toolbar.set_toggle(key, bool(config.data["maps"].get(key, True)))
        self._toolbar.set_toggle("frame", not self._frameless)

    def _toggle_map_option(self, key):
        """Flip one boolean map option and do whatever it implies."""
        value = not config.data["maps"].get(key, True)
        config.data["maps"][key] = value
        config.save()
        self._toolbar.set_toggle(key, value)
        if key == "show_other_players":
            self._show_others_changed(value)
        elif key == "auto_follow":
            self._map.center()
        elif key == "show_zone_lines":
            # Header chips, edge tabs and the rail's ZONE LINES section are one
            # answer to "which way out" shown three ways; the toggle owns all
            # three so turning it off actually clears the screen.
            self._refresh_zone_chrome()
        elif key != "show_mouse_location":
            self._map.update_()

    # -- backdrop idle fade -----------------------------------------------------

    def _arm_idle_fade(self):
        if not config.data["maps"].get("backdrop_fade_idle", False):
            return
        seconds = config.data["maps"].get("backdrop_fade_seconds", 5)
        self._idle_timer.start(max(1, int(seconds)) * 1000)

    def _wake_backdrop(self):
        """Any interaction restores the chosen backdrop and re-arms the fade."""
        if self._backdrop_faded:
            self._backdrop_faded = False
            self._map.apply_backdrop_opacity(config.data["maps"].get("backdrop_opacity", 100))
        self._idle_timer.stop()
        self._arm_idle_fade()

    def _fade_backdrop(self):
        """Idle: drop the fill to nothing. The geometry keeps full contrast —
        only the thing that was inking the game goes away."""
        if config.data["maps"].get("backdrop_fade_idle", False):
            self._backdrop_faded = True
            self._map.apply_backdrop_opacity(0)

    def eventFilter(self, obj, event):
        """Wake the backdrop on any interaction, and own the chrome's keys.

        The map canvas takes strong focus, so Tab/Ctrl+F/Esc land there and
        never reach this window's ``keyPressEvent`` — Tab would just move
        focus. Intercepting here is what makes those shortcuts real.
        """
        kind = event.type()
        if kind in _WAKE_EVENTS:
            self._wake_backdrop()
        # Not elif: a key press both wakes the backdrop and may be one of ours.
        if kind == QEvent.Type.KeyPress and self._handle_chrome_key(event):
            return True
        return False

    def _handle_chrome_key(self, event):
        """Tab = rail, Ctrl+F = find, Esc = close find. True if consumed."""
        key = event.key()
        if key == Qt.Key.Key_Tab:
            self.toggle_rail()
            return True
        if key == Qt.Key.Key_Escape and self._search_box.isVisible():
            self.close_find()
            return True
        if key == Qt.Key.Key_F and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.open_find()
            return True
        return False

    # -- chrome placement + live values -----------------------------------------

    def _chrome_ready(self):
        """False until ``_build_chrome`` has run.

        ``ParserWindow.__init__`` calls ``setGeometry`` before this subclass
        gets to build anything, so the very first resize/enter arrives with no
        chrome to place. (PySide6 swallows exceptions out of virtual overrides
        — this would have been an invisible traceback, not a crash.)
        """
        return self._header is not None

    def resizeEvent(self, event):
        if self._chrome_ready():
            chrome.place_chrome(
                self.rect(), self._header, self._toolbar, self._rail, self._puck, self._rail_open
            )
            self._position_search()
        super().resizeEvent(event)

    def enterEvent(self, event):
        # Deliberately NOT super(): ParserWindow would reveal the legacy menu
        # strip, which the header replaces.
        if self._chrome_ready():
            self._set_chrome_visible(True)
            self._wake_backdrop()

    def leaveEvent(self, event):
        if self._chrome_ready():
            self._set_chrome_visible(False)

    def _set_chrome_visible(self, shown):
        if shown == self._chrome_shown:
            return
        self._chrome_shown = shown
        chrome.place_chrome(
            self.rect(), self._header, self._toolbar, self._rail, self._puck, self._rail_open
        )
        self._header.setVisible(shown)
        self._toolbar.setVisible(shown)
        if shown:
            self._header.raise_()
            self._toolbar.raise_()
            self._puck.raise_()
        # The edge tabs and the header answer the same question; only one of
        # them is ever up.
        for tab in self._edge_tabs:
            tab.setVisible(not shown)

    def _refresh_chrome(self):
        """Per-tick chrome values: zone, loc chip, Z badge, puck, edge tabs.

        Skipped while the window is hidden — same gating as the other windows'
        refresh timers; a closed map has nothing to keep current.
        """
        data = self._map._data
        if data is None or not self.isVisible():
            return
        self._header.set_zone(data.zone or "")
        self._header.set_z(self._z_badge_text())
        self._header.set_location(*self._loc_chip_text())
        self._header.set_exits(self._zone_line_exits())
        self._toolbar.set_recording(bool(self._map._path_recording))
        self._update_puck()
        self._update_edge_tabs()

    def _z_badge_text(self):
        data = self._map._data
        groups = getattr(data.geometry, "z_groups", None) if data else None
        if not groups:
            return "Z —"
        index = min(max(self._map._z_index, 0), len(groups) - 1)
        return f"Z {index + 1}/{len(groups)}"

    def _you(self):
        data = self._map._data
        return data.players.get("__you__") if data else None

    def _loc_chip_text(self):
        player = self._you()
        if player is None or player.timestamp is None:
            return "no /loc yet", ""
        age = int((datetime.now() - player.timestamp).total_seconds())
        loc = f"{-player.location.y:.0f}, {-player.location.x:.0f}"
        return loc, f"{age}s" if age < 600 else "stale"

    def _zone_lines_shown(self) -> bool:
        return bool(config.data["maps"].get("show_zone_lines", True))

    def _refresh_zone_chrome(self) -> None:
        """Re-apply the zone-line toggle to all three surfaces at once."""
        self._header.set_exits(self._zone_line_exits())
        self._update_edge_tabs()
        if self._rail_open:
            self._rebuild_rail()

    def _zone_line_exits(self):
        """``[(display name, (scene x, scene y)), …]`` from the zone's own
        ``to_*`` POI labels — nothing invented."""
        data = self._map._data
        if data is None or not self._zone_lines_shown():
            return []
        return [
            (chrome.zone_line_label(text), (x, y))
            for text, x, y, _z in data.poi_entries()
            if chrome.is_zone_line(text)
        ]

    def _view_center_scene(self):
        return self._map.mapToScene(self._map.viewport().rect().center())

    def _update_puck(self):
        player = self._you()
        if player is None:
            self._puck.set_offset("", "")
            return
        center = self._view_center_scene()
        dx = player.location.x - center.x()
        dy = player.location.y - center.y()
        if math.hypot(dx, dy) * self._map._scale < RECENTER_DEAD_ZONE:
            self._puck.set_offset("", "")
            return
        self._puck.set_offset(
            chrome.bearing_arrow(dx, dy), chrome.format_distance(math.hypot(dx, dy))
        )

    def _update_edge_tabs(self):
        """Park a tab on the edge you would leave through, per zone line."""
        exits = self._zone_line_exits()
        while len(self._edge_tabs) > len(exits):
            self._edge_tabs.pop().deleteLater()
        center = self._view_center_scene()
        width, height = self.width(), self.height()
        for index, (name, (x, y)) in enumerate(exits):
            dx, dy = x - center.x(), y - center.y()
            arrow = chrome.bearing_arrow(dx, dy)
            vertical = abs(dy) * max(1, width) >= abs(dx) * max(1, height)
            if index < len(self._edge_tabs):
                tab = self._edge_tabs[index]
                if tab.property("signature") == (name, arrow, vertical):
                    self._place_edge_tab(tab, dx, dy)
                    continue
                tab.deleteLater()
                self._edge_tabs[index] = tab = ZoneEdgeTab(self, name, arrow, vertical)
            else:
                tab = ZoneEdgeTab(self, name, arrow, vertical)
                self._edge_tabs.append(tab)
            tab.setProperty("signature", (name, arrow, vertical))
            tab.setVisible(not self._chrome_shown)
            self._place_edge_tab(tab, dx, dy)

    def _place_edge_tab(self, tab, dx, dy):
        tab.adjustSize()
        x, y = chrome.edge_anchor(dx, dy, self.width(), self.height())
        tab.move(
            min(max(0, x - tab.width() // 2), max(0, self.width() - tab.width())),
            min(max(0, y - tab.height() // 2), max(0, self.height() - tab.height())),
        )
        tab.raise_()

    def _recenter(self):
        player = self._you()
        if player is not None:
            self._map.centerOn(player.location.x, player.location.y)
            self._wake_backdrop()

    def _flash_point(self, location):
        self._map.flash_location(location[0], location[1])

    # -- rail + find ------------------------------------------------------------

    def toggle_rail(self):
        self._rail_open = not self._rail_open
        if self._rail_open:
            self._rebuild_rail()
        chrome.place_chrome(
            self.rect(), self._header, self._toolbar, self._rail, self._puck, self._rail_open
        )
        self._rail.setVisible(self._rail_open)
        if self._rail_open:
            self._rail.raise_()

    def _rebuild_rail(self):
        data = self._map._data
        if data is None:
            return
        exits = [(name, "", chrome.GREEN) for name, _location in self._zone_line_exits()]
        markers = [
            (name, f"{-point.location.y:.0f}, {-point.location.x:.0f}", self._map_colors.gold)
            for name, point in list(data.waypoints.items())[:8]
        ]
        others = [(name, "", chrome.GREEN) for name in data.players if name != "__you__"][:8]
        respawn = chrome.format_respawn(data.get_default_spawn_timer())
        self._rail.rebuild(
            data.zone or "",
            [
                ("RESPAWN", [("default", respawn, self._map_colors.edge)]),
                ("ZONE LINES", exits),
                ("MARKERS", markers),
                ("SHARING", others),
            ],
        )

    def open_find(self):
        self._search_box.clear()
        self._search_box.show()
        self._position_search()
        self._search_box.raise_()
        self._search_box.setFocus(Qt.FocusReason.ShortcutFocusReason)
        # Explicitly, not via textChanged: clearing an already-empty box emits
        # nothing, and an empty palette showing the zone's notables is the
        # point (it replaced the old "☰ NPCs" button).
        self.show_notables()

    def close_find(self):
        self._search_box.hide()
        self._hide_search_results()

    def _position_search(self):
        """Center the find input near the top of the map (a palette, not a
        box wedged into a toolbar)."""
        width = min(330, max(180, self.width() - 60))
        self._search_box.setGeometry((self.width() - width) // 2, 52, width, 26)

    def keyPressEvent(self, event):
        # The event filter handles the same keys when the map canvas holds
        # focus; this covers a press that lands on the window itself.
        if self._chrome_ready() and self._handle_chrome_key(event):
            event.accept()
            return
        super().keyPressEvent(event)

    def handle_remote_event(self, event):
        """Shared-player events from the backend bus (queued Qt bridge).

        Wire coordinates are in the raw ``/loc`` print order (see
        core.events.RemotePlayer); map scene space is ``(-second, -first)``
        of that order — the same transform ``to_real_xy`` applies to the
        local ``Your Location is`` line above.
        """
        if isinstance(event, OtherPlayerLocationReceivedRemoteEvent):
            if not config.data["maps"].get("show_other_players", True):
                # Display-only gate (eqtool #211): the SharingCoordinator keeps
                # sending our own location; we just don't render the others.
                return
            remote = event.player
            zone_key = self._map._data.short_zone_key if self._map._data else None
            if remote.zone and zone_key and remote.zone != zone_key:
                return  # another zone (nparse-mode state spans zones)
            point = MapPoint(x=-remote.y, y=-remote.x, z=remote.z)
            self._map.add_player(
                remote.name,
                datetime.now(),
                point,
                tracking_distance=remote.tracking_distance,
            )
        elif isinstance(event, PlayerDisconnectReceivedRemoteEvent):
            self._map.remove_player(event.player.name)
        elif isinstance(event, CorpseMarkerEvent):
            self._handle_corpse_marker(event)
        elif isinstance(event, WaypointsReceivedRemoteEvent):
            self._reconcile_remote_waypoints(event)

    def _handle_corpse_marker(self, event):
        """Your own death: a persistent corpse marker in the death zone."""
        zone_key = self._map._data.short_zone_key if self._map._data else None
        point = MapPoint(x=-event.loc.x, y=-event.loc.y, z=event.loc.z)
        if zone_key and event.zone == zone_key:
            self._map.add_persistent_waypoint(f"{event.name}'s corpse", point, icon="corpse")
        elif self._map.marker_store is not None:
            # Map is showing another zone: persist straight to the store so
            # the marker is there when the death zone loads.
            markers = self._map.marker_store.load(event.zone)
            markers.user_waypoints.append(
                WaypointMarker(
                    x=point.x, y=point.y, z=point.z, icon="corpse", name=f"{event.name}'s corpse"
                )
            )
            del markers.user_waypoints[: -MapCanvas.MAX_PERSISTENT_WAYPOINTS]
            self._map.marker_store.save(event.zone, markers)

    def _reconcile_remote_waypoints(self, event):
        """Full waypoint snapshot for one zone from the nparse wire: add the
        new ones, drop vanished ones. Locally persisted markers are ours, not
        the server's — reconciliation never touches them."""
        zone_key = self._map._data.short_zone_key if self._map._data else None
        if not zone_key or event.zone != zone_key:
            return
        seen = set()
        for waypoint in event.waypoints:
            seen.add(waypoint.key)
            if waypoint.key not in self._map._data.waypoints:
                point = MapPoint(x=-waypoint.y, y=-waypoint.x, z=waypoint.z)
                self._map.add_waypoint(waypoint.key, point, waypoint.icon)
        for name in [
            key
            for key, waypoint in self._map._data.waypoints.items()
            if key not in seen and not getattr(waypoint, "persistent", False)
        ]:
            self._map.remove_waypoint(name)

    def parse(self, timestamp, text):
        # Zoning ("LOADING, PLEASE WAIT...") and death ("You have been slain")
        # are handled by the new core (LoadingPleaseWait parser, CorpseWaypoint
        # handler); the legacy MapsSignals they used to fan out to had no
        # listeners, so those branches are gone.
        if text[:16] == "You have entered":
            self._map.load_map_async(text[17:-1])
        elif ZONE_MATCHER.match(text):
            new_zone = ZONE_MATCHER.match(text).groupdict()["zone"].lower()
            new_zone = MapData.translate_who_zone(new_zone)
            if new_zone not in (self._map._data.zone.lower(), "everquest"):
                self._map.load_map_async(new_zone, keep_loc=True)
        elif text[:16] == "Your Location is":
            x, y, z = [float(value) for value in text[17:].strip().split(",")]
            x, y = to_real_xy(x, y)
            radius = self.tracking_radius_provider() if self.tracking_radius_provider else None
            self._map.add_player(
                "__you__", timestamp, MapPoint(x=x, y=y, z=z), tracking_distance=radius
            )
            self._map.record_path_loc((x, y, z))
        elif text[:16] == "start_recording_":
            recording_name = text.split()[0][16:]
            if recording_name:
                recording_name = recording_name.replace("_", " ")
                self._map.start_path_recording(recording_name)
        elif text[:17] == "rename_recording_":
            recording_name = text.split()[0][17:]
            if recording_name:
                recording_name = recording_name.replace("_", " ")
                self._map.rename_path_recording(new_name=recording_name)
        elif text[:14] == "stop_recording":
            self._map.stop_path_recording()

    # events
    def _purge_remote_players(self):
        if not self._map._data:
            return
        for name in [n for n in self._map._data.players if n != "__you__"]:
            self._map.remove_player(name)

    def _show_others_changed(self, shown):
        """Display-only gate (eqtool #211): hiding others never stops sending
        your own location — it just drops the dots already on screen."""
        if not shown:
            self._purge_remote_players()

    def sync_show_others(self):
        """Settings-window flips arrive via config_updated (see app.py)."""
        shown = config.data["maps"].get("show_other_players", True)
        self._toolbar.set_toggle("show_other_players", shown)
        self._show_others_changed(shown)

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
        query = text.strip()
        if len(query) < 2:
            # An empty palette is where the zone's notable NPCs live now (they
            # used to need their own button in the strip).
            self.show_notables()
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

    def show_notables(self):
        """The zone's notable NPCs — what an empty find palette offers."""
        notables = self._search_index.notables() if self._search_index else []
        if not notables:
            self._hide_search_results()
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
        box = self._search_box.geometry()
        rows = self._search_results.count()
        height = min(24 * rows + 6, max(80, self.height() - box.bottom() - 40))
        self._search_results.setGeometry(box.left(), box.bottom() + 2, box.width(), height)
        self._search_results.raise_()
        self._search_results.show()

    def _hide_search_results(self):
        self._transient_timer.stop()
        self._search_results.hide()
