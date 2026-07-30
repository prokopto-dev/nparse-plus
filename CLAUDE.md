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
    parsers/            #   one module per EQTool parser; registry.py fixes the order
    handlers/           #   bus subscribers (spawn timers, DPS, CH, pets, …)
    triggers/           #   Trigger model ({name}/{c}/{COUNTER} tokens), engine, builtin sync
    spells/             #   spells_us.txt loader (EQTool fixups), matching, durations
    timers.py           #   TimersService rows (SpellRow/TimerRow/CounterRow/RollRow)
    dps.py              #   FightTracker (12s trailing window, session stats, >20s gate)
    zones.py            #   ZoneDatabase over data/zones.json (respawn lookup order)
    ch_chain.py, death_loop.py, pets.py, npc_search.py, boats.py
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
                        #   pluginmanager.py (Settings > Plugins + registry browser),
                        #   pluginconsent.py (the one-time approval dialog),
                        #   pluginwindow.py (the PluginWindow base plugins subclass)
  audio/tts.py          # Speaker protocol: macOS `say`, PowerShell, espeak, Null
  data/                 # generated/ported data — regenerate via tools/, never hand-edit JSON
  helpers/, parsers/    # LEGACY nparse code (maps + discord windows) — see below
sdk/                    # uv WORKSPACE MEMBER: nparseplus-sdk, the stable third-party
                        # plugin contract. Versioned + released independently of the
                        # app (sdk-v* tags -> PyPI); __init__.py's exports are public
                        # API under an additive-only 1.x promise. sdk/tests runs in
                        # the root pytest. See sdk/README.md.
examples/plugins/       # reference add-ons (hello_timer.py, merchant_prices/);
                        # tests/core/plugins/test_examples.py keeps them loading
templates/              # ready-to-push content of two repos that don't exist yet:
                        #   plugin-repo/ (the future plugin template repo) and
                        #   registry-repo/ (the curated index; see its SETUP.md)
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
`sdk/src/nparseplus_sdk/__init__.py`). One-time pypi.org setup in
sdk/README.md.

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
index; `DEFAULT_REGISTRY_URL` points at the not-yet-created
`nparseplus-plugins` Pages repo, so Browse degrades to a "could not reach"
status). `core/driver.py` grew `add_supervised_tick`: plugin ticks are
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

Remote: `origin` = github.com/prokopto-dev/nparse-plus (the updater points
there too); `upstream` = nomns/nparse. The release pipeline is exercised
through v1.10.0 (semantic-release + platform builds + flatpak repo publish).
Remaining human step: confirm bidirectional dots with a real EQTool user
in-game (probe-level interop is verified). Post-1.0 parking lot lives in
README.md.
