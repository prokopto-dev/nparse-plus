"""Minimal ASP.NET Core SignalR JSON hub protocol (client side).

The probe (``tools/pigparse_probe_transcript.md``) showed signalrcore's
invocation path is broken, so nParse+ speaks the wire protocol directly —
it is tiny: an HTTP negotiate, a websocket upgrade, a one-line handshake,
then ``\\x1e``-separated JSON frames. Message types used here:

    1  Invocation       (both directions)
    3  Completion       (server acknowledgement for setup invocations)
    6  Ping             (server every ~15 s; client must ping too)
    7  Close

This module is the pure codec half (functions only, no I/O beyond
``negotiate``); the socket lifecycle lives in ``net.pigparse_hub``.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

RECORD_SEPARATOR = "\x1e"
HANDSHAKE_FRAME = '{"protocol":"json","version":1}' + RECORD_SEPARATOR
NEGOTIATE_TIMEOUT_S = 10.0

MSG_INVOCATION = 1
MSG_COMPLETION = 3
MSG_PING = 6
MSG_CLOSE = 7


class HandshakeError(RuntimeError):
    """The server rejected the hub handshake."""


def encode_frame(message: dict[str, Any]) -> str:
    return json.dumps(message, separators=(",", ":")) + RECORD_SEPARATOR


def decode_frames(raw: str | bytes) -> list[dict[str, Any]]:
    """Split a websocket payload into hub messages (may hold several)."""
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    frames: list[dict[str, Any]] = []
    for part in text.split(RECORD_SEPARATOR):
        if not part:
            continue
        try:
            decoded = json.loads(part)
        except ValueError:
            continue  # never let a malformed frame kill the reader
        if isinstance(decoded, dict):
            frames.append(decoded)
    return frames


def invocation_frame(target: str, arguments: list[Any], invocation_id: str | None = None) -> str:
    """Build an invocation, optionally requesting a completion reply."""
    message: dict[str, Any] = {
        "type": MSG_INVOCATION,
        "target": target,
        "arguments": arguments,
    }
    if invocation_id is not None:
        message["invocationId"] = invocation_id
    return encode_frame(message)


def ping_frame() -> str:
    return encode_frame({"type": MSG_PING})


def check_handshake_response(first_frame: dict[str, Any]) -> None:
    """The server answers the handshake with ``{}`` (or an ``error``)."""
    error = first_frame.get("error")
    if error:
        raise HandshakeError(str(error))


def negotiate(hub_url: str, http: httpx.Client | None = None) -> str:
    """POST the negotiate endpoint; return the websocket URL to connect to.

    Raises on any failure — the caller's reconnect policy handles it.
    """
    negotiate_url = hub_url.rstrip("/") + "/negotiate?negotiateVersion=1"
    owns_client = http is None
    client = http or httpx.Client(timeout=NEGOTIATE_TIMEOUT_S, headers={"User-Agent": "nparseplus"})
    try:
        resp = client.post(negotiate_url)
        resp.raise_for_status()
        payload = resp.json()
    finally:
        if owns_client:
            client.close()
    token = payload.get("connectionToken") or payload.get("connectionId")
    if not token:
        raise HandshakeError(f"negotiate returned no connection token: {payload!r}")
    transports = {
        str(t.get("transport"))
        for t in payload.get("availableTransports", [])
        if isinstance(t, dict)
    }
    if transports and "WebSockets" not in transports:
        raise HandshakeError(f"server offers no WebSockets transport: {sorted(transports)}")
    return websocket_url(hub_url, token)


def websocket_url(hub_url: str, connection_token: str) -> str:
    scheme, netloc, path, query, fragment = urlsplit(hub_url)
    ws_scheme = {"https": "wss", "http": "ws"}.get(scheme, scheme)
    query = f"id={connection_token}"
    return urlunsplit((ws_scheme, netloc, path, query, fragment))
