# What the sharing network would let us leverage

**The question:** on a given server, can we obtain all users' information at once?

There are two sharing networks behind `settings.sharing.mode`, they answer
differently, and the difference is *who owns the server*. Keep them apart.

| | **PigParse** (`mode = "pigparse"`) | **nparse locationserver** (`mode = "nparse"`) |
| --- | --- | --- |
| Whose server | **Someone else's.** pigparse.org, shared with the EQTool community | **Yours, if you host it.** Reference server is in this repo at `locationserver/` |
| All users at once? | **No.** Not for players, not by any method the protocol has | **Effectively yes** — and where it falls short it is a **design choice you can change**, not a discovery problem |
| Widest player response | one player per frame (`PlayerLocationEvent`) | one `state` frame = *every player in a zone*, and the schema admits every zone at once |
| What stops you | a **server-side** group rule you cannot address around | nothing you do not control |

## The direct answer

**PigParse: no.** There is no roster method, no listing method, and no
server-wide player broadcast. The hub delivers exactly one player per
invocation, and it only delivers players from the `{Server}_{Zone}` group your
own connection has been placed in. Placement happens **on the server**, as a
side effect of you publishing a location with that zone. Reaching every zone
means announcing a spoofed presence into every zone, on a service you are a
guest on, at a rate its cadence norms forbid — see [Constraints](#constraints).
And even if you did all of that, the ceiling is *"every EQTool/nParse+ user
currently sharing"*, which is not the same population as *"all users on the
server"* and never will be.

**nparse locationserver: yes.** The stock server already broadcasts a whole
zone's player table to every connection in the `group_key`, **without checking
what zone the recipient is in** (`locationserver/location_server.py:86-94`).
The client already parses a multi-zone snapshot
(`net/nparse_ws.py:242-259`, a nested `for zone → for player` loop). The
only reason you do not currently see the whole world is that
`location_event()` chooses to put one zone in the frame
(`locationserver/location_server.py:50`). That is one dict comprehension. You
own both ends, the users are your own, and the data is data they sent you.

The rest of this note is the evidence.

## 1. Every method and endpoint the client knows about, with scope

### PigParse SignalR hub — `https://www.pigparse.org/PP`

Client → server (`net/pigparse_hub.py`):

| Target | Arguments | Scope | Code |
| --- | --- | --- | --- |
| `JoinServerGroup` | `[server:int]` | **per-server**; subscribes to server-wide events only (roars, custom timers). Grants **no** player traffic | `pigparse_hub.py:442` |
| `PlayerLocationEvent` | `[SignalrPlayerV2]` | **per-zone**; publishes you *and* enters you into `{Server}_{Zone}` | `pigparse_hub.py:312-334` |
| `DragonRoarEvent` | `[SignalRDragonRoar]` | **per-server** | `pigparse_hub.py:336-354` |
| `send_waypoint` | — | **no such wire.** Explicit no-op; corpse waypoints are nparse-mode only | `pigparse_hub.py:356-366` |

Server → client (`_DECODERS`, `pigparse_hub.py:242-247`):

| Target | Payload | Cardinality | Scope |
| --- | --- | --- | --- |
| `PlayerLocationEvent` | one `SignalrPlayerV2` | **1 player** | your current `{Server}_{Zone}` group |
| `PlayerDisconnected` | one `SignalrPlayerV2` | **1 player** | the departing connection's zone group |
| `DragonRoarEvent` | one `SignalRDragonRoar` | 1 event | per-server |
| `AddCustomTrigger` | one `SignalrCustomTimer` | 1 timer | per-server |

Four inbound targets, all singular. There is no fifth.

### PigParse REST — `https://pigparse.azurewebsites.net`

Everything the client calls (`net/pigparse_api.py`). Full published surface,
including routes nParse+ does not call, is in
[pigparse-api.md](pigparse-api.md).

| Method | Path | Returns | Scope | Code |
| --- | --- | --- | --- | --- |
| `POST` | `api/player/getbynames` | `list[WirePlayerRecord]` — name, guild, class, level | **per-server, but keyed by names you supply** | `pigparse_api.py:125` |
| `POST` | `api/player/upsertplayers` | — (write) | per-server | `pigparse_api.py:132` |
| `POST` | `api/item/postmultiple` | `list[ItemPrice]` | per-server, per-name-list | `pigparse_api.py:109` |
| `POST` | `api/item/wiki` | raw wiki markup | per-name | `pigparse_api.py:118` |
| `POST` | `api/inventory/upload` | — (write, **Bearer**) | per-character | `pigparse_api.py:158` |
| `POST` | `api/zone/npcactivity` | — (write) | per-server | `pigparse_api.py:185` |
| `GET` | `api/zone/quakev2/{server}` | — (write) | **per-server** | `pigparse_api.py:213` |
| `GET` | `api/boat/serverActivity/{server}` | `list[BoatActivity]` | **per-server, whole** | `pigparse_api.py:227` |
| `GET` | `api/rolltimer/timers/{server}` | `list[RollTimer]` | **per-server, whole** | `pigparse_api.py:234` |

Two endpoints return an entire server's state unconditionally — `boat_activity`
and `roll_timers`. Neither is about players. The only player-shaped bulk
response, `players_by_names`, is a **lookup**: it answers questions about names
you already have. It cannot be asked "who is there".

### nparse locationserver wire — `net/nparse_ws.py`

| Direction | Frame | Cardinality | Scope |
| --- | --- | --- | --- |
| → | `{"type":"location","group_key":k,"location":{…}}` | 1 player | places you in `group_key` |
| → | `{"type":"waypoint","group_key":k,"location":{…}}` | 1 marker | zone, expiring |
| ← | `{"type":"state","locations":{zone:{player:{…}}},"waypoints":{…}}` | **unbounded** | see below |
| ← | `{"type":"users","count":N}` | global count | **server has it, nobody sends it** — `notify_users` is commented out at both call sites (`location_server.py:107,115`) and the client ignores any non-`state` frame (`nparse_ws.py:238`) |

There is no server concept on this wire at all — `set_server` is a documented
no-op (`nparse_ws.py:105`) and `RemotePlayer.server` arrives as `None`.
Partitioning is `group_key`, a plain string with a default of `"public"`
(`config/settings.py:207`).

## 2. The zone rule: whose filter is it?

CLAUDE.md's cheatsheet says *"You only receive a zone's players after SENDING a
location with that zone."* Verified. It is **server-side, on PigParse only**,
and stating it as a property of "the sharing wire" is the one thing in that
line worth tightening.

### It is server-side, and the client has no reception filter at all

Four independent strands, all pointing the same way:

1. **The probe proved it empirically.** `tools/pigparse_probe_transcript.md`
   item 7: after `JoinServerGroup` alone, *"a 4-minute passive run saw only
   pings"*. Frames began only after a `PlayerLocationEvent` carrying a `Zone`.
   Two probes in a synthetic zone (`probezone`) then saw each other. That is a
   controlled experiment against the live hub, not an inference.
2. **The client cannot be the one filtering, because it never looks at the
   zone.** `PigParseHubClient._handle_invocation` (`pigparse_hub.py:478-489`)
   dispatches on `target` alone; `_decode_location` validates a `WirePlayer`
   and returns it. No zone comparison exists anywhere on the path.
3. **Neither does the coordinator.** `SharingCoordinator._dispatch_inbound`
   (`sharing.py:335-380`) is the complete inbound gate. Its filters are: mode
   is `off`; self-echo (name + server); roar server-match + `share_timers`;
   custom-timer server-match. **There is no zone branch.** A frame for a zone
   we have never entered would be published to the bus verbatim.
4. **The one zone check in the app is a *renderer*, not a filter.**
   `parsers/maps/window.py:644-645` declines to *draw* a dot from another zone —
   and its own comment says why the case exists at all: `# another zone
   (nparse-mode state spans zones)`. The app was written expecting the *other*
   network to deliver zones it is not standing in. The bus still carries those
   events; a plugin subscribing to `OtherPlayerLocationReceivedRemoteEvent`
   sees every one.

A fifth, sharper strand: `WirePlayer.sharing` (the `Everyone` / `GuildOnly`
flag) **is parsed and then dropped** — `to_remote_player()`
(`pigparse_models.py:70-81`) does not carry it, and `RemotePlayer`
(`core/events.py:310-322`) has no such field. So even *guild-only* visibility
cannot be a client-side decision here. Whatever the hub decides to send us, we
render. Everything we do not receive, we do not receive because the server did
not send it.

**Conclusion:** on PigParse the zone scope is a routing decision made in
`PPHub.cs`, addressable only by changing what group the server puts our
connection in — that is, by sending. It is not a knob on our side.

### On the nparse wire the rule is different, and weaker

The stock server's broadcast target list is *every connection whose `group_key`
matches* — with **no zone predicate**:

```python
# locationserver/location_server.py:86-94
if PLAYERS:
    keyed_players = [
        user
        for user in PLAYERS
        # if user != websocket and
        if PLAYERS[user][1] == group_key
    ]
    if keyed_players:
        websockets.broadcast(keyed_players, message)
```

Note also the commented-out `user != websocket` on line 90 — the sender is
deliberately included. That is one of the two reasons the coordinator
self-filters; see [§5](#5-auth-and-identity).

You still have to **send once**, but for a different reason and with a
different consequence. `register()` stores `(None, None)`
(`location_server.py:104-107`); your `group_key` is only recorded when you
publish (`location_server.py:136`). So a purely passive connection matches no
group and receives nothing. But after **one** location frame in **any** zone,
your connection is a permanent recipient for that `group_key` — and every
subsequent `/loc` from anyone in the group broadcasts *their* zone's full
player table to you, whatever zone you are in.

So: `send once → receive the whole group`, versus PigParse's
`send per zone → receive that zone`.

The only thing standing between that and a complete world view is the server's
own choice of what to put in the frame:

```python
# locationserver/location_server.py:48-52
return {
    "type": "state",
    "locations": {zone_name: PLAYER_LOCS.get(group_key, {}).get(zone_name, {})},
    "waypoints": waypoints,
}
```

`PLAYER_LOCS[group_key]` **is** the whole group, keyed by zone. One key is
selected. The client is already built for the general case
(`nparse_ws.py:242-259` iterates zones then players).

## 3. The widest single response the protocol admits

Ranked:

| Rank | Response | Breadth | Network |
| --- | --- | --- | --- |
| 1 | `state` frame | **unbounded** — `{zone: {player: loc}}`, every zone × every player in the group. Reference server sends 1 zone; the schema and the client both take N | nparse |
| 2 | `players_by_names` | N player records — **but N is the list you supplied**. A lookup, not an enumeration | PigParse REST |
| 3 | `boat_activity` / `roll_timers` | entire server's state, unconditional — **not players** | PigParse REST |
| 4 | `item/getall/{server}` | entire server's price table — not players, and not called by nParse+ ([pigparse-api.md](pigparse-api.md)) | PigParse REST |
| 5 | `PlayerLocationEvent` | **exactly one player** | PigParse hub |

The gap between rank 1 and rank 5 is the whole finding.

## 4. What "all users on a server" would actually require

### On PigParse

| Technique | Does the protocol offer it? | What it would cost |
| --- | --- | --- |
| Ask for a roster | **No such method exists.** Four inbound targets, all singular | — |
| Join every zone group | Only by **sending a `PlayerLocationEvent` per zone** — i.e. publishing a presence in a zone you are not in | 121 zones in `data/zones.json`. One connection can hold one zone at a time (each send re-groups it), so this is either 121 connections or continuous flapping. Both are presence spoofing: the `Name` is self-asserted (§5) |
| Poll a REST endpoint | No player-listing route exists. `getbynames` needs the names first | — |
| Harvest names, then enrich | `getbynames` works, but names come from your **own** `/who` output (`handlers/player_tracker.py`) — your zone, your session | Legitimate, and already what `PlayerTrackerHandler` does. Scope: whoever you can see |

**And the ceiling is lower than the question implies.** Even a perfect sweep
returns *"every EQTool/nParse+ user with sharing on, right now"* — not every
player on the server. Nothing in this protocol observes anyone who is not
running the software and opted in.

### On a self-hosted nparse locationserver

| Technique | Needed? |
| --- | --- |
| Join every zone group | **No.** `group_key` is the only partition; one send puts you in it |
| Spoof presence | **No.** You will already be receiving the group's traffic |
| Poll | **No.** The server pushes on every member's `/loc` |
| Change anything | Only if you want *all zones in one frame* instead of one zone per frame: `location_event()` (`location_server.py:37-52`) |

One caveat that is honestly a client bug for that use case: `NParseWsClient`
treats every `state` frame as global truth and synthesizes
`PlayerDisconnectReceivedRemoteEvent` for anyone absent from it
(`nparse_ws.py:260-266`). Feed it multi-zone frames and it behaves correctly;
feed it *alternating single-zone* frames and it churns disconnects. The
`_last_seen` diff would want to be per-zone. Worth knowing before anyone widens
the server frame.

## 5. Auth and identity

| | PigParse hub | PigParse REST | nparse ws |
| --- | --- | --- | --- |
| Identifies the connection | `connectionToken` from `POST /PP/negotiate` (`hubproto.py:82-107`) — an opaque session handle, not an account | none | none |
| Identifies the *player* | the `Name` string **in the payload you send** | `Name` in the body | `player` in the frame |
| Is that name verified? | **No.** `settings.sharing.player_name_override` substitutes any string (`sharing.py:515-516`); the server capitalizes it and uses it as a dict key (`location_server.py:120`) | No | No |
| Required to receive | send a location with a zone | — | send a location once |
| Auth on writes | none | **only** `api/inventory/upload` (Bearer, `pigparse_api.py:180`). Every other route is unauthenticated | none |
| Secret, if any | — | the Discord-issued API token, for uploads only | `group_key` — a shared string, the only thing making a private server private |

**Why the coordinator self-filters.** Both servers send your own frames back to
you, for different reasons: PigParse echoes to the zone group including the
sender (probe transcript item 8, confirmed live), and the locationserver
broadcasts to the whole group with its self-exclusion commented out
(`location_server.py:90`). Without a filter your own dot renders as a second
player standing on you. `SharingCoordinator._is_self_echo`
(`sharing.py:382-388`) drops name+server matches, case-sensitively like the C#,
and handles the nparse wire's missing server (`None` → a name match alone is
the echo). It is deliberately **not** applied to waypoints
(`sharing.py:359-363`) — your own corpse marker must render like everyone
else's.

A second thing worth naming: **`PlayerDisconnected` is presence, not
location.** It fires on connection close and carries the departing player's
full payload (probe item 9). That is a real per-zone presence signal you get
without asking.

## Constraints

These are in their own section because they are not caveats on the technique —
they decide *which server* a technique is allowed on.

| Norm | Value | Where |
| --- | --- | --- |
| Send cadence | one send per parsed `/loc` | `sharing.py:429-432` |
| Keepalive | resend last location every **10 s** | `KEEPALIVE_SECONDS`, `sharing.py:63` |
| Idle stop | keepalives stop after **5 idle minutes** | `IDLE_LIMIT_SECONDS`, `sharing.py:64` |
| Camp | camping clears the keepalive state | `sharing.py:434-436` |
| Roar dedupe | same spell within **4 s** is dropped | `ROAR_DEDUPE_SECONDS`, `sharing.py:65` |
| Kael throttle | one engage send per **15 s** | `KAEL_ENGAGE_THROTTLE_SECONDS`, `handlers/zone_activity.py:35` |
| Hub ping | 15 s, matching the server's own | `PING_INTERVAL_S`, `pigparse_hub.py:56` |

`core/sharing.py:19-21` states the rule in the module docstring: *"EQTool
cadence (do not exceed — shared service courtesy)"*.

**The honest statement, both halves:**

- Zone-group sweeping on PigParse is **technically possible**. Nothing
  authenticates the `Name`, nothing rate-limits us at the protocol level, and
  the group placement rule is mechanical. A client that sent a synthetic
  `PlayerLocationEvent` into each of 121 zones would be routed each of those
  zones' sharing players.
- It is **out of bounds on pigparse.org**. That server belongs to the EQTool
  community; we are a guest with no account, no quota and no agreement. The
  technique breaches every number in the table above, and it works by
  publishing presences that are not real — which pollutes the map for every
  other user of the service, not just ours.

- The **same technique is entirely legitimate on a self-hosted nparse
  locationserver**, and it is not even a technique there: you own the process,
  the data is your users' and they sent it to you deliberately, and the widest
  view is a change to a file in this repo. Nothing about `locationserver/`
  is anyone else's to be courteous with.

The distinction is ownership, not ethics-in-the-abstract. Same packets,
different server.

## What this does enable

Ranked by whose cooperation is needed.

### Needs nobody — build today

| Option | Basis |
| --- | --- |
| **Whole-group live view on a self-hosted server.** Widen `location_event()` to emit every zone; make `NParseWsClient._last_seen` per-zone. You get every connected player, every zone, continuously | `location_server.py:37-52`, `nparse_ws.py:260-266` |
| **Group presence and census.** `PLAYERS` is already a live connection table; `users_event()` already exists and is simply never sent. Wiring it back is uncommenting two lines and adding a client branch | `location_server.py:55-56,97-101,107,115` |
| **Persist what you already receive.** Every dot the app renders passes through the bus. A plugin subscribing to `OtherPlayerLocationReceivedRemoteEvent` / `PlayerDisconnectReceivedRemoteEvent` gets all of it, cross-zone, no protocol change — this is the SDK-shaped answer | `sharing.py:352-357`, `core/events.py:325-330` |
| **Waypoint/corpse history.** The nparse wire's per-zone waypoint snapshot is already a full reconciliation set | `nparse_ws.py:271-289` |
| **Your own observations, enriched.** `/who` output → `players_by_names` for guild/class/level. Already implemented; the roster is real data with honest provenance | `handlers/player_tracker.py` |

### Needs your own users

| Option | Note |
| --- | --- |
| **Run a locationserver for a guild or raid.** `nparse_ws_url` + `nparse_group_key` are per-user settings; a group that points at your host gives you complete, consensual visibility of that group | `config/settings.py:206-207` |
| **Extend the wire.** You control both ends: classes, levels, HP, target, timers — anything you agree to send. The protocol is untyped JSON with a `type` discriminator | `nparse_ws.py:237-239` |
| **Server-side history/analytics.** Zone population over time, movement traces, raid attendance — trivially, because the server already holds `PLAYER_LOCS[group_key]` in full | `location_server.py:18-21` |

### Needs PigParse's cooperation

| Option | Note |
| --- | --- |
| **A roster or server-wide subscribe method.** Would have to be added to `PPHub.cs`. Our client would need one decoder and one invocation — the hub half is genuinely small | `pigparse_hub.py:242-247` |
| **A bulk player REST route.** `getbynames` is the shape; an enumeration variant is a server change | `pigparse_api.py:125` |
| **Anything cross-zone at all.** There is no version of this that does not go through them, because the routing is theirs | — |

## Reading order for the next person

1. `tools/pigparse_probe_transcript.md` — the live experiment; item 7 is the answer to the zone question
2. `locationserver/location_server.py` — 197 lines, the whole self-hosted server
3. `core/sharing.py` — the single gate; `_dispatch_inbound` is the complete inbound filter list
4. [pigparse-api.md](pigparse-api.md) — the full published REST/hub surface, including routes nParse+ never calls
