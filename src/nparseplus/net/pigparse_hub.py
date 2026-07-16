"""PigParse SignalR hub client (port of EQTool/Services/SignalrPlayerHub.cs).

Split three ways so the reconnect policy is testable offline:

* ``HubTransport`` — the I/O boundary protocol. ``RawWsTransport`` is the
  real one (negotiate -> websocket -> handshake -> framed JSON, per
  ``net.hubproto``); tests script a fake.
* ``PigParseHubClient`` — one daemon thread running EQTool's reconnect
  policy: connect, **JoinServerGroup immediately after every (re)connect**,
  hold the session (pinging every 15 s), and on close retry after a random
  0-4 s jitter (connect failures wait a flat 5 s). Sends are dropped unless
  connected, like the C# guard.
* Decoding — inbound invocations validate through the wire DTOs and come out
  as the frozen remote events from ``core.events``; the caller's
  ``on_inbound`` receives them **on the client/reader thread** and must only
  enqueue (the sharing coordinator republishes on the driver thread — the
  bus is not thread-safe).

All failures are non-fatal; ``status`` is a human string for the tray.
"""

from __future__ import annotations

import contextlib
import logging
import random
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

import websocket

from nparseplus.core.events import (
    CustomTimerReceivedRemoteEvent,
    DragonRoarRemoteEvent,
    OtherPlayerLocationReceivedRemoteEvent,
    PlayerDisconnectReceivedRemoteEvent,
    RemoteEvent,
)
from nparseplus.core.geometry import Loc
from nparseplus.net import hubproto
from nparseplus.net.pigparse_models import (
    WireCustomTimer,
    WireDragonRoar,
    WirePlayer,
    wire_dragon_roar_from_loc,
    wire_player_from_loc,
)

logger = logging.getLogger(__name__)

HUB_URL = "https://www.pigparse.org/PP"
PING_INTERVAL_S = 15.0
CONNECT_FAIL_WAIT_S = 5.0  # EQTool: flat 5 s between failed connect attempts
RECONNECT_JITTER_MAX_S = 4  # EQTool: random 0-4 s after a drop
SOCKET_TIMEOUT_S = 60.0  # server pings every ~15 s; a silent minute is dead


class HubTransport(Protocol):
    """One websocket session. Set the callbacks before ``connect``."""

    on_invocation: Callable[[str, list[Any]], None]
    on_close: Callable[[Exception | None], None]

    def connect(self) -> None: ...  # blocking; raises on failure
    def send_invocation(self, target: str, arguments: list[Any]) -> None: ...
    def send_ping(self) -> None: ...
    def close(self) -> None: ...


class RawWsTransport:
    """The real transport: httpx negotiate + websocket-client + a reader thread."""

    def __init__(self, url: str) -> None:
        self.on_invocation: Callable[[str, list[Any]], None] = lambda target, args: None
        self.on_close: Callable[[Exception | None], None] = lambda exc: None
        self._url = url
        self._ws: websocket.WebSocket | None = None
        self._send_lock = threading.Lock()
        self._close_notified = False
        self._close_lock = threading.Lock()

    def connect(self) -> None:
        ws_url = hubproto.negotiate(self._url)
        ws = websocket.create_connection(ws_url, timeout=hubproto.NEGOTIATE_TIMEOUT_S)
        try:
            ws.send(hubproto.HANDSHAKE_FRAME)
            frames = hubproto.decode_frames(ws.recv())
            if not frames:
                raise hubproto.HandshakeError("no handshake response")
            hubproto.check_handshake_response(frames[0])
            pending = frames[1:]  # anything piggybacked after the handshake
        except BaseException:
            with contextlib.suppress(Exception):
                ws.close()
            raise
        ws.settimeout(SOCKET_TIMEOUT_S)
        self._ws = ws
        reader = threading.Thread(
            target=self._read_loop, args=(pending,), name="pigparse-hub-reader", daemon=True
        )
        reader.start()

    def _read_loop(self, pending: list[dict[str, Any]]) -> None:
        error: Exception | None = None
        try:
            while True:
                for message in pending:
                    kind = message.get("type")
                    if kind == hubproto.MSG_INVOCATION:
                        self.on_invocation(
                            str(message.get("target", "")), list(message.get("arguments", []))
                        )
                    elif kind == hubproto.MSG_CLOSE:
                        return
                raw = self._ws.recv() if self._ws is not None else ""
                if not raw:
                    return
                pending = hubproto.decode_frames(raw)
        except Exception as exc:
            error = exc
        finally:
            self._notify_close(error)

    def _notify_close(self, error: Exception | None) -> None:
        with self._close_lock:
            if self._close_notified:
                return
            self._close_notified = True
        with contextlib.suppress(Exception):
            if self._ws is not None:
                self._ws.close()
        self.on_close(error)

    def _send_raw(self, frame: str) -> None:
        ws = self._ws
        if ws is None:
            raise ConnectionError("transport not connected")
        with self._send_lock:
            ws.send(frame)

    def send_invocation(self, target: str, arguments: list[Any]) -> None:
        self._send_raw(hubproto.invocation_frame(target, arguments))

    def send_ping(self) -> None:
        self._send_raw(hubproto.ping_frame())

    def close(self) -> None:
        self._notify_close(None)


def _decode_location(payload: dict[str, Any]) -> RemoteEvent:
    wire = WirePlayer.model_validate(payload)
    return OtherPlayerLocationReceivedRemoteEvent(player=wire.to_remote_player())


def _decode_disconnect(payload: dict[str, Any]) -> RemoteEvent:
    wire = WirePlayer.model_validate(payload)
    return PlayerDisconnectReceivedRemoteEvent(player=wire.to_remote_player())


def _decode_dragon_roar(payload: dict[str, Any]) -> RemoteEvent:
    wire = WireDragonRoar.model_validate(payload)
    # Location only when the sender knew all three coordinates (C# parity);
    # kept in wire order like RemotePlayer.
    location = None
    if wire.x is not None and wire.y is not None and wire.z is not None:
        location = Loc(x=wire.x, y=wire.y, z=wire.z)
    return DragonRoarRemoteEvent(spell_name=wire.spell_name, location=location, server=wire.server)


def _decode_custom_timer(payload: dict[str, Any]) -> RemoteEvent:
    wire = WireCustomTimer.model_validate(payload)
    return CustomTimerReceivedRemoteEvent(
        name=wire.name,
        duration_in_seconds=wire.duration_in_seconds,
        spell_name_icon=wire.spell_name_icon,
        server=wire.server,
    )


_DECODERS: dict[str, Callable[[dict[str, Any]], RemoteEvent]] = {
    "PlayerLocationEvent": _decode_location,
    "PlayerDisconnected": _decode_disconnect,
    "DragonRoarEvent": _decode_dragon_roar,
    "AddCustomTrigger": _decode_custom_timer,
}


class PigParseHubClient:
    def __init__(
        self,
        url: str = HUB_URL,
        on_inbound: Callable[[RemoteEvent], None] = lambda event: None,
        transport_factory: Callable[[str], HubTransport] = RawWsTransport,
        rng: random.Random | None = None,
        sleep: Callable[[float], None] = time.sleep,
        ping_interval_s: float = PING_INTERVAL_S,
    ) -> None:
        self._url = url
        self._on_inbound = on_inbound
        self._transport_factory = transport_factory
        self._rng = rng or random.Random()
        self._sleep = sleep
        self._ping_interval_s = ping_interval_s
        self._server: int | None = None
        self._transport: HubTransport | None = None
        self._connected = threading.Event()
        self._session_closed = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._status = "stopped"

    @property
    def status(self) -> str:
        return self._status

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="pigparse-hub", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        transport = self._transport
        if transport is not None:
            with contextlib.suppress(Exception):
                transport.close()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5.0)
        self._thread = None
        self._status = "stopped"

    def set_server(self, server: int | None) -> None:
        """Remember the server; (re)join its group if the change is live."""
        changed = server != self._server
        self._server = server
        if changed and server is not None and self._connected.is_set():
            self._invoke("JoinServerGroup", [server])

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
        """Core-native SharingClient surface; wire conversion (and the axis
        swap) happens in pigparse_models."""
        wire = wire_player_from_loc(
            name=name,
            guild_name=guild_name,
            server=server,
            zone=zone,
            sharing=sharing,
            loc=loc,
            tracking_distance=tracking_distance,
        )
        self._invoke("PlayerLocationEvent", [wire.wire_dump()])

    def send_dragon_roar(
        self,
        *,
        spell_name: str,
        guild_name: str | None,
        server: int,
        zone: str,
        sharing: int,
        loc: Loc | None,
    ) -> None:
        wire = wire_dragon_roar_from_loc(
            spell_name=spell_name,
            guild_name=guild_name,
            server=server,
            zone=zone,
            sharing=sharing,
            loc=loc,
        )
        self._invoke("DragonRoarEvent", [wire.wire_dump()])

    def _invoke(self, target: str, arguments: list[Any]) -> None:
        """C# guard: invocations are silently dropped unless connected."""
        transport = self._transport
        if transport is None or not self._connected.is_set():
            return
        try:
            transport.send_invocation(target, arguments)
        except Exception:
            logger.warning("pigparse hub send %s failed", target, exc_info=True)

    # --- connection loop (the one client thread) -------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            transport = self._connect_once()
            if transport is None:
                if not self._stop.is_set():
                    self._status = f"retrying in {CONNECT_FAIL_WAIT_S:.0f}s"
                    self._sleep(CONNECT_FAIL_WAIT_S)
                continue
            self._hold_session(transport)
            if self._stop.is_set():
                break
            jitter = self._rng.randint(0, RECONNECT_JITTER_MAX_S)
            self._status = f"reconnecting in {jitter}s"
            self._sleep(jitter)
        self._status = "stopped"

    def _connect_once(self) -> HubTransport | None:
        self._status = "connecting"
        transport = self._transport_factory(self._url)
        transport.on_invocation = self._handle_invocation
        transport.on_close = self._handle_close
        # Cleared before connect(): the reader thread may drop the session
        # (and fire on_close) before connect() even returns.
        self._session_closed.clear()
        try:
            transport.connect()
        except Exception:
            logger.warning("pigparse hub connect failed", exc_info=True)
            return None
        self._transport = transport
        self._connected.set()
        self._status = "connected"
        # EQTool rejoins the server group after every single (re)connect.
        if self._server is not None:
            self._invoke("JoinServerGroup", [self._server])
        return transport

    def _hold_session(self, transport: HubTransport) -> None:
        """Block until the session drops, keeping the connection pinged."""
        while not self._session_closed.wait(self._ping_interval_s):
            if self._stop.is_set():
                break
            try:
                transport.send_ping()
            except Exception:
                logger.warning("pigparse hub ping failed", exc_info=True)
        self._connected.clear()
        self._transport = None
        with contextlib.suppress(Exception):
            transport.close()

    # --- transport callbacks (reader thread) -----------------------------------

    def _handle_close(self, error: Exception | None) -> None:
        if error is not None:
            logger.warning("pigparse hub connection closed: %r", error)
        self._connected.clear()
        self._session_closed.set()

    def _handle_invocation(self, target: str, arguments: list[Any]) -> None:
        decoder = _DECODERS.get(target)
        if decoder is None:
            return
        if not arguments or not isinstance(arguments[0], dict):
            return
        try:
            event = decoder(arguments[0])
        except Exception:
            logger.warning("pigparse hub bad %s payload", target, exc_info=True)
            return
        self._on_inbound(event)
