"""Live probe of the PigParse SignalR hub (M3 build-order step 1).

A diagnostic TOOL, not a test: connects to the real hub with signalrcore,
joins a server group, and logs every wire frame (raw + decoded) so the wire
details the M3 brief lists — negotiate behavior, property casing, enum
shapes, ping cadence — are confirmed from evidence before net/ code is
built on them. The sanitized findings live in
``tools/pigparse_probe_transcript.md``.

Usage:
    uv run python tools/probe_pigparse.py --server 0 --minutes 3
    uv run python tools/probe_pigparse.py --send-name ProbeTest  # one synthetic send

The optional --send-name sends a single synthetic PlayerLocationEvent (in a
fake zone at 0,0,0) so the serializer path and the server's echo behavior
can be observed. Use a throwaway name.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime

import httpx
from signalrcore.hub_connection_builder import HubConnectionBuilder

DEFAULT_URL = "https://www.pigparse.org/PP"
RECORD_SEPARATOR = "\x1e"


def _stamp() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


class FrameLog:
    """Timestamped append-only log of everything seen on the wire."""

    def __init__(self, path: str | None) -> None:
        self._fh = open(path, "w", encoding="utf-8") if path else None  # noqa: SIM115

    def write(self, kind: str, payload: object) -> None:
        line = f"[{_stamp()}] {kind}: {payload}"
        print(line, flush=True)
        if self._fh:
            self._fh.write(line + "\n")
            self._fh.flush()

    def close(self) -> None:
        if self._fh:
            self._fh.close()


def probe_negotiate(url: str, log: FrameLog) -> None:
    """Record the raw negotiate response before signalrcore does its own."""
    negotiate_url = url.rstrip("/") + "/negotiate?negotiateVersion=1"
    try:
        resp = httpx.post(negotiate_url, timeout=10.0, headers={"User-Agent": "nparseplus-probe"})
        log.write("NEGOTIATE-STATUS", resp.status_code)
        log.write("NEGOTIATE-BODY", resp.text)
    except Exception as exc:  # diagnostic tool, report everything
        log.write("NEGOTIATE-ERROR", repr(exc))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--server", type=int, default=0, help="Server enum int (Green=0)")
    parser.add_argument("--minutes", type=float, default=3.0)
    parser.add_argument("--send-name", default=None, help="send one synthetic location as NAME")
    parser.add_argument(
        "--resend-seconds",
        type=float,
        default=None,
        help="with --send-name: resend the location at this cadence (EQTool uses 10s)",
    )
    parser.add_argument("--zone", default="probezone", help="synthetic zone for --send-name")
    parser.add_argument("--out", default=None, help="also write frames to this file")
    parser.add_argument("--debug", action="store_true", help="signalrcore DEBUG logging")
    args = parser.parse_args()

    log = FrameLog(args.out)
    log.write("PROBE", f"url={args.url} server={args.server} minutes={args.minutes}")
    log.write("PYTHON", sys.version.split()[0])
    import signalrcore

    log.write("SIGNALRCORE", getattr(signalrcore, "__version__", "unknown"))

    probe_negotiate(args.url, log)

    # signalrcore 1.0.2 bug: InvocationMessage.__repr__ references the old
    # attribute name `invocation_id` (renamed to `invocationId`), and
    # transport.send eagerly formats the message for a debug log — so every
    # invocation raises AttributeError. Patch the repr so the probe can run;
    # this is recorded evidence for the transport decision.
    from signalrcore.messages.invocation_message import InvocationMessage

    InvocationMessage.__repr__ = (  # type: ignore[method-assign]
        lambda self: f"InvocationMessage(target={self.target!r}, arguments={self.arguments!r})"
    )

    builder = HubConnectionBuilder().with_url(args.url)
    if args.debug:
        builder.configure_logging(logging.DEBUG)
    hub = builder.build()

    # Tap the raw frame paths at class level (the transport instance is only
    # created inside hub.start()). on_message receives every raw text frame
    # before protocol parsing; send sees every outbound message object.
    from signalrcore.transport.websockets.websocket_transport import WebsocketTransport

    original_on_message = WebsocketTransport.on_message
    original_send = WebsocketTransport.send

    def spy_on_message(self: object, app: object, raw_message: str) -> object:
        for frame in str(raw_message).split(RECORD_SEPARATOR):
            if frame:
                log.write("RECV-RAW", frame)
        return original_on_message(self, app, raw_message)

    def spy_send(self: object, message: object) -> object:
        try:
            encoded = hub.protocol.encode(message)
        except Exception:
            encoded = repr(message)
        log.write("SEND-RAW", str(encoded).replace(RECORD_SEPARATOR, ""))
        return original_send(self, message)

    WebsocketTransport.on_message = spy_on_message
    WebsocketTransport.send = spy_send

    targets = ("PlayerLocationEvent", "PlayerDisconnected", "AddCustomTrigger", "DragonRoarEvent")
    for target in targets:
        hub.on(
            target,
            lambda args_, t=target: log.write(f"CALLBACK {t}", json.dumps(args_, default=str)),
        )

    hub.on_open(lambda: log.write("STATE", "open"))
    hub.on_close(lambda: log.write("STATE", "closed"))
    hub.on_error(lambda err: log.write("ERROR", repr(err)))
    hub.on_reconnect(lambda: log.write("STATE", "reconnected"))

    log.write("STATE", "starting")
    hub.start()
    time.sleep(2.0)

    log.write("INVOKE", f"JoinServerGroup({args.server})")
    hub.send("JoinServerGroup", [args.server])

    def send_location() -> None:
        payload = {
            "Name": args.send_name,
            "GuildName": None,
            "Sharing": 0,  # MapLocationSharing.Everyone
            "Server": args.server,
            "Zone": args.zone,
            "X": 0.0,
            "Y": 0.0,
            "Z": 0.0,
            "TrackingDistance": None,
        }
        log.write("INVOKE", f"PlayerLocationEvent({json.dumps(payload)})")
        hub.send("PlayerLocationEvent", [payload])

    if args.send_name:
        send_location()

    deadline = time.monotonic() + args.minutes * 60
    last_resend = time.monotonic()
    try:
        while time.monotonic() < deadline:
            time.sleep(1.0)
            if (
                args.send_name
                and args.resend_seconds
                and time.monotonic() - last_resend >= args.resend_seconds
            ):
                send_location()
                last_resend = time.monotonic()
    except KeyboardInterrupt:
        log.write("STATE", "interrupted")
    finally:
        log.write("STATE", "stopping")
        hub.stop()
        log.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
