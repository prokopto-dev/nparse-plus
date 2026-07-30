# Plugin API reference (`nparseplus_sdk` 1.x)

Everything importable from the `nparseplus_sdk` package root is the public
contract and follows semantic versioning: breaking changes only in a new
SDK major, and the app refuses plugins whose `requires_sdk` range does not
admit the SDK it bundles. `SDK_VERSION` is the installed SDK's version
string.

## PluginMeta

Frozen pydantic model — your plugin's identity and compatibility claim.

| Field | Type / default | Meaning |
| --- | --- | --- |
| `id` | `str`, required | `^[a-z][a-z0-9_-]{1,39}$`; keys consent, storage, window ids |
| `name` | `str`, required | display name |
| `version` | `"0.0.0"` | your plugin's own version |
| `requires_sdk` | `">=1.0,<2"` | PEP 440 range vs the bundled SDK |
| `min_app_version` | `None` | optional minimum nParse+ version |
| `description` / `author` / `homepage` | `""` | shown in consent + manager UI |

`check_compat(meta, sdk_version=..., app_version=...) -> str | None` returns
the human-readable refusal reason, or `None` when loadable.

## NParsePlugin

Base class. Subclass, set `meta` as a class attribute, implement
`activate(ctx)`; `deactivate()` is optional (app shutdown, best-effort).
Expose `create_plugin()` at module level returning an instance.

## PluginContext

The capability object handed to `activate` (a `typing.Protocol`; the app
implements it, `nparseplus_sdk.testing.FakePluginContext` fakes it).

**Identity / environment**

| Member | Meaning |
| --- | --- |
| `meta` | your validated `PluginMeta` |
| `app_version` / `sdk_version` | host versions, as strings |
| `logger` | `logging.Logger` child of the app's tree (`nparseplus.log`) |
| `storage` | per-plugin persistence (below) |

**Backend access** *(driver-thread objects — touch only inside your
subscriptions/ticks)*

| Member | Meaning |
| --- | --- |
| `timers` | the app `TimersService`; row classes via `nparseplus_sdk.timers` |
| `player` | the active character (name, server, class…) — read-only |
| `speaker` | text-to-speech: `speak(text)` |
| `pigparse` | PigParse REST client — call only inside a `submit` fetch |

**Registration** *(call during `activate`)*

| Method | Contract |
| --- | --- |
| `subscribe(EventClass, fn) -> Unsubscribe` | exact-type dispatch; `fn` runs on the driver thread, exceptions contained |
| `add_parser(parser)` | `parser.handle(line, ctx) -> bool` (True = consume); runs after all built-ins |
| `add_tick(fn)` | `fn(now: datetime)` every ~100 ms on the driver thread; never block |
| `submit(fetch, apply=None)` | `fetch()` on a worker thread; `apply(result)` back on the driver thread |
| `add_window(PluginWindowSpec)` | declare an overlay window |
| `add_settings_page(PluginSettingsPageSpec)` | declare a Settings page |

## PluginStorage

`ctx.storage` — isolated from the app's settings:

- `load() -> dict` — the plugin's JSON store (missing/corrupt → `{}`)
- `save(dict)` — atomic write
- `data_dir -> Path` — a private directory for anything bigger

## Window & settings-page specs

**`PluginWindowSpec(key, title, factory, default_geometry=(200,200,320,240),
command_key=None)`** — `key` must match the plugin-id pattern and be unique
within your plugin. `factory(wctx)` runs on the GUI thread and returns any
widget with `.toggle()`/`.isVisible()`; subclassing
`nparseplus_sdk.ui.PluginWindow` is the recommended way (overlay recipe +
persistence for free — call `self.restore_visibility()` last). The in-game
chat toggle is `toggle_<command_key>` (default `<id>_<key>`, `-`→`_`).

**`PluginWindowContext`** (the `wctx` your factory receives): `settings`,
`window_key`, `title`, `default_geometry`, `on_save`, `bridge` — the Qt
bridge whose `event_received` / `events_batch` signals deliver bus events
on the GUI thread.

**`PluginSettingsPageSpec(title, builder, apply=None)`** — `builder(parent)
-> QWidget` builds the page; `apply(widget)` runs on Settings
"Apply && Save". Both are individually guarded by the app.

## Host re-export modules (lazy)

These import the running app on first attribute access, so importing your
plugin stays possible in Qt-free/host-free environments:

- **`nparseplus_sdk.events`** — the typed event catalogue (`LineEvent` — the
  every-line firehose, `CommsEvent` + `CommsChannel`, `YouZonedEvent`,
  `DeathEvent`, …). Subscribe with the exact class.
- **`nparseplus_sdk.timers`** — `TimerRow`, `CounterRow`, `SpellRow`,
  `RollRow` and group constants for `ctx.timers`.
- **`nparseplus_sdk.ui`** — `PluginWindow` (needs PySide6; import inside
  window factories).

## Testing & validation helpers

- **`nparseplus_sdk.testing.FakePluginContext`** — records
  `subscriptions` / `parsers` / `ticks` / `windows` / `settings_pages` /
  `submitted`; `publish(event)` drives subscriptions, `run_submitted()`
  executes queued fetch/apply pairs; fake `storage`, `speaker`
  (`.spoken`), and `pigparse` (`.calls`).
- **`nparseplus_sdk.validate.validate_plugin(path, app_version=None) ->
  ValidationReport`** — the engine behind the
  `nparseplus-plugin validate` CLI (`ok`, `errors`, `warnings`, `meta`,
  registration counts). Warnings are advisory only.

## Other exports

`LineParser`, `LineInfoLike`, `Speaker`, `PluginStorage` (protocols),
`Unsubscribe` (type alias), `PLUGIN_ID_RE`.
