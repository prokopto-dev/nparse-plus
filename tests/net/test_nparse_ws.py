"""nparse websocket client — golden frames against the locationserver protocol."""

import json
import queue

from nparseplus.core.events import (
    OtherPlayerLocationReceivedRemoteEvent,
    PlayerDisconnectReceivedRemoteEvent,
    WaypointsReceivedRemoteEvent,
)
from nparseplus.core.geometry import Loc
from nparseplus.core.zones import load_zone_database
from nparseplus.net.nparse_ws import NParseWsClient

ZONES = load_zone_database()


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.frames: queue.Queue = queue.Queue()
        self.closed = False

    def send(self, text: str) -> None:
        self.sent.append(text)

    def recv(self) -> str:
        item = self.frames.get()
        if isinstance(item, Exception):
            raise item
        return item

    def close(self) -> None:
        self.closed = True
        self.frames.put(ConnectionError("closed"))


def _connected_client(**kwargs) -> tuple[NParseWsClient, FakeSocket, list]:
    socket = FakeSocket()
    inbound: list = []
    client = NParseWsClient(
        url="ws://test:8424",
        group_key="testkey",
        on_inbound=inbound.append,
        zones=ZONES,
        connect=lambda url, timeout: socket,
        **kwargs,
    )
    # Wire the internals as _run would, without the thread.
    client._ws = socket
    client._connected.set()
    return client, socket, inbound


def test_outbound_location_matches_legacy_golden_frame() -> None:
    client, socket, _ = _connected_client()
    # "Your Location is 111, 222, 3" -> Loc(x=222, y=111, z=3);
    # legacy to_real_xy(111, 222) == (-222, -111) == (-loc.x, -loc.y).
    client.send_location(
        name="Xantik",
        guild_name=None,
        server=0,
        zone="gfaydark",
        sharing=0,
        loc=Loc(x=222.0, y=111.0, z=3.0),
    )
    (raw,) = socket.sent
    frame = json.loads(raw)
    assert frame["type"] == "location"
    assert frame["group_key"] == "testkey"
    loc = frame["location"]
    assert (loc["x"], loc["y"], loc["z"]) == (-222.0, -111.0, 3.0)
    assert loc["zone"] == "greater faydark"  # long zone name on this wire
    assert loc["player"] == "Xantik"
    assert "timestamp" in loc


def test_send_dropped_when_disconnected() -> None:
    client, socket, _ = _connected_client()
    client._connected.clear()
    client.send_location(
        name="X", guild_name=None, server=0, zone="gfaydark", sharing=0, loc=Loc(1, 2, 3)
    )
    assert socket.sent == []


def test_inbound_state_converts_scene_coords_and_zone() -> None:
    client, _socket, inbound = _connected_client()
    client._handle_frame(
        {
            "type": "state",
            "locations": {
                "greater faydark": {
                    "Soandso": {"x": -222.0, "y": -111.0, "z": 3.0, "timestamp": "2026-07-16"}
                }
            },
            "waypoints": {},
        }
    )
    (event,) = [e for e in inbound if isinstance(e, OtherPlayerLocationReceivedRemoteEvent)]
    remote = event.player
    assert remote.name == "Soandso"
    assert remote.zone == "gfaydark"  # short key for the map adapter
    # Raw wire order restored: first = -scene_y = 111, second = -scene_x = 222.
    assert (remote.x, remote.y, remote.z) == (111.0, 222.0, 3.0)
    assert remote.server is None


def test_vanished_player_synthesizes_disconnect() -> None:
    client, _socket, inbound = _connected_client()
    state = {
        "type": "state",
        "locations": {"greater faydark": {"Soandso": {"x": 0, "y": 0, "z": 0}}},
    }
    client._handle_frame(state)
    client._handle_frame({"type": "state", "locations": {"greater faydark": {}}})
    disconnects = [e for e in inbound if isinstance(e, PlayerDisconnectReceivedRemoteEvent)]
    assert [d.player.name for d in disconnects] == ["Soandso"]


def test_non_state_and_malformed_frames_ignored() -> None:
    client, _socket, inbound = _connected_client()
    client._handle_frame({"type": "something else"})
    client._handle_frame({"type": "state", "locations": {"zone": "not a dict"}})
    # Malformed player data yields no player events; the (empty) waypoint
    # snapshot for the zone still flows so removals reconcile.
    assert [e for e in inbound if not isinstance(e, WaypointsReceivedRemoteEvent)] == []


def test_reconnect_loop_retries_after_failure() -> None:
    attempts = {"n": 0}
    sleeps: list[float] = []
    sslopts: list = []
    socket = FakeSocket()

    def connect(url: str, timeout: float, sslopt=None) -> FakeSocket:
        attempts["n"] += 1
        sslopts.append(sslopt)
        if attempts["n"] == 1:
            raise ConnectionError("scripted")
        return socket

    client = NParseWsClient(url="ws://test:8424", connect=connect, sleep=sleeps.append)

    original_read = client._read_loop

    def read_then_stop(ws) -> None:
        client._stop.set()
        original_read(ws)

    client._read_loop = read_then_stop  # type: ignore[method-assign]
    socket.frames.put(ConnectionError("drop"))
    client._run()
    assert attempts["n"] == 2  # failed once (slept 5), then connected
    assert sleeps == [5.0]
    assert client.status == "stopped"
    # certifi pinned so wss:// works in the frozen app (empty default store).
    import certifi

    assert all(opt == {"ca_certs": certifi.where()} for opt in sslopts)


def test_outbound_waypoint_matches_legacy_share_death_frame() -> None:
    client, socket, _ = _connected_client()
    client.send_waypoint(
        name="Xantik",
        zone="gfaydark",
        loc=Loc(x=222.0, y=111.0, z=3.0),
        icon="corpse",
        timeout_minutes=60,
    )
    frame = json.loads(socket.sent[0])
    assert frame["type"] == "waypoint"
    assert frame["group_key"] == "testkey"
    location = frame["location"]
    assert (location["x"], location["y"], location["z"]) == (-222.0, -111.0, 3.0)
    assert location["zone"] == "greater faydark"
    assert location["player"] == "Xantik"
    assert location["icon"] == "corpse"
    assert location["timeout"] == 60


def test_inbound_waypoints_snapshot_converts_and_keys() -> None:
    client, _, inbound = _connected_client()
    client._handle_frame(
        {
            "type": "state",
            "locations": {"greater faydark": {}},
            "waypoints": {
                "greater faydark": {
                    "Xantik:1789000000.0": {"x": -222.0, "y": -111.0, "z": 3.0, "icon": "corpse"}
                }
            },
        }
    )
    events = [e for e in inbound if isinstance(e, WaypointsReceivedRemoteEvent)]
    assert len(events) == 1
    assert events[0].zone == "gfaydark"
    (waypoint,) = events[0].waypoints
    assert waypoint.key == "Xantik:1789000000.0"
    # Scene -> raw /loc print order (same convention as RemotePlayer).
    assert (waypoint.x, waypoint.y, waypoint.z) == (111.0, 222.0, 3.0)
    assert waypoint.icon == "corpse"


def test_inbound_empty_waypoints_still_emits_snapshot() -> None:
    client, _, inbound = _connected_client()
    client._handle_frame({"type": "state", "locations": {"greater faydark": {}}})
    events = [e for e in inbound if isinstance(e, WaypointsReceivedRemoteEvent)]
    assert len(events) == 1
    assert events[0].waypoints == ()
