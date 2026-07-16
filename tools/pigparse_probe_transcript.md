# PigParse SignalR hub — live probe transcript (M3, 2026-07-16)

Evidence gathered with `tools/probe_pigparse.py` (signalrcore 1.0.2) against the
live hub at `https://www.pigparse.org/PP`, per the M3 build order ("probe
first"). Two probe instances shared a synthetic zone (`probezone`, Green=0) so
each observed the other; only synthetic probe names appear below. Connection
tokens are redacted.

## Decision record (the wire facts net/ is built on)

1. **Negotiate**: standard ASP.NET Core SignalR. `POST /PP/negotiate?negotiateVersion=1`
   → 200 with `connectionId`, `connectionToken`, and transports
   `WebSockets` (Text+Binary), `ServerSentEvents`, `LongPolling`. WebSockets it is.
2. **Handshake**: `{"protocol":"json","version":0}\x1e` was accepted (server replied
   `{}\x1e`). The SignalR spec says `"version":1`; our client sends 1.
3. **Inbound property casing is camelCase** — NOT the PascalCase the C# DTOs
   suggest (ASP.NET Core's JSON hub protocol camelCases by default):
   ```
   {"type":1,"target":"PlayerLocationEvent","arguments":[{"name":"NparseProbeB",
    "trackingDistance":null,"guildName":null,"sharing":0,"server":0,
    "zone":"probezone","x":0,"y":0,"z":0,"groupName":"Green_probezone"}]}
   ```
   The computed `groupName` property is serialized too (ignore it).
4. **Outbound PascalCase is accepted**: our probe sent
   `{"Name":...,"GuildName":null,"Sharing":0,"Server":0,"Zone":"probezone","X":0.0,...}`
   and the server bound, stored, and rebroadcast it correctly (case-insensitive
   binding). So: emit PascalCase (matches EQTool), validate camelCase+PascalCase.
5. **Enums are ints** on the wire: `server:0` (Green), `sharing:0` (Everyone).
6. **`JoinServerGroup` takes the server enum int** and completes:
   `{"type":1,"target":"JoinServerGroup","arguments":[0],"invocationId":"…"}` →
   `{"type":3,"invocationId":"…","result":null}`.
7. **Group semantics confirmed**: after `JoinServerGroup` alone, no player
   frames arrive (4-minute passive run saw only pings). Location frames only
   flow once you SEND a `PlayerLocationEvent` with a `Zone` — the server then
   places the connection in the `{Server}_{Zone}` group and you receive that
   zone's players. Matches `PPHub.cs`.
8. **The server echoes your own location back to you** (probe B received
   "NparseProbeB" immediately after sending). The client-side self-filter
   (drop `server==mine && name==mine`) is mandatory, exactly as EQTool does.
9. **`PlayerDisconnected` fires on connection close** with the full camelCase
   player payload of the departing connection, delivered to its zone group.
10. **Keepalive**: server sends `{"type":6}` roughly every 15 s. Connection held
    with no drops across all runs.

Cross-delivery excerpt (probe A, 10 s resend cadence; B joined ~12 s later,
sent once, exited at the 1-minute mark):

```
[10:58:27.576] CALLBACK PlayerLocationEvent: [{"name": "NparseProbeA", ... "zone": "probezone", ...}]   # self-echo
[10:58:29.457] CALLBACK PlayerLocationEvent: [{"name": "NparseProbeB", ...}]                            # B's send
[10:58:37.609] CALLBACK PlayerLocationEvent: [{"name": "NparseProbeA", ...}]                            # A keepalive
...
[10:59:29.532] CALLBACK PlayerDisconnected:  [{"name": "NparseProbeB", ...}]                            # B closed
```

## Transport decision: minimal raw client, not signalrcore

signalrcore 1.0.2 (current release; vendors its own raw-socket websocket code,
sole dependency msgpack) **negotiates, connects, and receives fine, but every
invocation crashes**: `InvocationMessage.__repr__` still references the
pre-rename attribute `invocation_id` (now `invocationId`), and
`WebsocketTransport.send` eagerly formats the message for a debug log, so
`hub.send(...)` raises `AttributeError` unless the repr is monkeypatched (the
probe does exactly that — see `probe_pigparse.py`). It also sends handshake
`"version": 0` and owns its reconnect loop, which fights the M3 requirement
that reconnect policy (jitter, rejoin-group-on-every-connect) be ours and be
testable with a fake transport.

**Decision** (the brief's pre-approved fallback): `net/` implements a minimal
JSON-hub-protocol client — `POST negotiate` (httpx) → `wss` via
`websocket-client` → handshake `{"protocol":"json","version":1}\x1e` →
`\x1e`-framed JSON frames (type 1 invocations, type 3 completions, type 6
ping/pong, type 7 close). The protocol surface exercised above is tiny and now
pinned by this transcript. signalrcore stays a dependency of the probe tool
only.
