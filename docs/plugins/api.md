# Plugin API reference (`nparseplus_sdk` 1.x)

Everything importable from the `nparseplus_sdk` package root is the public
contract and follows semantic versioning: breaking changes only in a new
SDK major, and the app refuses plugins whose `requires_sdk` range does not
admit the SDK it bundles. `SDK_VERSION` is the installed SDK's version
string — a plain literal in `nparseplus_sdk/__init__.py`, deliberately not
an `importlib.metadata` lookup (a frozen app has no dist metadata).

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
| `update_url` | `""` | optional https index the app polls for **your** updates — see [Shipping updates](developing.md#shipping-updates-without-a-registry) |

`check_compat(meta, *, sdk_version, app_version=None) -> str | None` returns
the human-readable refusal reason, or `None` when loadable. `sdk_version` is
keyword-only and required; malformed version strings come back as a reason,
never an exception.

## NParsePlugin

Base class. Subclass, set `meta` as a class attribute, implement
`activate(ctx)`; `deactivate()` is optional (app shutdown, best-effort).
Expose `create_plugin()` at module level returning an instance.

## PluginContext

The capability object handed to `activate` (a `typing.Protocol`; the app
implements it in `nparseplus.core.plugins.context.HostPluginContext`,
`nparseplus_sdk.testing.FakePluginContext` fakes it).

**Identity / environment**

| Member | Meaning |
| --- | --- |
| `meta` | your validated `PluginMeta` |
| `app_version` / `sdk_version` | host versions, as strings |
| `logger` | `logging.Logger` named `nparseplus.plugins.<your-id>` (lands in `nparseplus.log`) |
| `storage` | per-plugin persistence (below) |

**The EQ install** *(SDK 1.2+)*

| Member | Meaning |
| --- | --- |
| `eq_dir` | `pathlib.Path` of the user's EverQuest directory, or `None` when they have not set one. Read **live** from the app's settings — do not cache it at `activate` time |
| `eq_is_running()` | best-effort "is the client up?", for the *restart EQ for this to take effect* warning. **Spawns a process (~18 ms): never call it from a tick.** Answers `False` on failure, and always on Windows |

`None` is the normal first-run state, not an error — the app works with only
a log directory set. Treat it as "not available yet".

Writing into `eq_dir` is a promise to the user. Use
[`nparseplus_sdk.eqfiles`](#host-re-export-modules-lazy) rather than plain
`open()`: it carries the app's own preflight / backup-first / splice-one-section
cycle, and it is the difference between an edit the user can undo and one they
cannot.

**Backend access** *(driver-thread objects — touch only inside your
subscriptions/ticks)*

The SDK types these as `Any` on purpose: they are host objects, and typing
them would give the SDK a hard dependency on the app. The concrete classes,
for when you need to read the source or set up type checking with the app
installed:

| Member | Concrete type | Meaning |
| --- | --- | --- |
| `timers` | `nparseplus.core.timers.TimersService` | the Timers window's row store; row classes via `nparseplus_sdk.timers` |
| `player` | `nparseplus.core.player.ActivePlayer` | the active character (`name`, `server`, `player_class`, …) — read-only by convention |
| `speaker` | satisfies `nparseplus.audio.tts.Speaker` | text-to-speech: `speak(text)`. The app hands you a swappable holder, so voice/volume changes follow automatically |
| `pigparse` | `nparseplus.net.pigparse_api.PigParseApiClient`, satisfying the Qt-free `nparseplus.core.pigparse.PigParseApi` protocol | PigParse REST client (`item_prices`, `item_wiki`, `boat_activity`, …). Reading the property is thread-safe; its **methods block on HTTP**, so call them only inside a `submit` fetch |

With sharing off, the host lazily builds one PigParse client and one network
worker shared by all plugins, so `ctx.pigparse` and `ctx.submit` are never
`None` and never depend on the user's sharing settings.

**Registration** *(call during `activate`)*

| Method | Contract |
| --- | --- |
| `subscribe(EventClass, fn) -> Unsubscribe` | exact-type dispatch; `fn` runs on the driver thread, exceptions contained. Subscribe-only — there is no publish |
| `add_parser(parser)` | `parser.handle(line, ctx) -> bool` (True = consume); appended after all built-ins, so it never sees a consumed line |
| `add_tick(fn)` | `fn(now: datetime)` every ~100 ms on the driver thread. **Supervised: two consecutive runs over 250 ms and the driver drops it permanently** — see [the tick budget](developing.md#the-tick-budget) |
| `submit(fetch, apply=None)` | `fetch()` on a worker thread; `apply(result)` back on the driver thread. A raise in `fetch` is logged and drops the `apply` |
| `add_window(PluginWindowSpec)` | declare an overlay window |
| `add_settings_page(PluginSettingsPageSpec)` | declare a Settings page |
| `add_window_timer(name, *, group, started_at, base_seconds, window_seconds, allow_duplicates=False)` | arm a variable respawn ("pop") window and return the row (`WindowTimerLike`). See [Pop windows](../features/respawn-timers.md#pop-windows). SDK 1.3 |

## PluginStorage

`ctx.storage` — isolated from the app's settings, living in
`plugin-data/<id>/` under the config directory:

- `load() -> dict` — the plugin's JSON store (missing/corrupt → `{}`)
- `save(dict)` — atomic write (tmp + rename)
- `data_dir -> Path` — a private directory for anything bigger

Both the store and the directory are moved to `plugins/trash/plugin-data/`
when the user uninstalls the plugin.

## Window & settings-page specs

**`PluginWindowSpec(key, title, factory, default_geometry=(200,200,320,240),
command_key=None)`** — `key` must match the plugin-id pattern and be unique
within your plugin; declare it twice and only the first window is kept — the
second would share the first's `window_key`, so the app logs a warning and
drops it (tray entry, chat toggle and all).
`factory(wctx)` runs on the GUI thread and returns any widget with
`.toggle()`/`.isVisible()`; subclassing `nparseplus_sdk.ui.PluginWindow` is
the recommended way (overlay recipe + persistence for free — call
`self.restore_visibility()` last, and only such a window gets a
[Settings → Windows](../settings/windows.md#plugin-windows) row). The in-game
chat toggle is `toggle_<command_key>` (default `<id>_<key>`, with any
non-word character mapped to `_`). `title` is user-facing in three places:
the tray entry, the Settings → Windows row (prefixed with your `meta.name`),
and the window's own title bar.

`PluginWindow.__init__(wctx, *, translucent=True, default_state=None,
parent=None)` — the keyword arguments are passed through to
`OverlayWindowBase`; `self.window_context` holds the `wctx` you were given.

**`PluginWindowContext`** (the `wctx` your factory receives) — a dataclass
with six fields plus one extension point:

| Field | Meaning |
| --- | --- |
| `settings` | the host's pydantic `Settings` root |
| `window_key` | this window's canonical key, `plugin.<id>.<spec key>` — `Settings.windows[window_key]` is the state the user edits in Settings → Windows |
| `title` | the spec's title |
| `default_geometry` | the spec's `(x, y, w, h)` |
| `on_save` | call to request a settings save |
| `bridge` | the Qt bridge whose `event_received` / `events_batch` signals deliver bus events on the GUI thread (`None` outside the app) |
| `extras` | `dict[str, Any]`, empty today — a forward-compatibility slot; do not rely on any key |

**`PluginSettingsPageSpec(title, builder, apply=None)`** — `builder(parent)
-> QWidget` builds the page; `apply(widget)` runs on Settings
"Apply && Save". Both are individually guarded by the app.

## Host re-export modules (lazy)

These import the running app on first attribute access, so importing your
plugin stays possible in Qt-free/host-free environments:

- **`nparseplus_sdk.events`** — the typed event catalogue (`LineEvent` — the
  every-line firehose, `CommsEvent` + `CommsChannel`, `YouZonedEvent`,
  `DeathEvent`, `TimerWindowOpenedEvent` / `TimerWindowClosedEvent` — a pop
  window opening and closing, …), forwarded from `nparseplus.core.events`.
  Subscribe with the exact class.
- **`nparseplus_sdk.timers`** — `TimerRow`, `CounterRow`, `SpellRow`,
  `RollRow` and group constants, forwarded from `nparseplus.core.timers`.
- **`nparseplus_sdk.ui`** — `PluginWindow`, forwarded from
  `nparseplus.ui.pluginwindow` (needs PySide6; keep it out of your plugin's
  top-level module — see [Windows](developing.md#windows)).
- **`nparseplus_sdk.eqfiles`** *(SDK 1.2+)* — EQ install-file plumbing,
  forwarded from `nparseplus.core.eqini`: `preflight` (is this really an
  install?), `backup_once` (keeps only the *first* copy, so re-applying never
  overwrites the pristine original), `read_lines` / `write_lines` /
  `detect_newline`, and the ini section splice `section_bounds` /
  `section_body` / `replace_section` / `split_key_value`. The forwarded set is
  the explicit `EXPORTS` allowlist; anything else raises `AttributeError`.

Outside the app these raise `ImportError` with a message telling you to
install `nparseplus` from source.

## Testing & validation helpers

- **`nparseplus_sdk.testing.FakePluginContext(meta=None, *, app_version,
  sdk_version, storage, timers, player, eq_dir, eq_running)`** — records `subscriptions` /
  `parsers` / `ticks` / `windows` / `settings_pages` / `submitted`;
  `publish(event)` drives subscriptions by exact type, `run_submitted()`
  executes and clears queued fetch/apply pairs; fake `storage`
  (`FakeStorage`, with `.data` and `.save_count`), `speaker`
  (`.spoken`), and `pigparse` (`RecordingApi`, with `.calls`). `eq_dir` is
  whatever you pass; `eq_is_running()` returns the `eq_running` flag and never
  spawns a process.
- **`nparseplus_sdk.validate.validate_plugin(path, *, app_version=None) ->
  ValidationReport`** — the engine behind the `nparseplus-plugin validate`
  CLI. `app_version` is **keyword-only**. The report carries `ok`, `errors`,
  `warnings`, `meta`, and the registration counts (`window_count`,
  `page_count`, `parser_count`, `subscription_count`, `tick_count`).
  Warnings are advisory only and never affect `ok`. Note that validating
  **imports the plugin and calls `activate()`**.

## Other exports

`LineParser`, `LineInfoLike`, `Speaker`, `PluginStorage` (protocols),
`Unsubscribe` (type alias), `PLUGIN_ID_RE`.
