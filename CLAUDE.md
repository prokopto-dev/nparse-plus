# CLAUDE.md — working on nparseplus

nParse+ is a Python 3.12 EverQuest P99 log-parsing overlay: a fork of
nomns/nparse rebuilt to feature parity with EQTool/PigParse (C#/WPF). If you
are porting or checking behavior, the C# source of truth is
github.com/smasherprog/eqtool at commit `d8e8084f` (see CREDITS.md) — clone it
somewhere disposable and read it; do not vendor C# into this repo.

## Commands

```bash
uv sync                                       # install (creates .venv)
uv run python -m nparseplus                   # run the app (tray + overlays)
uv run pytest                                 # full suite (~460 tests, fast)
QT_QPA_PLATFORM=offscreen uv run pytest       # headless; CI matrix does this
uv run ruff check . && uv run ruff format .   # lint/format (line length 100)
uv run pytest tests/core/parsers -q           # scope runs to one area
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
  config/               # Pydantic Settings -> platformdirs settings.json (+ legacy migration)
  net/                  # Qt-free network clients (UI marshals results itself):
                        #   p99wiki.py, pigparse_models.py (wire DTOs, camelCase-in/
                        #   PascalCase-out, THE axis swap), pigparse_api.py (REST),
                        #   hubproto.py + pigparse_hub.py (minimal SignalR JSON hub
                        #   client; signalrcore is probe-only — its sends are broken),
                        #   nparse_ws.py (legacy locationserver wire), worker.py
  updater.py            # GitHub releases check (prokopto-dev/nparse-plus)
  ui/                   # PySide6 windows; overlaybase.py is the shared overlay recipe
  audio/tts.py          # Speaker protocol: macOS `say`, PowerShell, espeak, Null
  data/                 # generated/ported data — regenerate via tools/, never hand-edit JSON
  helpers/, parsers/    # LEGACY nparse code (maps + discord windows) — see below
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
`.github/workflows/release.yml`. The Linux job also wraps the onedir build
into a `.flatpak` bundle (`packaging/flatpak/` manifest; Linux-only to
build — CI does it, don't try locally on macOS).

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

## Where things stand

**M0–M5 complete** (~680 tests): full EQTool parity including the network —
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

Remote: `origin` = github.com/prokopto-dev/nparse-plus (the updater points
there too); `upstream` = nomns/nparse. Remaining human steps: push a `v*`
tag to exercise the release pipeline; confirm bidirectional dots with a
real EQTool user in-game (probe-level interop is verified). Post-1.0
parking lot lives in README.md.
