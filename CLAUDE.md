# CLAUDE.md — working on nparseplus

nParse+ is a Python 3.12 EverQuest P99 log-parsing overlay: a fork of
nomns/nparse rebuilt to feature parity with EQTool/PigParse (C#/WPF). If you
are porting or checking behavior, the C# source of truth is
github.com/smasherprog/eqtool at commit `d8e8084f` (see CREDITS.md) — clone it
somewhere disposable and read it; do not vendor C# into this repo. (The
generated data assets were converted from an earlier commit, `fdd3f25a` — the
`SOURCE_COMMIT` in each `tools/convert_*.py`; CREDITS.md explains both.)

## Commands

```bash
uv sync                                       # install (creates .venv); this is
                                              # a WORKSPACE — sdk/ syncs with it
uv run python -m nparseplus                   # run the app (tray + overlays)
uv run pytest                                 # full suite (~1460 tests, fast);
                                              # testpaths = tests + sdk/tests
QT_QPA_PLATFORM=offscreen uv run pytest       # headless; CI matrix does this
uv run ruff check . && uv run ruff format .   # lint/format (line length 100)
uv run pytest tests/core/parsers -q           # scope runs to one area
NPARSEPLUS_NO_PLUGINS=1 uv run python -m nparseplus   # safe mode: veto add-ons
```

To exercise the app without the game: point settings at a scratch dir with an
`eqlog_<Name>_<server>.txt` file and append timestamped lines to it —
`NPARSEPLUS_SETTINGS=/path/settings.json uv run python -m nparseplus`
(the env var overrides the settings.json location; see `nparseplus/app.py`).
Log line format: `[Wed Jul 15 12:00:00 2026] You begin casting Clarity.`
Note: your own group chat logs as `You tell your party, '...'` — not
"You tell the group".

## The architecture rule that matters

**`nparseplus.core`, `nparseplus.config`, and `nparseplus.net` never import
PySide6.** `tests/test_architecture.py` imports every module in those packages
with PySide6 poisoned and fails if anything pulls Qt. Domain logic goes in
core; only `nparseplus/ui/`, `app.py`, and the legacy `helpers/`+`parsers/`
packages may touch Qt.

## Data flow (one line)

`LogDriver` (worker thread, 100 ms poll of the newest `eqlog_*_*.txt`) →
`LogPipeline` (strip timestamp → ordered first-match-wins parser chain) →
typed Pydantic events on `EventBus` → handlers mutate services
(`TimersService`, `FightTracker`, trigger engine, …) → UI reads snapshots on
QTimers, and `ui/qtbridge.QtEventBridge` re-emits bus events as a queued Qt
signal.

**Thread crossings (only these):** the Qt bridge above (driver → GUI), and
network inbound: net-client/worker threads ONLY call
`SharingCoordinator.enqueue_inbound()`; the coordinator's driver-tick drains
the inbox and is the sole place inbound traffic touches the bus or timers.
The bus and TimersService are not thread-safe — never publish off-thread.

## Map of the code

```
src/nparseplus/
  composition.py        # THE wiring: build_backend() constructs everything in order
  app.py                # create_app(): backend + windows + tray; NPARSEPLUS_SETTINGS hook
  core/                 # Qt-free engine (see rule above)
    events.py           #   44 events, 1:1 with EQTool LogEvents.cs — extend, don't fork
    bus.py, pipeline.py, driver.py, logfile.py, lineinfo.py
    background.py       #   BackgroundJob: the one-at-a-time off-the-driver-
                        #   thread seam (log archive sweep, socials EQ probe)
    parsers/            #   one module per EQTool parser; registry.py fixes the order
    handlers/           #   bus subscribers (spawn timers, DPS, CH, pets, …)
    triggers/           #   Trigger model ({name}/{c}/{COUNTER} tokens), engine, builtin sync
    spells/             #   spells_us.txt loader (EQTool fixups), matching, durations
    timers.py           #   TimersService rows (SpellRow/TimerRow/CounterRow/RollRow)
    dps.py              #   FightTracker (12s trailing window, session stats, >20s gate)
    zones.py            #   ZoneDatabase over data/zones.json (respawn lookup order)
    ch_chain.py, death_loop.py, pets.py, npc_search.py, boats.py
    dumps/              #   character dump library: /outputfile inventory +
                        #   spellbook snapshots kept per (character, kind).
                        #   models.py (CharacterDump + digest/diff), parse.py
                        #   (the two file shapes; reuses inventory.py's
                        #   parser), store.py (DumpLibrary), watcher.py (THE
                        #   single EQ-dir poll — publishes CharacterDump*
                        #   events and feeds handlers/inventory_upload.py)
    sharing.py          #   SharingCoordinator: THE sharing gate + inbound thread crossing
    visionfix.py        #   Night Vision fix apply/revert (backup-first)
    pigparse.py         #   Qt-free Protocol for the REST client + SubmitFn
    plugins/            #   add-on host (OPT-IN, off by default): discovery.py
                        #   (plugins dir + entry points), host.py (LoadedPlugin
                        #   status machine + consent), context.py (the SDK
                        #   PluginContext impl + unwind), install.py (zip-slip-
                        #   safe installer, https-only, sha256), registry.py
                        #   (index schema + client: resolve_registries
                        #   synthesizes the built-in row, fetch_indexes fans
                        #   out over every enabled one, MergedListing carries
                        #   provenance), storage.py (per-plugin JSON)
  pluginbootstrap.py    # THE two gated plugin import sites create_app may use
                        # (start_plugins pre-Qt, build_plugin_ui post-windows);
                        # nothing plugin-related imports while plugins are off
  config/               # Pydantic Settings -> platformdirs settings.json (+ legacy migration)
  net/                  # Qt-free network clients (UI marshals results itself):
                        #   p99wiki.py, pigparse_models.py (wire DTOs, camelCase-in/
                        #   PascalCase-out, THE axis swap), pigparse_api.py (REST),
                        #   hubproto.py + pigparse_hub.py (minimal SignalR JSON hub
                        #   client; signalrcore is probe-only — its sends are broken),
                        #   nparse_ws.py (legacy locationserver wire), worker.py
  updater.py            # GitHub releases check (prokopto-dev/nparse-plus);
                        # Linux picks .flatpak vs .tar.gz via /.flatpak-info
  crashguard.py         # excepthooks -> crash.log; run_app also mirrors the
                        # nparseplus logger tree to nparseplus.log (both in
                        # platformdirs user_log_dir — frozen stderr is invisible,
                        # so check these first on any crash/connection report)
  ui/                   # PySide6 windows; overlaybase.py is the shared overlay recipe
                        #   theme.py (Palette: the one set of colour VALUES)
                        #   + chrome.py (config-surface tokens + the window
                        #   and app stylesheets; Qt-free) + chromewidgets.py
                        #   (its Qt half: label factories, ChromeMixin,
                        #   the Fusion QPalette) + skins.py
                        #   (Skin: the overlay chrome — duxa/velious/ledger —
                        #   Qt-free data + pure stylesheet builders, applies LIVE),
                        #   skinwidgets.py (what QSS can't express: the notched
                        #   plate, the full-row bar, the gem mark, previews),
                        #   dumpswindow.py (Character Dumps: the library
                        #   browser + its auto-import/auto-update toggles;
                        #   never imports on the GUI thread — it asks
                        #   DumpWatcher and refreshes off a QTimer),
                        #   pluginmanager.py (Settings > Plugins + registry browser),
                        #   pluginconsent.py (the one-time approval dialog),
                        #   pluginwindow.py (the PluginWindow base plugins subclass)
  audio/tts.py          # Speaker protocol: macOS `say`, PowerShell, espeak, Null
  data/                 # generated/ported data — regenerate via tools/, never hand-edit JSON
  helpers/, parsers/    # LEGACY nparse code (maps + discord windows) — see below
                        #   parsers/maps/chrome.py is NEW code in the legacy
                        #   tree: the map's summoned surfaces (header, toolbar,
                        #   rail, recenter puck, edge tabs) + pure geometry
sdk/                    # uv WORKSPACE MEMBER: nparseplus-sdk, the stable third-party
                        # plugin contract. Versioned + released independently of the
                        # app (sdk-v* tags -> PyPI); __init__.py's exports are public
                        # API under an additive-only 1.x promise. sdk/tests runs in
                        # the root pytest. See CONTRIBUTING.md.
examples/plugins/       # reference add-ons (hello_timer.py, merchant_prices/);
                        # tests/core/plugins/test_examples.py keeps them loading
templates/              # plugin-repo/ = ready-to-push content of the plugin
                        #   template repo (not created yet; see TEMPLATE_SETUP.md).
                        #   registry-repo/ = the mirror of the LIVE curated index
                        #   repo, kept here because tools/gen_registry_schema.py
                        #   --check guards its schema against drift (see SETUP.md)
tools/                  # one-shot converters (Zones.cs -> zones.json etc.); outputs committed
tests/                  # pytest; tests/fixtures = EQtoolsTests golden corpus
```

## Porting conventions (EQTool -> here)

- Parsers/handlers are **1:1 ports**: same regexes (EQ names allow backticks
  and spaces: `[\w\` ]+`), same quirks (e.g. mend-wounds never consumes its
  line). When behavior is ambiguous, the `EQtoolsTests` C# tests are the spec;
  port the test first.
- Events must match `EventModels.cs` shapes. Timestamps are **naive local
  datetimes** everywhere (the log's own clock) — never introduce tz-aware
  values (it broke once; the whole pipeline compares naive).
- Data changes go through `tools/convert_*.py` regeneration, not JSON edits.
- Deliberate divergences from the C# get a comment saying so and why.

## Legacy zone (transitional — don't modernize casually)

`helpers/` and `parsers/` are original nparse code (maps + discord windows,
`ParserWindow` base, legacy `nparse.config.json` in the CWD). The app runs
"backend mode": `helpers/application.py` receives LineEvents from the bridge
instead of its old file watcher. Maps still read the legacy config; the new
Pydantic Settings drives everything else. This dual-config state ends when the
maps window is rebuilt. Ruff grandfathers these dirs (per-file-ignores in
pyproject.toml) — keep NEW code clean even when touching them.

## Gotchas

- The app needs CWD at repo root for `data/` and the legacy config;
  `app._ensure_data_cwd()` handles it — don't add more CWD-relative paths.
- `settings.json` lives in `platformdirs.user_config_dir("nparseplus")`, not
  the repo. Tests never touch the real one (pass `settings_file=`/tmp paths).
- Overlay windows: Qt.Tool windows hide on app deactivation on macOS — the
  event overlay sets `WA_MacAlwaysShowToolWindow`. Keep that on anything that
  must stay visible while the game has focus.
- The spell DB path: user's EQ install `spells_us.txt` if configured, else the
  bundled `data/spells/spells_us.txt`. The tests use the pinned fixture at
  `tests/fixtures/spells_us.txt` — don't swap it.
- Background app processes started by tooling get reaped between commands —
  use `nohup ... & disown` when you need a demo instance to survive.
- Git: commit with `git -c core.hooksPath=/dev/null commit` (hook friction),
  imperative messages prefixed by milestone (`M2: ...`).

## Packaging

`uv run pyinstaller packaging/nparseplus.spec --noconfirm` builds the onedir
.app (both data roots land under `_MEIPASS`; `_ensure_data_cwd` chdirs there
when frozen and the legacy config moves to platformdirs). Then ad-hoc
`codesign` + `uv run dmgbuild -s packaging/dmg_settings.py`. Tag `v<X.Y.Z>`
(must equal `__version__` AND pyproject) to cut a GitHub release via
`.github/workflows/release.yml`. Release flow since 1.11: **merge to master
auto-runs the Semantic Release workflow** (it also still supports manual
`gh workflow run semantic-release.yml` / local `uv run semantic-release
version`). `feat:` = minor, `fix:`/`perf:` = patch, `chore`/`ci`/`docs` don't
bump (docs/chore merges no-op but still build); it bumps both version files,
tags, and dispatches release.yml. Every PR's commits are gated against the
allowed Conventional-Commit types by `pr-commit-check.yml` +
`tools/check_conventional_commits.py` (types read from
`[tool.semantic_release.commit_parser_options]`); merge with a **merge commit**,
not squash, so those commits survive for versioning. The Linux job also wraps the onedir
build into a `.flatpak` bundle (`packaging/flatpak/` manifest; Linux-only to
build — CI does it, don't try locally on macOS), built with `--repo-url` and
publishes the OSTree repo to the single-commit `gh-pages` branch (GitHub
Pages) so `flatpak update` works for bundle installs.

**Flatpak `finish-args` are release-notes-worthy, permanently.** Flatpak
refuses any in-app/portal update whose new version asks for a permission the
installed version does not have — `UpdateMonitor.Update` fails with
`org.freedesktop.DBus.Error.NotSupported` and the user has to run the host
tools. So **every** addition to `finish-args` breaks in-app update across
exactly that one release hop, and a new permission must ship one release
before the code that needs it.

**The portal update path (issue #74) needs no new permission, and the one
that looks like it does is a sandbox escape.** `org.freedesktop.Flatpak` is
NOT the portal — it is `flatpak-session-helper`, whose
`org.freedesktop.Flatpak.Development.HostCommand` is the method behind
`flatpak-spawn --host`, so a `--talk-name` for it grants arbitrary command
execution on the host to anything in-process (a third-party plugin included).
The portal that owns `CreateUpdateMonitor`/`Update`/`Spawn` answers on
`org.freedesktop.portal.Flatpak` — bus name and interface name are the same
string, `portal/org.freedesktop.portal.Flatpak.service.in` — and
`common/flatpak-run-dbus.c` already grants every sandboxed app
`--call=org.freedesktop.portal.*=*` plus broadcasts under
`/org/freedesktop/portal/*`, where the UpdateMonitor object lives. If a
binding ever needs more than `--call`, the correct arg is
`--talk-name=org.freedesktop.portal.Flatpak`, scoped to the portal. The
manifest carries this as a comment so nobody re-adds the wrong one.

Update downloads are verified (issue #73): `updater.stream_https_to_file` is
the streaming sibling of the plugin installer's `fetch_https_bytes` — same
hop-by-hop https re-assertion, its own `MAX_ASSET_BYTES` budget (release
artifacts are 195–255 MB; do not raise the plugin's 50 MiB in-memory cap to
meet them), rolling sha256 to a `.part` staging file, promoted by rename only
once it matches `assets[].digest`. That digest travels over the same TLS
session as the release metadata, so it is a **channel** guarantee — it
defends against a corrupted, truncated or substituted CDN object and against
nothing that can publish a release. A signed `SHA256SUMS` (minisign, public
key compiled in) is the only actual signature and is still to do.

Since 1.18 the spec also `copy_metadata`s BOTH distributions (`nparseplus`
and `nparseplus-sdk`) — a frozen app has no site-packages, so a plugin's
`importlib.metadata.version(...)` would raise without it — and declares the
`nparseplus_sdk.*` submodules plus `nparseplus.ui.pluginwindow` as
hiddenimports (only plugins import them, so PyInstaller can't trace them).
The app's own `__version__`/`SDK_VERSION` stay plain literals precisely
because a metadata lookup fails exactly here.

**The SDK releases on its own axis**: semantic-release owns `v*` (app) tags
only; `nparseplus-sdk` publishes to PyPI from a hand-pushed `sdk-v<X.Y.Z>`
tag via `.github/workflows/release-sdk.yml` (PyPI Trusted Publishing/OIDC,
no token; the job refuses a tag that disagrees with
`sdk/src/nparseplus_sdk/__init__.py`). Release procedure and pypi.org setup
in CONTRIBUTING.md ("Working on the SDK").

Generated-artifact convention beyond `tools/convert_*.py`:
`tools/gen_registry_schema.py` derives
`templates/registry-repo/schema/index-v1.schema.json` from the live pydantic
models in `core/plugins/registry.py` (re-applying the constraints that live
in `field_validator`s and JSON Schema can't express). Output is committed;
`--check` fails on staleness and `tests/core/plugins/test_registry_schema.py`
runs it.

## Sharing wire cheatsheet (see tools/pigparse_probe_transcript.md)

- Inbound JSON is camelCase; send PascalCase (EQTool-compatible, servers
  bind case-insensitively). Enums as ints (`core.enums` wire ordinals).
- The wire carries raw `/loc` print order: wire X = `Loc.y`, wire Y =
  `Loc.x` (`wire_player_from_loc` owns it); the map plots `(-y, -x)` of
  wire order. Don't "fix" either without live calibration.
- Cadence courtesy (never exceed): send per `/loc` + 10 s keepalive, stop
  after 5 idle minutes or camp; 15 s Kael throttle; 4 s roar dedupe.
- You only receive a zone's players after SENDING a location with that
  zone; the hub echoes your own frames back (coordinator self-filters).

## Tracking future work

Pending features and dev tasks live in **GitHub Issues**
(github.com/prokopto-dev/nparse-plus/issues), labelled by `area/*` (triggers,
timers, overlays, maps, net, dps, data, packaging, docs, infra), `type/*`
(feature, bug, perf, refactor, tech-debt, test, parity), and `size/*` (S/M/L).
`docs/roadmap.md` is the public prose projection; the issues are the live
tracker. The "Where things stand" narrative below is shipped-history
orientation, not the pending-work list.

Agentic loop: list open issues by `area/`+`size/`, pick one, set it in-progress
(assign / comment), implement to its acceptance criteria (add a test), close it
on ship referencing the commit/PR. New deferred work opens an issue **before**
any code marker — `# TODO(#NN)` comments must resolve to a real issue number
(e.g. `TODO(#5)`), never a bare `TODO`.

## Where things stand

**M0–M6 complete, v1.4.1 released**: full EQTool parity including the network —
live PigParse hub interop (map dots, shared timers, quake/boat/roll feeds,
mob-info loot pricing), the self-hostable nparse websocket mode, Night
Vision fix, self-updater, PyInstaller .app/DMG + release CI. M4 (1.1) added
the unified `ui/settingswindow.py` "nParse+ Settings" window — it DUAL-
WRITES the Pydantic Settings and the legacy config.data (the only place
that bridges both), per-window opacity/on-top, per-character profiles
(PlayerProfileHandler syncs PlayerInfo <-> ActivePlayer), spell class
filters (`hide_spell`), the best_guess_spells toggle, EQTool-style drawn
map markers + tracking radius, and spell gem icons. M5 closed the last
parity gaps: /who roster + PigParse upserts (`core/handlers/
player_tracker.py`), pigparse.org Discord login (`net/discordauth.py`,
token in `settings.pigparse_account` — never log it) + inventory watcher
(`core/inventory.py`), friends ini sync (`core/friends.py`), map label
scale + tunable z-fade (legacy `maps` keys), raid_mode_auto wiring.
`git log --oneline` narrates the build milestone by milestone.

**1.7 batch** (post-1.6.1): four UX features. `/random` rolls now sort
highest-first within their group regardless of the spell-window sort mode
(`ui/spellwindow.py` `row_sort_key` special-cases `RollRow`). Windows that
were open at quit reopen on launch — a Qt-free `ui/appquit.py` quit flag plus
`QCoreApplication.closingDown()` stops overlay/spell/legacy `closeEvent`
paths from clobbering `shown`/`toggled` on Cmd+Q. The event overlay's
position mode now renders sample CH-lane/alert/timer-bar preview widgets (no
live-state leakage) and supports per-region drag repositioning, persisted via
`WindowState.overlay_regions` (`None` = legacy stacked layout, no migration).
Triggers gained named groups: `ui/triggereditor.py` group combo +
context-menu Move/Rename/New group (built-ins read-only), and GINA imports
map the full nested folder path to `category` ("Raid Pack / Sebilis") instead
of just the top folder.

**1.8 batch** (post-1.7.0): three window/UX features. The trigger editor can
delete a whole user group: `ui/triggereditor.py` `delete_group()` +
context-menu "Delete group…" with a QMessageBox confirm (refuses folders
holding a built-in). Frameless overlays are now resizable from any edge or
corner, not just the one grip — `ui/overlaybase.py` grows pure `edge_at`/
`cursor_for_edges` helpers and an `EdgeResizeMixin` (margin-band hit-test →
`startSystemResize` + hover resize cursors), keeps the transparent
`QSizeGrip`, and debounce-persists geometry (guarded so startup restore never
rewrites settings). The spell-timer window (the headline complaint — only one
resizable corner before) gains the mixin; DPS/mob-info get sensible minimums
and the event overlay's lanes-host min width drops 520→200 so the overlay can
narrow (lanes clip). A "Reset Window Positions" tray action
(`ui/windowlayouts.py` `reset_onscreen()` + pure `clamp_rect_to_screen`)
clamps every managed window back onto a visible screen (manual only; never
un-hides).

**1.9 batch** (post-1.8.0): TTS reaches full voice parity across platforms.
`audio/tts.py` `list_voices()` now enumerates per-platform and returns typed
`VoiceInfo(id, label, engine)` records (stays Qt-free): macOS parses `say
-v ?`, Windows enumerates BOTH classic SAPI (`GetInstalledVoices`) and the
modern WinRT "Natural" voices (`AllVoices`) via one PowerShell call — ids are
engine-prefixed (`sapi:`/`winrt:`) so `WindowsSpeaker._command` picks the
System.Speech path or the WinRT synthesize-to-WAV + SoundPlayer path (which
falls back to System.Speech on any error); Linux parses `espeak-ng --voices`,
and the Flatpak now bundles `espeak-ng` (1.52.0) + `pcaudiolib` modules so
sandboxed TTS works out of the box. The settings voice combo stores
`VoiceInfo.id` in userData and the picker/test/apply all key off it. Voice and
volume changes apply live without a restart: `composition._SwappableSpeaker`
is one holder shared by the trigger engine and every audio handler, and
`Backend.rebuild_speaker` (wired to the settings window's `on_audio_changed`
in `app.py`, fired on Apply only when voice/volume changed) swaps its delegate
in place and closes the old speaker. NOTE: the WinRT speak path is asserted
only at command-string level in CI — it needs manual Windows verification.

**1.11 batch** (post-1.10.0, ~970 tests): four roadmap features + a version
indicator. Raid-mode grouping returns strictly opt-in with per-row
orientation — `core/timers.py` `group_rows_for_display()` is a pure,
stateless recompute of section headers/orientation from the current rows
(killing the old global-flag stuck-header desync); `spellwindow.refresh`
consumes it; setting `raid_group_by_spell`. Post-expiry spell alerts: a
`SpellRow` with `post_expiry_persist_s > 0` lingers past `ends_at` as a
flashing rebuff prompt (`tick()` stamps `expired_at`; left-click dismisses;
per-spell allowlist toggled from the row's context menu; `post_expiry_flash_*`
settings). CH cadence indicator: `ch_chain.parse_ch_cadence` +
`CompleteHealCadenceEvent` draw a muted next-cast marker in the CH lane when
the raid leader calls "healers to N" (`ch_cadence_indicator`, opt-in). A
dedicated event-overlay Utility header section (`OverlayEvent.section` /
`TriggerOutput.overlay_section`) hosts two nParse+ built-ins (Rebuff Request,
Out of Mana; converter bumped 65→67). Settings > General now surfaces the app
version + an up-to-date/update-available badge + Check now (previously
tray-only).

**1.18 batch** (post-1.17.0, ~1460 tests): the add-on system — **opt-in and
OFF by default**, and that framing is the feature. `settings.plugins.enabled`
is False; a user ticks Settings > Advanced "Enable plugins (add-ons)" and
restarts, and only then does a Plugins page and an "Open Plugins Folder" tray
entry exist. The gate is structural, not cosmetic: `pluginbootstrap.py` holds
the ONLY two plugin import sites `create_app` may use (`start_plugins`
pre-Qt/pre-driver so subscriptions+parsers+ticks register race-free,
`build_plugin_ui` post-windows), so with plugins off the host, the SDK, the
installer and httpx/zipfile are never imported —
`tests/core/plugins/test_master_toggle.py` checks that structurally.
`config.settings.plugins_enabled()` also honours `NPARSEPLUS_NO_PLUGINS=1`,
a veto that can force plugins off and never on (safe mode for an add-on that
breaks startup). `sdk/` is a uv workspace member, `nparseplus-sdk` 1.0.0 —
the third-party contract (`NParsePlugin`/`PluginMeta`/`PluginContext`, lazy
`events`/`ui` re-exports of the host, `FakePluginContext`, `validate_plugin`
+ the `nparseplus-plugin` CLI), versioned and PyPI-released independently
(`sdk-v*` → `release-sdk.yml`); everything in its `__all__` is public API
under an additive-only 1.x promise, enforced at load by `check_compat`
(`requires_sdk` + `min_app_version`). `core/plugins/` is the Qt-free host:
`discovery.py` (plugins dir first, then `nparseplus.plugins` entry points —
the dir is what works in frozen builds), `host.py`'s status machine
(ready/active/disabled/pending_consent/incompatible/error/duplicate, every
failure isolated per plugin), consent persisted per id in
`Settings.plugins.entries` and **forgotten on uninstall together with
`plugin-data/<id>`** so a later plugin claiming that id is treated as the
stranger it is, `context.py` (the SDK context impl + `unwind()` of partial
registrations), `install.py` (zip-slip-safe member screen, staging +
`validate_plugin` gate before the move, https re-asserted on every redirect
hop, sha256 pinning, uninstall-to-`trash/`), `registry.py` (curated static
index; `DEFAULT_REGISTRY_URL` points at the `nparseplus-plugins` Pages repo,
live since 1.18 and serving an empty schema-1 index; Browse degrades to a
"could not reach" status). `core/driver.py` grew `add_supervised_tick`: plugin
ticks are
timed against `TICK_BUDGET_S` (0.25 s) and evicted after
`TICK_BREACH_LIMIT` (2) consecutive breaches — the plugin stays active and
the manager annotates its row "tick disabled (too slow)"; app-owned ticks
are appended to `on_tick` directly and never timed. Qt side:
`ui/pluginmanager.py` (the page + `RegistryBrowserDialog`, installs on a
worker thread because validation *imports and activates* the candidate, and
a Source provenance column that says "Sideloaded" out loud),
`ui/pluginconsent.py`, `ui/pluginwindow.py`, plus the settings window's
`extra_pages` seam. Known v1 limits, both documented: consent gates
activation but not import (a declarative manifest is the fix), and nothing
hot-loads — install/uninstall/toggle all apply next launch (TODO(#45)).

**Multiple plugin registries** (post-1.18, ~1537 tests): the single
`plugins.registry_url` override is gone — settings now carry
`plugins.registries: list[RegistrySource]` (**user-added only**) plus
`plugins.default_registry_enabled`, and a `PluginsSettings` model_validator
folds any legacy value into the list, normalizes (lower-case scheme+host,
path untouched), dedupes, and *drops* unusable rows instead of raising
(`load_settings` reads a ValueError as a corrupt document and would discard
every other setting). The built-in registry is **never persisted**:
`registry.resolve_registries` synthesizes it from `DEFAULT_REGISTRY_URL` on
every read, so it can be unticked but never removed or edited (guarded twice
— disabled button + `PluginHost.remove_registry` refusal) and changing that
constant later moves every user instead of stranding them. `fetch_indexes`
fans out over the enabled registries on a ThreadPoolExecutor and returns a
`MultiFetchResult` of per-registry `RegistryFetchResult`s placed by index
(deterministic order; a dead registry costs its timeout in parallel, not in
front of a live one), with a pure `summary_lines()` so the dialog stays a
renderer and one failure annotates rather than blanks the table. Provenance
is the `MergedListing(registry, plugin)` wrapper, NOT a field on
`RegistryPlugin` — an index cannot truthfully say who served it (a mirror
would copy the claim) and the wire models generate the registry repo's
committed JSON Schema. `PluginEntry.registry_url` records which registry
vouched for an install; `best_update` now compat-filters candidates (it
didn't — it could offer a release `check_compat` then refused) and prefers
the registry a plugin was installed from, the manager saying "from <name>
registry" when the offer comes from elsewhere and Browse showing a disabled
"Installed (other source)" instead of an Install button. UI: `ui/
pluginregistries.py` (Enabled/Name/URL table, Add…/Remove, `REGISTRY_WARNING`
+ `CONSENT_WARNING` in a defaults-to-Cancel confirmation — a registry
supplies both the artifact URL and the sha256 it is checked against, so
adding one is a wider trust decision than installing a plugin), and a merged
Browse with a Source column marking third-party rows, duplicate-id
annotations, and a Refresh button. Docs: docs/plugins/registry.md "Using
another registry" is the canonical trust argument; security.md and
docs/settings/plugins.md point at it.

**In-app plugin updates** (post-1.20, ~1653 tests): closes #51 — the app
could *detect* an update and had no way to *take* one, and the only
workaround (uninstall + reinstall) went through `PluginHost.forget`, which
drops consent and trashes `plugin-data/<id>` by design. `install.py` grows
`ReplaceTarget` + a `replace=` kwarg on all three entry points: same
pipeline, already-installed refusal inverted, two identity gates at
different points because they have to be — the archive root must equal the
installed path (before extraction; the install path comes from the archive
root, never `meta.id`, so a renamed root would install a *second* copy under
one id) and the validated `meta.id` must equal the plugin being replaced
(after `validate_plugin`, the earliest identity exists). The swap goes
through a private `.install-backup` sibling and only reaches `trash/` once
the new copy is in place, so a failure rolls straight back. **Consent and
plugin-data are untouched** — that is the feature. `core/plugins/
updatecheck.py` is the Qt-free decision layer: `best_update` stays the one
decision function and this adds the fan-out plus provenance classification
(`same_source`/`unknown_provenance`/`needs_confirmation`), plus
`listing_action` which decides per *browser row* (one row per
registry×listing, so a v1.0 row can't advertise another registry's v2.0).
SDK 1.1 adds `PluginMeta.update_url`, a self-published index letting a
plugin distributed outside any registry be updated: joins the same
`MergedListing` pipeline via `ResolvedRegistry.kind="self"`, may only ever
offer its own id (impersonation guard), never enters Browse, never outranks
the registry a plugin was installed from — but *does* count as same-source
for a copy nothing vouched for, or `update_url` would be permanently
two-click for its own use case. Cache lives on `PluginHost` (session-only:
the page is rebuilt per settings-window open, the host outlives them), fed
by `pluginbootstrap.schedule_update_check` ~12 s after launch behind
`plugins.update_check` (default on) — quiet, no popup, no tray balloon, and
a declined plugin's feed is never contacted. UI: per-row Update, Update all
(same-source only, serial on the one install worker since staging and the
download temp file are fixed paths), Check for updates, and Browse offering
"Update to vX" where it used to disable "Installed" regardless of version.
A cross-source offer stays a live button behind a confirmation naming both
ends — refusing outright left no path but the lossy one. Also fixed en
route: `_preserved_plugin_modules` stops `validate_plugin` repointing a live
plugin's `sys.modules` entry into the deleted staging dir (reachable today
via a same-stem install), and #52 — `loading.py` now registers the
`nparseplus_user_plugins` parent so `from . import helper` works, not just
`from .helper import x`.

**The visual redesign** (post-1.21, ~1750 tests): the app got a look. Four
pieces, all UI — no parser, event, handler or wire change.

*Skins.* `ui/skins.py` is the second half of `ui/theme.py`: where a
`Palette` answers "what color is body text", a `Skin` answers "what does
the window's edge look like, how loud is the title, is the bar a rule under
the row or the row's own background". Three ship — **duxa** (thin
double-line frame, black glass, tan caps; the default, and what a
DuxaUI-skinned P99 client already looks like), **velious** (beveled stone
plate, notched corners, gem sockets, engraved gold), **ledger** (Duxa
frame, full-row draining bars) — driven by one `general.skin`. Qt-free like
theme.py: data plus PURE stylesheet builders, so the whole layer is
testable without a window; the three things QSS cannot express (the corner
notch, the full-row bar behind its own labels, the gem mark) live in
`ui/skinwidgets.py` as painters. Unlike the theme a skin applies **live** —
`app._apply_appearance` is the single landing point, called by both the
tray's UI Skin submenu and the Settings picker's live preview, and every
skinned window grows an `apply_skin()` that re-dresses in place. Sizes are
multipliers of `general.font_size`, never px, so the user's font choice
keeps working. Typography resolves to the bundled Noto Sans family across
all skins; compact display roles share the tracked uppercase treatment while
body rows and values stay plain. `frame_opacity` fades ONLY the plate and
glass — the split from window opacity (which fades the countdowns too) is
the point.

*Appearance page.* A new Settings page between General and Character:
three live-previewing skin cards, theme and `general.font_size` (both moved
off General), alert-headline size, alert emphasis, alert text shadow (moved
off Audio & Overlays), frame opacity. The base font control is explicitly
the UI/overlay size and applies to open overlays through
`app._apply_appearance`; `overlay_text_size` remains only the large event-alert
headline. The picker previews by mutating `general.skin` and calling the
appearance callback, so **Close reverts** and Apply re-baselines —
`_skin_on_open` owns that, re-read on every `showEvent` because the tray can
change the skin while the window is hidden.

*Event overlay.* `split_alert_text` splits "Gorenaire — ENRAGED" into a
tracked-out kicker and the big word — presentation only: `current_text()`
and the reset match still use the whole string (`_alert_text`). Emphasis
(plain / pulse / pulse+glow) is a stylesheet swap on a timer, NOT a
graphics effect, because the label already carries the shadow effect and a
widget gets one. Bars stay `QProgressBar`s (the chunk is what drains) but
their text is two child labels, name-left/time-right — read them via
`bar_countdown_text`, not `format()`. The alert panel (kicker, headline and
rule) is always centered inside its configured Alerts region, regardless of
skin; this exception does not change alignment in the other overlay windows.

*Maps.* Backdrop opacity is now its own number: `MapCanvas` paints a scene
`backgroundBrush` with its own alpha and the `#MapCanvas` opaque black went
away, so the map can be glass while its geometry stays full-contrast
(`apply_backdrop_opacity` displays, `set_backdrop_opacity` also adopts —
the idle fade needs to drop to 0 without forgetting the user's value). A
wheel inside the edge band nudges it instead of zooming. The single-glyph
button strip is gone: `parsers/maps/chrome.py` holds a hover-revealed
header (zone, loc chip + age, Z badge, exit chips, find/rail) and labelled
toolbar, edge tabs naming each exit on the border you would leave through
(up only while the header is down), a recenter puck that lights with
bearing + distance once you pan off yourself, a Tab rail showing what the
zone actually has, and a Ctrl+F find palette (an empty one lists the zone's
notables — that was the old ☰ NPCs button). The interesting parts are pure
functions (`bearing_index`, `edge_anchor`, `zone_line_label`,
`place_chrome`) and tested as such.

**The chrome layer** (post-redesign, ~1850 tests): the skins work dressed the
overlays and stopped; Settings, the editors, the plugin manager and every
dialog were bare Qt defaults with sixteen copies of `color: #888888` that
never flipped with the theme. `ui/chrome.py` is the second half of
`ui/skins.py` for the windows you *configure* the app with — Qt-free, data
plus pure stylesheet builders, with `ui/chromewidgets.py` as its Qt half
(label factories, `ChromeMixin`, the Fusion `QPalette`).

The rule it rests on: **the palette owns VALUE, the skin owns HUE.** Ground,
field backgrounds and body text always come from `theme.palette()` — the
readability floor a skin cannot move — while the skin contributes one accent
(selection bands, focus rings, group titles, hairlines). That is why Velious
tints Settings without ever producing gold-on-gold, and it is asserted
directly rather than left as intent.

**Two stylesheets, never one.** `chrome.window_style` is set per window and
may carry bare type selectors; `chrome.app_stylesheet` is set on the
QApplication and may carry ONLY `#Id` selectors plus `APP_SCOPE_ALLOWLIST`
(QMenu/QToolTip, which are top-level windows a window sheet cannot reach). A
bare type selector at app scope lands on the overlays over EverQuest —
`skins.overlay_window_style` only overrides three properties on QLabel and Qt
resolves conflicts per-property, so anything it does not name leaks through.
`test_app_stylesheet_uses_only_id_selectors_and_the_allowlist` parses every
selector and is the guard on that; do not weaken it.

Rule ORDER inside `window_style` is load-bearing too: `QWidget` and
`QLineEdit` match a QLineEdit with equal specificity, so the ground rule must
come first or every text field becomes the page background.

Two traps worth knowing. A stray brace makes Qt **discard the entire sheet**
with only a runtime warning, so the window renders undressed while every
string-level test passes — `test_qt_parses_the_window_sheet` installs a Qt
message handler and asks Qt itself. And `app.setStyle("Fusion")` +
`qt_palette_spec` carries most control internals but NOT checkbox indicators:
Fusion draws those from a palette whose Base is near-black, so they must be
styled explicitly or an unchecked box is invisible.

**The light theme is gone** (`general.theme` removed; unknown keys are ignored
so old settings.json files still load). One palette, so `Skin.resolved()` and
`raw_skin()` are gone with it, and `data/ui/*.css` is deleted — of _.css's 217
lines only `#ParserWindow*` was live, and that is generated now.

Also landed: `skinwidgets.SkinTitleBar` (was copy-pasted in three overlays);
the trigger and macro editors on `OverlayWindowBase`, which **fixes their
dead Settings > Windows rows** — that page gates on `apply_window_state`,
which a raw QWidget never had — with `toggle()`, the mouse handlers and
`restore_visibility` deliberately overridden (see the commit); the map chrome
deriving `MapColors` from the skin plus a `show_zone_lines` toggle; Discord's
strip skinned; and the event overlay's alert now centered under every skin
and shrink-to-fit rather than clipping. The three `resolve_color` utility
lines keep their user-configured colour on purpose — only their size is the
skin's.

**The DPS meter counts differently** (post-2.2): three changes, one page.

*Rows stopped vanishing.* Staleness now belongs to `Fight`, not
`FightEntity` — EQTool's `ShouldRemove` aged out each `EntittyDPS` on its
own last hit, so an opener who stunned and switched to healing dropped off
the list 40s later while the mob was still up. A group is keyed on the most
recent hit from ANY attacker and retires as a unit; nothing is pruned out
from under a live fight. `Fight.is_stale(now, retention_s)` also lost the
old `abs()`, which read a log line stamped ahead of the wall clock as stale.

*Melee only, by default.* `"<target> was hit by non-melee"` names no
attacker, so `DamageParser` can only credit **You** — including other
players' nukes, which inflated your row and opened fights on mobs you never
swung at. `FightTracker.melee_only` (default on) drops non-melee before it
can open a fight. The filter lives in the TRACKER, not the parser: the
parser stays the record of what the log said, so triggers and plugins
subscribed to `DamageEvent` still see spell damage. `core/damagetypes.py` is
the shared vocabulary — its own module because `core/parsers/damage.py`
builds its four regexes from the verb list and `core.dps` filters by it, and
importing the parser to reach a frozenset costs ~100 ms (it loads the master
NPC list) on a module the UI imports. `is_melee` reads UNKNOWN types as not
melee on purpose, so a damage line the parser learns later (DoT ticks are
still unparsed) can't silently join a melee-only meter.

*Settings > DPS Meter.* The four tunables are plain attributes on the
tracker, not constructor-only, because the app builds one tracker per launch
and it outlives every settings window: `Backend.apply_dps_settings()` →
`FightTracker.configure()` on Apply, no restart. `trailing_window` is the
one that needs more than an assignment — it is carried per entity and
re-stamped by `tick()`, which is what reaches fights already running, and
changing it zeroes `best_window_damage` since a best-in-6s is not comparable
to a best-in-12s and the max-merge would otherwise keep the stale larger
number forever. Damage already counted is never re-filtered (the hit list
does not keep damage types). The >20s session-footer gate stayed, now as a
knob: it is why Best/Now/Last reads 0 all session on trash that dies faster.

**The settings window shrinks** (post-2.1): a `QStackedWidget`'s minimum is
its *widest page*, so one wide row on Sharing (a long label beside a combo
listing "pigparse.org character page") pinned the whole window at ~550px and
grew that floor with every point of `general.font_size`. Two halves fix it
and both are needed: `_scrollable()` puts every page in a `QScrollArea`, and
`_let_rows_wrap()` sets `WrapLongRows` on **every** `QFormLayout` in the
window — swept in one place, after construction, so the forms nested inside
group boxes (the widest rows here) cannot forget it. **`extra_pages` go
through the wrapper too**: the widest page in the app is a contributed one
(the Plugins manager's table, ~1800px), so leaving them out would have left
the window pinned for exactly the users who enabled plugins. The wrapper
goes in the stack while `_extra_pages` keeps the builder's own widget, so
`spec.apply` never sees a `QScrollArea` it didn't make. Without the wrap a narrow window just scrolls sideways past its
own labels. `MIN_SIZE` is the stated floor; the invariant that keeps it real
— and what the test asserts — is that Qt's own layout minimum stays *under*
it, because the larger of the two is what wins.

**The character dump library** (post-2.0, ~1906 tests): P99's
`/outputfile inventory` and `/outputfile spellbook` write
`<Character>-Inventory.txt` / `<Character>-Spellbook.txt` into the EQ
directory and **overwrite them every run** — one copy each, no history.
`core/dumps/` keeps copies, keyed by **(character, kind)**, so every
character holds its own current inventory AND its own current spellbook with
the previous N behind each (`keep_per_character`, default 10). Two file
shapes: inventory has the `Location Name ID Count Slots` header (reuses
`core.inventory.parse_inventory_text`; the client's `Empty` slot rows are
dropped on import), and a spellbook has **no header at all** — every line is
`<level>\t<Spell Name>`, so `parse_spellbook_text` rejects the whole file on
one bad line, since "every line parses" is the only discriminator. Snapshots
are JSON under `dumps_dir()`, the filename carrying `<when>-<digest>` so
listing is a directory scan with no reads and re-importing an unchanged dump
lands on the path that already exists. `read_dump_file` gates on the filename
by default (the scan must not open every `.txt` in the EQ directory) and only
sniffs content under `sniff=True`, which is the hand-picked-file path — the
window then confirms the guessed character, since that is the library's key
AND what p99planner creates its planner character from. Two toggles: `auto_import` (unseen
character+kind; also the master switch) and `auto_update` (a tracked dump
changed) — both default ON, unlike the pigparse uploader, because this only
reads files the game wrote and copies them into our own data dir.
`DumpWatcher` deliberately does NOT prime its mtimes (unlike
`InventoryWatcher`, which did): collecting what is already there is the
point, and the content digest stops an unchanged re-dump accumulating.

That watcher is now **the one poll of the EQ directory** for dumps.
`core/inventory.py` kept the parser and lost its poller; the upload half of
EQTool's InventoryWatcherService became `core/handlers/inventory_upload.py`.

**The upload trigger is `DumpWatcher(on_fresh_dump=...)`, NOT the bus
events.** The events mean "the library stored a snapshot" — local history,
which is the right question for a plugin and the wrong one for "should this
be published". Wiring uploads to them coupled two unrelated things and broke
it in both directions: `auto_update` off suppressed the event for a changed
dump, so one stale snapshot imported at startup silenced every upload for the
session; and a hand-picked `Import file…` raised the same event, so filing
away a backup — or another player's dump — published it under their character
name. `_ingest(automatic=)` is the seam: the hook fires from the directory
scan only, before the retention decision, never from `import_file`.
Three deliberate behavior changes are documented there: startup priming
became "only dumps captured after `session_start`", an unchanged re-dump no
longer re-uploads, and the character uploaded comes from the dump filename
rather than whoever is logged in. Consequence: upload rides on
`dumps.auto_import`, said out loud in both settings surfaces.

**Two upload destinations** (post-dumps): `dumps.upload_target` is one
picker — `off` (default) / `pigparse` / `p99planner` — not a checkbox each,
because both publish the same character to a different website. The legacy
`pigparse_account.inventory_upload` bool folds into it via a `Settings`
model_validator and is cleared (the `plugins.registry_url` pattern). Picking
a destination now builds its own plumbing regardless of `sharing.mode`:
uploading to pigparse.org is not the same decision as sharing your location
through it, so composition builds `PigParseApiClient` and/or
`P99PlannerClient` plus a `NetWorker` when the target asks for one.

**The destinations take different kinds**, and `UPLOAD_KINDS` in
`handlers/inventory_upload.py` is the ONE place that says so: pigparse takes
inventories (its character browser has no spellbook page), p99planner takes
both. Spellbooks ride the same unlabelled `files[]` array — there is no
`kind` field and the review page classifies by content, grouping a
character's pair into one entry — so all the wire owes it is
`export_filename`'s `<Character>-<Inventory|Spellbook>.txt`. One ordering
rule follows: a spellbook only applies to a character p99planner already
has, so `_planner_files` sorts inventory-before-spellbook per character.
The window's `upload_scope` stays site-agnostic (a selected snapshot uploads
*itself*, whatever kind; a character row or an empty selection sends each
character's current pair) and the handler drops what the target cannot take,
answering with the site that can rather than a bare refusal.

p99planner is a **handoff, not an upload**, and that shapes the code:
`net/p99planner.py` POSTs the raw export text with **no credentials of any
kind** and gets back a claim URL the player opens and approves; nothing is
applied without that human step. `core/p99planner.py` is its Qt-free
Protocol + DTOs (the `core/pigparse.py` split). Methods return an
`UploadOutcome`, not `None`-on-failure, because **410 is not a retryable
failure** — it means the claim was approved or swept, and the fix is a fresh
POST. The handler holds one claim open and PUTs later exports into it, so a
five-mule bank run is one review page rather than five; the browser opens
once per claim. **The claim URL is a bearer secret**: `ClaimLink.__repr__`
refuses to print the token, no log line carries the path or body, and
`status_text()` never contains it — all three are asserted. Claim state is
read/written only inside `submit` fetches (one net-worker thread), which is
what serializes back-to-back dumps into POST-then-PUT instead of two
competing POSTs. `upload_now()` is the manual entry point behind the
Character Dumps window's **Upload inventory** button, which resolves scope
from the selection (snapshot → character → whole roster) and ignores the
session-start gate, since that is what manual means.

Because the link is never displayed, the window is the only way back to a
pending one: a **Review import…** button appears while `has_claim()` (and
the status line carries `claim_summary()`, which names the count and expiry
but never the URL — the window asks those two predicates and never calls
`claim_url()`, so the secret never reaches a widget that could render it).
Right-clicking it offers open / **Copy review link** / cancel. Without that
button, a review page the player closed would be unreachable; without the
copy item, a machine where `webbrowser.open` does nothing would have no route
at all (the failure message used to point back at Review, which is the same
call that just failed). Copy is the ONE place the URL leaves the handler, on
an explicit user action, straight to the clipboard and never into a label.
The recovery hint lives on `claim_summary()` rather than in a one-shot status
message, because the pending line is the steady state and would replace it
within the second. The claim is **session-only** (memory, never persisted):
restart before approving and the button is gone, the link stays valid for its
24h, and the next upload stages a fresh one.

The hooks (what "expose to the SDK" means here) are two frozen bus events in
`core/events.py` — `CharacterDumpImportedEvent` and
`CharacterDumpUpdatedEvent` (the latter carrying an `added`/`removed`
multiset name diff). Plugins reach them today through the SDK's lazy
`nparseplus_sdk.events` re-export with no SDK version bump; **every** import
path publishes from the driver thread, including the window's buttons, which
is why `ui/dumpswindow.py` calls `request_scan()`/`request_import()` instead
of importing itself (same inbox shape as `SharingCoordinator`).

**Three things left the driver thread** (post-2.3.2, ~2130 tests). All three
were found by a survey of that thread; two were latent, one was live.

*Log archiving stopped killing the app on macOS/Linux* (#87). The sweep
**renamed** the log it found over the threshold — which is by definition the
one the game is writing, since that is the one that grows. The C# gets away
with it because Windows refuses to move a file the client holds open; POSIX
relinks the inode instead, EQ under Wine keeps writing to it, and no
`eqlog_*.txt` is left to attach to. The app went deaf for the rest of the
session with nothing logged, and restarting nParse+ did not help while the
game held the moved file. `logarchive.py` now copies the contents out and
**truncates in place** (what log rotators do): the client's descriptor stays
valid and its append writes resolve to offset 0.

**The tail is told, not left to notice.** A log emptied and refilled to the
tail's read offset — or past it — before the next 100 ms poll is not smaller,
and EQ repeats identical lines, so it need not differ in content there
either; no detector settles this. So the sweep is split across the two
threads it needs: `stage_oversized_logs` copies (100 MB = 80 ms, and it
scales) on a `BackgroundJob`, and `finish_archive` runs from the **driver
thread's tick**, truncating and calling `driver.note_log_rotated` in one step
no poll can land inside. What lands on that thread is *bounded*, which is the
whole point: one log per tick, one catch-up read capped at
`CATCHUP_LIMIT_BYTES` (past it the archive loses its tail, which the app has
already parsed), no fsync, and the `truncate` — 10 ms typical, 43 ms worst
measured at 100 MB. `LogTail`'s own shrink-and-signature checks stay as
the backstop for rotations nobody tells us about (the client recreating its
log, a user emptying it by hand) — hence the 64 bytes it keeps, re-read on
every poll for ~12 us. The write handle is opened FIRST so the
Windows share-mode refusal skips the file with nothing copied; the copy
lands under a `.part` name and is fsynced before the source is emptied; and
an emptied log **keeps its old mtime**, or a stale character's freshly
truncated file would become the newest one and pull the driver off the live
log.

*The EQ-running probe went off-thread* (#88). `SocialSyncWatcher.tick`
spawned `pgrep` every 15 s on the driver thread — 17.6 ms mean measured
against 0.092 ms for all ten app-owned ticks combined, with a 5 s subprocess
ceiling on the thread that tails the log. `core/background.py` is the seam:
the tick decides only *whether* a scan is due and submits it, the job runs
on a one-shot daemon thread and refuses a second run while one is in flight,
and a refused submit does not charge the interval. Scheduling costs 0.035 ms.
Only filesystem/subprocess work may run there — the bus and TimersService
stay driver-thread-only, which is what makes the seam safe. The archive
sweep uses it too (copying a 100 MB log measured 80 ms).

*`parse_line` stopped going through `strptime`* (#89). `%a`/`%b` read the
process `LC_TIME`, so under a non-English locale the parse raised, the
`except ValueError` silently took the `datetime.now()` fallback, and every
line got a wall-clock stamp instead of the log's own clock. Nothing calls
`setlocale` today — a plugin could. The stamp is fixed-width, so slice the
digits and look the month up in a dict: locale-proof, and 2.99 → 1.00
us/line on a function that runs on every log line. The `datetime.now()`
fallback stays for genuinely malformed lines; that tolerance is EQTool's.

Remote: `origin` = github.com/prokopto-dev/nparse-plus (the updater points
there too); `upstream` = nomns/nparse. The release pipeline is exercised
through v1.10.0 (semantic-release + platform builds + flatpak repo publish).
Remaining human step: confirm bidirectional dots with a real EQTool user
in-game (probe-level interop is verified). Post-1.0 parking lot lives in
README.md.
