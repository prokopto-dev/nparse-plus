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
  updater.py            # GitHub releases check (prokopto-dev/nparse-plus);
                        # Linux picks .flatpak vs .tar.gz via /.flatpak-info
  crashguard.py         # excepthooks -> crash.log; run_app also mirrors the
                        # nparseplus logger tree to nparseplus.log (both in
                        # platformdirs user_log_dir — frozen stderr is invisible,
                        # so check these first on any crash/connection report)
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
`.github/workflows/release.yml`. Preferred flow since 1.4: push Conventional
Commits (`fix:` = patch, `feat:` = minor; `chore`/`ci`/`docs` don't bump) and
run the Semantic Release workflow (locally via `uv run semantic-release
version` or `gh workflow run semantic-release.yml`) — it bumps both version
files, tags, and dispatches release.yml. The Linux job also wraps the onedir
build into a `.flatpak` bundle (`packaging/flatpak/` manifest; Linux-only to
build — CI does it, don't try locally on macOS), built with `--repo-url` and
publishes the OSTree repo to the single-commit `gh-pages` branch (GitHub
Pages) so `flatpak update` works for bundle installs.

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

**M0–M6 complete, v1.4.1 released** (~770 tests): full EQTool parity including the network —
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

Remote: `origin` = github.com/prokopto-dev/nparse-plus (the updater points
there too); `upstream` = nomns/nparse. The release pipeline is exercised
through v1.4.1 (semantic-release + platform builds + flatpak repo publish).
Remaining human step: confirm bidirectional dots with a real EQTool user
in-game (probe-level interop is verified). Post-1.0 parking lot lives in
README.md.
