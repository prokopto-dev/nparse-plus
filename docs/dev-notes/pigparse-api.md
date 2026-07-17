# PigParse API and SignalR contract

Validated against the live services and source on **2026-07-17**. This document separates
the endpoints published by Swagger from older EQTool endpoints that are still live but no
longer appear in the generated OpenAPI document.

## Service roots and evidence

| Service | Root | Evidence |
| --- | --- | --- |
| REST API | `https://pigparse.azurewebsites.net` | [Swagger UI](https://pigparse.azurewebsites.net/swagger/index.html), [OpenAPI JSON](https://pigparse.azurewebsites.net/swagger/v1/swagger.json) |
| SignalR hub | `https://www.pigparse.org/PP` | [pinned EQTool client](https://github.com/smasherprog/EqTool/blob/d8e8084f/EQTool/Services/SignalrPlayerHub.cs), [pinned server hub](https://github.com/smasherprog/EqTool/blob/d8e8084f/EQToolApis/Hubs/PPHub.cs) |
| Legacy REST contract | same REST root | [pinned EQTool client](https://github.com/smasherprog/EqTool/blob/d8e8084f/EQTool/Services/PigParseApi.cs) |

The current OpenAPI document identifies itself as OpenAPI 3.0.4, title `P99 Pricing Data
API`, version `v1`. PigParse uses two hosts: REST calls go to the Azure host, while realtime
map and timer traffic uses the `pigparse.org` SignalR hub.

## Swagger-published REST endpoints

These are every operation in the live OpenAPI document as of the validation date.

| Method | Path | Input | Purpose / response |
| --- | --- | --- | --- |
| `GET` | `/api/inventory/profile` | query `character`, `server` | Inventory profile page/data response. |
| `GET` | `/api/inventory/profile-data` | query `character`, `server` | Character inventory profile data. |
| `POST` | `/api/inventory/upload` | JSON `InventoryUploadRequest`; Bearer token | Upload a character inventory snapshot. |
| `GET` | `/api/item/getall/{server}` | route `server` | All known item pricing records for a server. |
| `GET` | `/api/item/get/{server}/{itemname}` | route `server`, `itemname` | Pricing record for one named item. |
| `GET` | `/api/item/getmultiple/{server}` | route `server`; repeated query `itemnames` | Pricing records for several named items. |
| `POST` | `/api/item/postmultiple` | JSON `ItemsLookups` | Pricing records for several named items; this is what nParse+ uses. |
| `GET` | `/api/item/getdetails/{server}/{itemname}` | route `server`, `itemname` | Detailed item and auction information by name. |
| `GET` | `/api/item/getdetails/{itemid}` | route `itemid` | Legacy detail lookup by ID; Swagger marks this operation “do not use.” |
| `POST` | `/api/item/auctionParse` | JSON string | Submit/parse one auction log line. |
| `POST` | `/api/item/wiki` | JSON `P99WikiLookup` | Return raw P99 Wiki content for a name/zone lookup. |
| `GET` | `/api/secured/test` | authentication required | Authentication test endpoint. |

Relevant request shapes (PascalCase is what EQTool and nParse+ emit):

```json
{
  "Server": 0,
  "Itemnames": ["Rusty Sword", "Bone Chips"]
}
```

```json
{
  "CharacterName": "Soandso",
  "Server": 0,
  "Items": [
    {"Location": "WORN", "Name": "Cloth Cap", "ItemId": 1001, "Count": 1, "Slots": 0}
  ]
}
```

`POST /api/inventory/upload` requires `Authorization: Bearer <PigParse API token>`. The
current Swagger document does not declare a security scheme for it, but the server source
uses the `ApiToken` authentication scheme; see the [inventory controller](https://github.com/smasherprog/EqTool/blob/f95ecd9e1364e9814412745dfb67d631a21594d9/EQToolApis/Controllers/InventoryController.cs).

## Live legacy REST endpoints omitted from Swagger

These routes are called by the pinned and current EQTool client. Read-only probes on the
validation date confirmed `player/getbynames`, `boat/serverActivity`, and
`rolltimer/timers` still return HTTP 200. The remaining mutation routes are source-backed
and used by nParse+, but were not invoked during validation to avoid altering production
data.

| Method | Path | Input / behavior | nParse+ use |
| --- | --- | --- | --- |
| `POST` | `/api/player/getbynames` | `{"Players":[...],"Server":0}` | Resolve `/who` names to guild/class/level facts. |
| `POST` | `/api/player/upsertplayers` | `{"Players":[Player...],"Server":0}` | Share facts learned from `/who`. |
| `POST` | `/api/zone/npcactivity` | `NPCData`, `IsDeath`, `IsEngaged`, `Server` | Share selected NPC engage/death/location activity. |
| `GET` | `/api/zone/quakev2/{server}` | enum name route such as `Green` | Report an earthquake; the server deduplicates reports. |
| `POST` | `/api/boat/seen` | `StartPoint`, numeric `Boat`, numeric `Server` | Report a boat sighting. |
| `GET` | `/api/boat/serverActivity/{server}` | enum name route such as `Green` | Fetch recent shared boat sightings. |
| `GET` | `/api/rolltimer/timers/{server}` | enum name route such as `Green` | Fetch shared roll/quake timer state. |

Player records use `Name`, `GuildName`, numeric `Server`, numeric `PlayerClass`, and
`Level`. NPC data uses `Name`, `Zone`, `LocX`, and `LocY`.

## SignalR connection and hub methods

### Setup sequence

1. Wait until the active character's server is known.
2. `POST https://www.pigparse.org/PP/negotiate?negotiateVersion=1`.
3. Select the `WebSockets` transport and connect to
   `wss://www.pigparse.org/PP?id={connectionToken}`.
4. Send the JSON hub handshake `{"protocol":"json","version":1}` followed by the
   SignalR record separator byte `0x1e`; require a successful `{}` response.
5. Invoke `JoinServerGroup` with the numeric server enum and an `invocationId`; require a
   type-3 completion before reporting the connection as healthy.
6. Send type-6 pings about every 15 seconds. On reconnect, repeat negotiation, handshake,
   and group join. A character server change must use a fresh connection so the old server
   group is removed.

### Client-to-server hub invocations

| Target | Arguments | Purpose |
| --- | --- | --- |
| `JoinServerGroup` | `[server]` | Subscribe the connection to server-wide events. |
| `PlayerLocationEvent` | `[SignalrPlayerV2]` | Publish a location and enter the `{Server}_{Zone}` location group. |
| `DragonRoarEvent` | `[SignalRDragonRoar]` | Publish a dragon-roar event. |

### Server-to-client hub invocations

| Target | Payload | Purpose |
| --- | --- | --- |
| `PlayerLocationEvent` | `SignalrPlayerV2` | Map location update; the server also echoes the sender's own frame. |
| `PlayerDisconnected` | `SignalrPlayerV2` | Remove a disconnected remote player. |
| `AddCustomTrigger` | `SignalrCustomTimer` | Add a shared custom timer. |
| `DragonRoarEvent` | `SignalRDragonRoar` | Receive a shared dragon-roar event. |

Location frames only arrive after this connection publishes a location containing its zone;
joining the server group alone does not subscribe it to a zone group. nParse+ therefore sends
on each parsed `/loc`, repeats the last location every 10 seconds, stops after five idle
minutes or camping, and filters its own echoed player frame.

## Wire conventions

- Server enum values: `0 Green`, `1 Blue`, `2 Red`, `3 Quarm`; `4 MaxServers` is a sentinel
  and must not be sent. REST path parameters use enum names (`Green`), while request bodies
  and SignalR arguments use integers.
- Outbound DTOs use PascalCase to match EQTool. ASP.NET binding is case-insensitive, and
  inbound JSON normally arrives camelCase.
- The location wire preserves EverQuest `/loc` print order: wire `X = Loc.y`, wire
  `Y = Loc.x`, and wire `Z = Loc.z`. This axis swap is intentional.
- The API and hub are optional integrations. Network failures must degrade to reconnects or
  empty/no-op REST results and must never stop log parsing.

## nParse+ implementation map

| Concern | File |
| --- | --- |
| REST routes, retry policy, authentication | `src/nparseplus/net/pigparse_api.py` |
| SignalR negotiation and JSON framing | `src/nparseplus/net/hubproto.py` |
| Hub lifecycle, reconnects, group setup | `src/nparseplus/net/pigparse_hub.py` |
| Request/response DTO casing and axis conversion | `src/nparseplus/net/pigparse_models.py` |
| Sharing cadence, self-filter, thread crossing | `src/nparseplus/core/sharing.py` |
| Original live wire transcript | `tools/pigparse_probe_transcript.md` |

