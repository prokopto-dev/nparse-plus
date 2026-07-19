"""nparse websocket sharing client (the ``sharing.mode = "nparse"`` fallback).

Port of the legacy ``helpers/location_service.py`` (Qt QWebSocket) onto the
Qt-free SharingClient protocol. The wire format is the sheeplauncher /
``locationserver/`` protocol:

    -> {"type": "location", "group_key": k,
        "location": {"x": sx, "y": sy, "z": z, "zone": <long zone name>,
                     "player": name, "timestamp": iso}}
    <- {"type": "state",
        "locations": {<long zone>: {<player>: {x, y, z, timestamp}}},
        "waypoints": {<long zone>: {<key>: {x, y, z, icon}}}}

Coordinates on this wire are **map scene** coordinates (the legacy client
applied ``to_real_xy``: scene = (-second, -first) of the raw ``/loc``
numbers) and zones are the **long** names the maps window uses — both are
converted at this boundary so the rest of the app only ever sees the
raw-wire-order ``RemotePlayer`` convention and short zone keys.

Inbound state is a full snapshot: players missing from it get synthesized
PlayerDisconnect events (the legacy client removed their dots directly), and
the zone's waypoint snapshot (corpse markers, keyed "Player:expiry") becomes
one WaypointsReceivedRemoteEvent the maps window reconciles against.

Deliberate divergence from the legacy module: dragon roars don't exist on
this wire (no-op).
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime

import certifi
import websocket

from nparseplus.core.events import (
    OtherPlayerLocationReceivedRemoteEvent,
    PlayerDisconnectReceivedRemoteEvent,
    RemoteEvent,
    RemotePlayer,
    RemoteWaypoint,
    WaypointsReceivedRemoteEvent,
)
from nparseplus.core.geometry import Loc
from nparseplus.core.zones import ZoneDatabase

logger = logging.getLogger(__name__)

DEFAULT_URL = "ws://sheeplauncher.net:8424"
RECONNECT_WAIT_S = 5.0  # legacy reconnect_delay default
SOCKET_TIMEOUT_S = 90.0  # server keepalive pings are ~20s; silence = dead


class NParseWsClient:
    def __init__(
        self,
        url: str = DEFAULT_URL,
        group_key: str = "public",
        on_inbound: Callable[[RemoteEvent], None] = lambda event: None,
        zones: ZoneDatabase | None = None,
        sleep: Callable[[float], None] = time.sleep,
        connect: Callable[..., websocket.WebSocket] = websocket.create_connection,
    ) -> None:
        self._url = url
        self._group_key = group_key
        self._on_inbound = on_inbound
        self._zones = zones
        self._sleep = sleep
        self._connect = connect
        self._ws: websocket.WebSocket | None = None
        self._send_lock = threading.Lock()
        self._connected = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._status = "stopped"
        # name -> long zone, from the last state snapshot (disconnect diffing)
        self._last_seen: dict[str, str] = {}

    @property
    def status(self) -> str:
        return self._status

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="nparse-ws", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._close_socket()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5.0)
        self._thread = None
        self._status = "stopped"

    def set_server(self, server: int | None) -> None:
        """The nparse protocol has no server concept; groups partition it."""

    def send_dragon_roar(self, **_kwargs: object) -> None:
        """Not part of this wire."""

    def send_location(
        self,
        *,
        name: str,
        guild_name: str | None,
        server: int,
        zone: str,
        sharing: int,
        loc: Loc,
        tracking_distance: float | None = None,
    ) -> None:
        if not self._connected.is_set():
            return
        frame = {
            "type": "location",
            "group_key": self._group_key,
            "location": self._location_dict(name, zone, loc),
        }
        try:
            self._send_raw(json.dumps(frame))
        except Exception:
            logger.warning("nparse ws send failed", exc_info=True)

    def _location_dict(self, name: str, zone: str, loc: Loc) -> dict:
        """The six shared keys of a location/waypoint frame's inner dict.

        Wire x/y are scene coordinates: scene = (-second, -first) of the raw
        /loc print order; Loc is (x=second, y=first) -> (-loc.x, -loc.y). Kept
        in one place so the axis swap is never fixed in only one of two spots.
        """
        return {
            "x": -loc.x,
            "y": -loc.y,
            "z": loc.z,
            "zone": self._long_zone(zone),
            "player": name,
            "timestamp": datetime.now().isoformat(),
        }

    def send_waypoint(
        self,
        *,
        name: str,
        zone: str,
        loc: Loc,
        icon: str = "corpse",
        timeout_minutes: int = 60,
    ) -> None:
        """Corpse/user waypoint (legacy share_death) — the exact send_location
        coordinate transform; the server expires it after ``timeout`` minutes."""
        if not self._connected.is_set():
            return
        location = self._location_dict(name, zone, loc)
        location["timeout"] = timeout_minutes
        location["icon"] = icon
        frame = {
            "type": "waypoint",
            "group_key": self._group_key,
            "location": location,
        }
        try:
            self._send_raw(json.dumps(frame))
        except Exception:
            logger.warning("nparse ws waypoint send failed", exc_info=True)

    # --- connection loop ---------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            self._status = "connecting"
            try:
                # certifi pinned for wss:// self-hosted servers (harmless for
                # plain ws://): the frozen app's default SSL store is empty —
                # see RawWsTransport.connect in pigparse_hub.py.
                ws = self._connect(
                    self._url,
                    timeout=SOCKET_TIMEOUT_S,
                    sslopt={"ca_certs": certifi.where()},
                )
            except Exception:
                logger.warning("nparse ws connect failed", exc_info=True)
                if not self._stop.is_set():
                    self._status = f"retrying in {RECONNECT_WAIT_S:.0f}s"
                    self._sleep(RECONNECT_WAIT_S)
                continue
            self._ws = ws
            self._connected.set()
            self._status = "connected"
            try:
                self._read_loop(ws)
            finally:
                self._connected.clear()
                self._close_socket()
            if not self._stop.is_set():
                self._status = f"retrying in {RECONNECT_WAIT_S:.0f}s"
                self._sleep(RECONNECT_WAIT_S)
        self._status = "stopped"

    def _read_loop(self, ws: websocket.WebSocket) -> None:
        while not self._stop.is_set():
            try:
                raw = ws.recv()
            except Exception:
                return
            if not raw:
                return
            try:
                self._handle_frame(json.loads(raw))
            except Exception:
                logger.warning("nparse ws bad frame", exc_info=True)

    def _send_raw(self, text: str) -> None:
        ws = self._ws
        if ws is None:
            raise ConnectionError("not connected")
        with self._send_lock:
            ws.send(text)

    def _close_socket(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            with contextlib.suppress(Exception):
                ws.close()

    # --- inbound -------------------------------------------------------------------

    def _handle_frame(self, message: dict) -> None:
        if message.get("type") != "state":
            return
        locations = message.get("locations", {})
        seen: dict[str, str] = {}
        for long_zone, players in locations.items():
            if not isinstance(players, dict):
                continue
            short_zone = self._short_zone(long_zone)
            for player_name, data in players.items():
                if not isinstance(data, dict):
                    continue
                seen[player_name] = long_zone
                # Scene -> raw wire order: first = -scene_y, second = -scene_x.
                remote = RemotePlayer(
                    name=player_name,
                    server=None,
                    zone=short_zone,
                    x=-float(data.get("y", 0.0)),
                    y=-float(data.get("x", 0.0)),
                    z=float(data.get("z", 0.0)),
                )
                self._on_inbound(OtherPlayerLocationReceivedRemoteEvent(player=remote))
        for player_name, long_zone in self._last_seen.items():
            if player_name not in seen:
                remote = RemotePlayer(
                    name=player_name, server=None, zone=self._short_zone(long_zone)
                )
                self._on_inbound(PlayerDisconnectReceivedRemoteEvent(player=remote))
        self._last_seen = seen

        # Waypoints: the server sends only the snapshot zone's markers (the
        # zone the state's locations are for); emit its full snapshot so the
        # maps window can reconcile removals too.
        snapshot_zone = next(iter(locations), None)
        if snapshot_zone is None:
            return
        waypoint_data = message.get("waypoints", {}).get(snapshot_zone, {})
        waypoints = tuple(
            RemoteWaypoint(
                key=key,
                # Scene -> raw wire order, exactly like the player dots above.
                x=-float(data.get("y", 0.0)),
                y=-float(data.get("x", 0.0)),
                z=float(data.get("z", 0.0)),
                icon=str(data.get("icon", "corpse")),
            )
            for key, data in waypoint_data.items()
            if isinstance(data, dict)
        )
        self._on_inbound(
            WaypointsReceivedRemoteEvent(zone=self._short_zone(snapshot_zone), waypoints=waypoints)
        )

    # --- zone-name conversion ---------------------------------------------------------

    def _long_zone(self, short_zone: str) -> str:
        if self._zones is not None:
            long_form = self._zones.long_name(short_zone)
            if long_form:
                return long_form
        return short_zone

    def _short_zone(self, long_zone: str) -> str:
        if self._zones is not None:
            short = self._zones.short_name(long_zone)
            if short:
                return short
        return long_zone
