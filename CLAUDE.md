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
QT_QPA_PLATFORM=offscreen uv run pytest \
  -m benchmark --benchmark-only               # the perf suite (#132); excluded
                                              # from the default run
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
                        #   provenance), storage.py (per-plugin JSON),
                        #   telemetry.py (#132's rolling per-plugin stats:
                        #   gated, lock-free, plugin callbacks ONLY)
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
                        #   pluginwindow.py (the PluginWindow base plugins
                        #   subclass — skinned by default since SDK 1.4),
                        #   pluginregion.py (its region counterpart, SDK 1.5:
                        #   the PluginOverlayRegion base an add-on's EVENT
                        #   OVERLAY region subclasses — sealed non-interactive,
                        #   skinned, with sample() for position mode),
                        #   pluginskin.py (THE host half of
                        #   nparseplus_sdk.skin: the curated AppSkin
                        #   snapshot an add-on dresses itself from —
                        #   the only place skins/theme/chrome are
                        #   exposed outside the app)
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
examples/plugins/       # reference add-ons (hello_timer.py, tod_window.py,
                        # merchant_prices/, kill_ticker.py = the overlay-region
                        # reference);
                        # tests/core/plugins/test_examples.py keeps them loading
templates/              # plugin-repo/ = ready-to-push content of the plugin
                        #   template repo (not created yet; see TEMPLATE_SETUP.md).
                        #   registry-repo/ = TWO files and nothing else (#147):
                        #   the generated schema, kept because
                        #   tools/gen_registry_schema.py --check guards it
                        #   against drift and the registry server vendors it
                        #   verbatim, plus the seed index the tests parse.
                        #   Everything documenting the old PR-submission
                        #   route is deleted; a test asserts the directory
                        #   stays those two files
tools/                  # one-shot converters (Zones.cs -> zones.json etc.); outputs committed
tests/                  # pytest; tests/fixtures = EQtoolsTests golden corpus
  perf/                 #   the benchmark suite + profiles.py (solo/group/raid
                        #   traffic built from real corpus lines) + baseline.json
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
- The window is called **Timers** on screen, but everything persisted or
  internal still says *spell*: `settings.json`'s `windows.spells` and top-level
  `spellwindow`, `window_layouts.*.geometries.spells`, the `show_spells` /
  `hide_spells` / `toggle_spells` chat commands, `ui/spellwindow.py`,
  `SpellTimerWindow` and the `SpellTimer*` objectNames. That split is
  deliberate (#126): renaming a key would orphan every existing settings file.
  Grep for both names.
- Background app processes started by tooling get reaped between commands —
  use `nohup ... & disown` when you need a demo instance to survive.
- Git: commit with `git -c core.hooksPath=/dev/null commit` (hook friction),
  imperative messages prefixed by milestone (`M2: ...`).

## The mark (branding)

`data/assets/icon.svg` is THE source for everything that identifies the app —
window/tray icon, `.ico`, `.icns`, docs logo + favicon, README banner, social
card. `tools/gen_icons.py` rasterizes it (PySide6's `QSvgRenderer`; **no new
deps** — no cairosvg, no Pillow, and the multi-size `.ico` is assembled with
stdlib `struct`), `packaging/make_icns.sh` calls it with `--iconset` and hands
the result to `iconutil`. Outputs are committed, like `tools/convert_*.py`.
It replaced `icon.xcf`, 64x64 GIMP art inherited from the nParse fork that
every large representation used to be an upscale of.

The glyph is an angular lowercase **n** in a ring, engraved gold on a notched
Velious plate, every colour lifted from `ui/skins.py`. The **16px silhouette
is the only hard requirement** — hence one PNG per size in `data/ui/` rather
than one big one Qt downscales (`ui/appicon.py` assembles the `QIcon`; both
`app.py` and the legacy tray go through it). There is deliberately **no
`--check` mode**: Qt's rasterizer is not byte-stable across PySide6 versions,
so `tests/tools/test_gen_icons.py` asserts the artifacts' *shape* instead
(notched corners transparent, gold survives, dark field behind it). Two
rejections are recorded in the SVG and `docs/dev-notes/branding.md` so nobody
re-proposes them: **Nauthiz** (the real n-rune) reads as a prohibition sign —
a diagonal through a ring — and a **"+" in the ring** is a two-pixel smudge at
16px, so the plus lives in the wordmark. The GitHub social preview uploads
**only through the web UI**; the card is committed at
`docs/assets/images/social-preview.png` for a human to attach once.

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

**Linux ships TWO native builds, because glibc is the one thing PyInstaller
cannot bundle** — so an artifact's glibc floor is its BUILD HOST's.
`build-linux` runs on `ubuntu-latest` (24.04, glibc 2.39) and its `.tar.gz`
cannot start on Debian 12 (2.36). `build-linux-debian12` builds the same
onedir inside a `debian:12` container and packages it with
`packaging/deb/build_deb.py` as `nparseplus_<version>_amd64.deb`; a second
job, `verify-deb-debian12`, `apt install`s it on a PRISTINE `debian:12` and
boots it, which is the only thing that validates `Depends:` (the build
container has every library installed by hand). Not `ubuntu-22.04` — that
image is unsupported from 2027-04-17. Not a manylinux image — PySide6's wheels
are `manylinux_2_34`, so the host itself needs glibc >= 2.34. **The long
`apt-get install` list in that job is load-bearing**: PyInstaller bundles
whatever `ldd` resolves at build time, and a library merely absent from the
container is not bundled while the build still SUCCEEDS — v2.25.0's Debian job
lost QtWebEngine (the Discord overlay) to a missing `libxkbfile1` exactly that
way. `packaging/deb/check_bundle.py` is the gate, and an `objdump` step
MEASURES the glibc floor rather than asserting it, so a dependency raising it
fails the build instead of a user's launch. **That gate is deliberately not
"nothing unresolved"**: PySide6 ships plugins for Qt modules this app never
loads (TIFF — unsatisfiable on bookworm and equally unresolved in the Ubuntu
tarball — plus Wayland, GTK theming, PulseAudio), so it is fatal only for
`CRITICAL` (core Qt, the xcb plugin, QtWebEngine, the launcher) and reports
the rest. A gate that fails on what does not matter is a gate that gets
turned off. **`espeak-ng` is a `Depends`, not a `Recommends`**: it is the ONLY
audio path on Linux (the trigger engine's `sound_player` seam is unwired and
the spec excludes Qt Multimedia/TextToSpeech), and `default_speaker` falls
back to `NullSpeaker` without it — silently, which is the worst failure an
alerting tool has. The Flatpak bundles espeak-ng + pcaudiolib for the same
reason; declaring it is the packaging-native equivalent.

**Every new Linux release asset must be inert to `updater.pick_asset`.** It
sweeps for `"-linux" in name` plus a suffix and takes the FIRST match, and
that predicate ships compiled into every already-released binary — it cannot
be fixed retroactively for anyone. So at most one asset may both contain
`-linux` and end in `.tar.gz`; the Debian filename satisfies that by
construction, which is why a `.deb` and not a second tarball (#160 was this
bug one artifact over). `tests/test_updater.py` carries a test that
reimplements the DEPLOYED predicate literally rather than calling
`pick_asset` — do not "fix" it to call the real function. Known gap, #163: a
`.deb` install is still offered the tarball by the update check.

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

**A Flatpak now updates itself in place** (#74). `flatpakportal.py` is the
client — a sibling of `updater.py` and Qt-free for the same reason, with
`PortalOutcome.title()/message()` carrying the prose so `ui/updatewindow.py`
stays a renderer. Four calls over **jeepney** (pure Python, `sys_platform ==
'linux'` in pyproject; PyInstaller traces every module it needs with **no**
hiddenimport, including the function-scoped `jeepney.io.blocking`, and the
spec says so): `CreateUpdateMonitor`, `UpdateMonitor.Update`, the `Progress`
signals, then `Spawn` with `FLATPAK_SPAWN_FLAGS_LATEST_VERSION` to relaunch.
**`UpdateAvailable` is deliberately not waited on** — the portal polls its
remotes on its own schedule, and the GitHub release check already told us a
version exists, so gating on the portal's poll would make "Install" do nothing
for an arbitrary interval. "Nothing to pull" comes back as `Progress` status 1
instead, which on release day means the OSTree repo has not caught up yet and
is a better thing to say than silence. `Spawn`'s argv is `/app/bin/nparseplus`
and NOT `sys.executable`: the latter skips `packaging/flatpak/nparseplus.sh`,
which is what sets `QT_QPA_PLATFORM=xcb`, so the overlays would come back on
native Wayland with no keep-above. A test ties the constant to the manifest's
install line.

**`PortalStatus.UNAVAILABLE` is the only outcome the user never hears about**,
and that split is the degrade rule: not sandboxed, no jeepney, no session bus,
a portal older than 2, or a call the D-Bus policy refused all mean *the portal
was never reached* — nothing has been said, so `_on_portal_finished` quietly
runs the download path that existed before. Every other status means the
portal answered, so it is reported rather than retried more slowly behind the
user's back; `NOT_SUPPORTED` (the permission-widening hop above) names
`flatpak update <app-id>` instead of reading as a generic failure, and can
arrive either as a D-Bus error reply *or* as a failed `Progress` — the portal
does the work in a thread, so both paths classify to the same status. The
sandbox probe is `updater.running_in_flatpak` itself, not a second
`/.flatpak-info` check that could disagree with the one picking `.flatpak` vs
`.tar.gz`.

**The `Progress` subscription needs TWO match rules, and sharing one silently
drops every signal.** `AddMatch` is read by the bus daemon, which tracks name
ownership and resolves the well-known `org.freedesktop.portal.Flatpak`, so the
wire rule names it — that is what stops the daemon forwarding somebody else's
signals. `connection.filter()` is jeepney matching **in-process**, and
`MatchRule.matches` compares the `sender` header literally against what the
daemon wrote there, which is always the sending connection's *unique* name
(`:1.42`); a well-known name never appears in that field. One rule for both
therefore matches nothing, the queue stays empty, and the install runs to its
idle timeout reporting a failure that never happened — while every test
passes, because a fake that enqueues signals directly never calls `matches`.
`progress_match_rule` (with sender) and `progress_filter_rule` (without) are
the split, and `FakeConnection` routes signals through the client's own rule
precisely so this cannot pass again. Nothing is given up by dropping `sender`
locally: the monitor's object path is minted per sender by the portal and
handed to us privately. What CI structurally cannot check (does the portal answer under the
real D-Bus policy, does `Update` find the origin remote, does `Spawn` come
back on the new deploy) is listed in `docs/development/releasing.md`.

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

**A refusal is not a flaky network, and the caller now hears the difference**
(#93). `download_asset` answers a `DownloadOutcome` (frozen pydantic:
`status` + `asset_name` + `path` + `detail` + `pinned`), never a bare `None` —
answering `None` to both made the one case verification exists to catch
indistinguishable from a timeout, and `install_action` turned that into
"open the release page", handing the user a browser pointed at the artifact
that had just been refused. `DownloadStatus` is a vocabulary meant to grow
(the #76 pre-flights — unwritable install root, insufficient disk,
translocated bundle — land here); `REFUSALS` is the set a caller actually
branches on, so nobody switches on members. **`pinned` is orthogonal to
`status` on purpose**: "this release published no checksum" and "the checksum
did not match" are facts about different releases, and collapsing them tells
someone on a pre-digest release their download is corrupt. **An over-long
body never reaches `_size_error`** — the published size doubles as the stream
ceiling, so it is cut mid-download; `ByteBudgetExceeded` carries which
ceiling stopped it so the caller can say. Stopped at the release's own
number that is the same `SIZE_MISMATCH` a short body gets; stopped at the
global backstop it is a bare `REFUSED`. Left as a plain `ValueError` it read
as a transport failure and opened the release page for an artifact that had
just been rejected — the exact bug #93 is about, one path over. The prose
lives in
`DownloadOutcome.message()`/`title()` — Qt-free and tested without a window —
and `ui/updatewindow.DownloadOutcomeDialog` is a pure renderer of it, with
the digests in the details drawer. **A refusal does not open the release
page**; a transport failure still does, and says so.

**macOS publishes a `.app` zip beside the DMG** (#75), built with `ditto -c -k
--keepParent`, NOT `zip`: the bundle's signature seals over resource forks and
extended attributes that `zip` drops, so a plain-zipped `.app` fails
`codesign --verify` and, on Apple Silicon, will not launch. CI extracts the
zip and verifies the seal, which is the guard on that line. `pick_asset`
takes the zip only under `self_update=True` (nothing passes it yet — #76's
swap helper is what will): the DMG is what a person should get, since it
mounts and shows the drag-to-Applications window, and the zip is what code
can unpack without `hdiutil attach`/copy/`detach`. The macOS branch's last
fallback is deliberately `.dmg` and never "the requested suffix" — the
Windows artifact is a `.zip` sitting in the same release.

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
runs it. The one consumer is the registry server's `SCHEMA001` gate, which
diffs the document it renders against this file — so the schema is how "what
the server serves" and "what the app parses" are kept from drifting, and a
regeneration has to be copied over there.

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
rewrites settings). The Timers window (the headline complaint — only one
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
hop, sha256 pinning, uninstall-to-`trash/`), `registry.py` (the curated
schema-1 index; `DEFAULT_REGISTRY_URL` has pointed at the live registry
server since #130 — see "The registry moved" below — and Browse degrades to
a "could not reach" status). `core/driver.py` grew `add_supervised_tick`: plugin
ticks are
timed against `TICK_BUDGET_S` (0.25 s) and evicted after
`TICK_BREACH_LIMIT` (2) consecutive breaches — the plugin stays active and
the manager annotates its row "tick disabled (too slow)"; app-owned ticks
are appended to `on_tick` directly and never timed. Qt side:
`ui/pluginmanager.py` (the page + `RegistryBrowserDialog`, installs on a
worker thread because validation *imports and activates* the candidate, and
a Source provenance column that says "Sideloaded" out loud),
`ui/pluginconsent.py`, `ui/pluginwindow.py`, plus the settings window's
`extra_pages` seam. Known v1 limit, documented: consent gates activation but
not import (a declarative manifest is the fix). The other one — nothing
hot-loaded — is gone; see "Add-ons load and unload without a restart" below.

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
constant later moves every user instead of stranding them (#130 did exactly
that, and found the one thing it does not cover — see below). `fetch_indexes`
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

**The meter learned to represent a caster and a pet class** (post-2.4,
~2250 tests): two DELIBERATE DIVERGENCES from EQTool, both commented as such
at the code that implements them, because `DamageParser.cs` and `PetHandler`
behave exactly as this repo did.

*Non-melee is attributed, not blanket-credited* (#80). `<target> was hit by
non-melee for N points` names **no attacker**, so the 1:1 port credited
`"You"` for every one — meaning melee-only dropped a caster's whole output
and counting it padded your row with the raid's nukes and opened fights on
mobs you never touched. The parser is untouched (2.2 put the filter in the
tracker so `DamageEvent` stays a faithful record for triggers and plugins);
`FightTracker._attribute` decides. The one signal a log gives is your own
casting, so `melee_only: bool` became
`damage_sources: melee | melee+mine | all` (a `DpsSettings` model_validator
folds the old bool in — the `plugins.registry_url` pattern). **The
fresh-install default and the migration deliberately disagree**:
`DEFAULT_DAMAGE_SOURCES` is `melee+mine` because that is the mode that works
for a caster, while an existing document maps LITERALLY (`true` → `melee`,
`false` → `all`), because what a headline number MEANS must not change
under a user who only updated the app — even toward being more correct.
Casters get a release note instead. The guard is `model_fields_set`, not
"still equals the default", so an explicit `melee+mine` survives a stale
`melee_only: true` beside it. Under `all` an unclaimable line lands on a
`"(spell damage)"` pseudo-attacker so group percentages stay right without
the meter claiming it.

**The window is armed from `YouBeginCastingEvent`, not just the finish
event.** The landing message and the damage line share a log second and the
stamps are 1-second resolution, so their arrival order cannot be relied on;
the begin line is the only one guaranteed to precede the damage. So
`note_your_cast(when, cast_time_s)` arms through cast-time + the credit
window, and the finish event extends it — union, and extending only ever
moves the end forward, which is why a chain-caster stays armed. Only
**detrimental** spells arm it (`Spell.is_detrimental`), or a cleric
chain-healing would collect the raid's spell damage. `cancel_your_cast()`
disarms and is deliberately NOT `clear()` (an interruption says nothing
about the fights on screen): `YourSpellInterruptedEvent` reaches it, since
arming from the begin line otherwise holds an 8s cast's window open for
nine seconds after it was interrupted a second in, and `clear()` calls it
because zoning/camping/dying cancel the cast too. A **resist** deliberately
does not disarm — a partial resist prints nothing and still does damage, so
acting on the resist event would discard real caster damage to close a
window at most the credit window wide. Only the LANDING moment is stored —
`credit_deadline` derives from it on every read — so moving
`spell_credit_window_s` reaches a cast already armed; storing the deadline
froze the window at arming time, and tightening it mid-raid (the one
situation the setting exists for) did nothing until the next cast. Unattributable forever: damage shields and procs
(they follow no cast), two casters inside one window, and DoT ticks — P99
does not log them at all, which is why #80 replaced a "parse DoT ticks"
proposal.

*Pet damage counts as yours* (#81). `DpsHandler` follows the existing
`PlayerPet.on_change` rather than re-deriving the CREATION/LEADER/DEATH
rules, pushing `FightTracker.set_pet_name`; the tracker stays value-in/
value-out and never imports pet state. `FightRow.is_your_pet` is separate
from `is_your_damage` — the window styles both as yours and marks the row
`Vexer (pet)`, but they are not the same claim. The pet keeps its **own
row** (whether the pet is holding up is what a mage wants to see; merging
would make `highest_hit` and per-row dps meaningless) while
`_update_session_stats` **sums** you and the pet into one footer reading
under `count_pet_damage` — **default OFF, diverging from #81**, which asked
for on. How to count a pet is a genuine difference of opinion, not a defect,
and a counting change must not silently alter what someone's headline number
MEANS on upgrade; the row marking stays unconditional because naming whose
pet that is is identification, not measurement. Two deliberate asymmetries
in the merge itself:
`highest_hit` stays yours alone (it reads as your own crit), and the
fight-length gate takes the LONGER of the pair, so a pet that opened 25s
before you joined carries the reading past the minimum. `_is_your_pet`
refuses a row whose attacker equals its fight's target, so the charm-shares-
an-NPC-name case `add_damage` already guards cannot come back through the
flag.

**The row flag and the footer ask different tenses, and that split is the
fix for a real bug.** `FightRow.is_your_pet` is present tense — a row stops
being "my pet" the moment the pet dies, which #81 asks for explicitly — but
the footer keyed off the same live `pet_name` silently dropped everything a
pet had already contributed as soon as `PetHandler` cleared it on death,
reclaim, charm break or zone, for the rest of a fight still running. So
ownership is stamped per hit onto `FightEntity.was_your_pet` in
`add_damage` (the one moment the answer is certain) and the footer selects
on that. It also makes the resummon case right — two entities qualify,
because both pets were yours. Sticky within one fight, which bounds the one
case it over-counts: a charm that breaks and keeps hitting the same target.

Both new knobs are measurement rules (`_measurement_rules`), so changing
them resets Best/Now like the window and the minimum-fight gate do. The DPS
window's title bar carries the mode (`MELEE` / `MELEE + MINE` / `ALL`) read
live off the tracker — a mode that excludes damage has to say so, or a
caster reads a zero as a broken parser. Settings > DPS Meter grows the mode
picker, the credit window and the pet toggle; all still live through
`Backend.apply_dps_settings`.

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

**Two more settings stopped needing a restart** (post-2.4.2), both by the
`apply_dps_settings` seam: the window mutates the settings tree and calls a
`Backend` method that pushes the change onto what launch built.

*The dump upload destination* (#68). `dumps.upload_target` was already read
live by the handler — what was not live was the plumbing that read depends
on, decided once in `build_backend`, so picking a destination mid-session
left `InventoryUploadHandler.planner`/`.api`/`.submit` at None and every
upload silently no-opped. `Backend.apply_upload_target()` builds what the
newly-picked target needs and lacks, then pushes all three onto the handler.
It closes the Discord login case too: `pigparse_account.api_token` rides in
a per-request header and was genuinely live, but `pigparse_api` may never
have been built. **Nothing is ever torn down** — `stop()` closes these at
quit and an in-flight p99planner claim or PUT would go with them, so
switching away leaves the client idle instead. What stops an upload is the
target, read **twice**: `accepts()` before anything is queued, and
`InventoryUploadHandler._still_targeting` on the net-worker thread just
before the request leaves. The second read is not belt-and-braces — one
worker serves the queue, so a dump submitted behind a slow POST waits there
while Settings > Apply changes the destination, and without it turning
uploads off still published the inventory. `forget_claim` deliberately does
NOT go through that gate: releasing a staged copy is the user withdrawing
data, and switching the destination off first is the likely order.
Deliberately narrow: a REST
client built for an upload does not re-arm the seven handlers that publish
on *sharing's* behalf (#69 owns that direction).

*The EQ install directory's spell database* (#70). Every other consumer of
`general.eq_install_dir` was already live; `_spells_path` resolved once, so
the one setting that first-run users set alongside the log directory left
spell durations on the bundled DB for the whole session, silently. Two
threads, because the two halves cost wildly different amounts:
`SpellBook.adopt(other)` is six rebinds and runs on the **driver tick** —
same object, new contents, which is what `ParseContext`, `SpellTimerHandler`,
`AbilityCooldownHandler` and `TimerPersistenceHandler` need since they all
captured the book itself — while the parse behind it is **~1.1 s** (8k lines,
measured) and rides `core/background.py` like the archive copy and the EQ
probe. `composition._SpellBookReloader` is the inbox: `request(path)` from
the GUI thread, one tick submits the parse, a later tick adopts it. A
refused submit keeps the request rather than dropping it, or a fast
double-Apply would strand the book on the first path. `SpellBook.reload(path)`
does both halves for anyone with no driver thread to protect.
`Backend.reload_spell_book()` returns whether it scheduled anything: an
install with no `spells_us.txt` resolves back to the bundled copy, and
re-parsing to arrive where we already are is pure cost.

**Mob Info renders the whole wiki page** (post-2.9.2, #113): `net/p99wiki.py`
parsed nine template fields and `ui/mobinfo.py` consumed **none** of them —
the window showed name, zone, a respawn figure and a loot list that only
existed with sharing on. It now ports EQTool's `MobInfoViewModel` field set:
the stat block (HP/AC/damage per hit/attacks per round/attack speed/run
speed/aggro radius), the **whole** `location` text (P99 lists several spawn
points; `location` keeps the first pair for the map and is the wrong answer
for a human), respawn, specials, factions, opposing factions, related quests,
the drop table, and the page picture. **Drops parse from two shapes**: the
`known_loot` field (`{{:Item}}`/`[[Item]]` per `<li>`, `drare` rarity kept —
EQTool strips it) and, only on a page whose template says it is an NPC's, a
body wikitable or `== Loot ==` list. That gate is what stops "Cazic Thule"
redirecting to the ZONE article and the god's drops reading `Feerrott`.
**The image resolves through the MediaWiki API** — EQTool builds
`/images/<file>` by hand, which misses the hash fan-out and can never ask for
a thumbnail. Two fetch legs on the same NetWorker, deliberately separate:
the PigParse pricing leg keeps the sharing-GATED submit, the wiki leg takes
the raw one, because wiki.project1999.com is not pigparse's API — so a
player who shares nothing still gets the drop table. `merge_loot` is the
rule: case-folded name is identity, **PigParse pricing wins and the wiki
contributes rarity**, wiki-only drops fill the gaps, priced rows sort first
(the window clips the list, so ordering decides what survives). Fetching
lands in `net/`, the QPixmap is built in `ui/` from a file the client
already cached under `platformdirs.user_cache_dir` — that split is why
net/ still imports no Qt. Everything wiki-derived is `html.escape`d and
non-http URLs are dropped before they reach a RichText label (#102's class).
`mobinfo.wiki_details` (whether nParse+ contacts the wiki at all) and
`mobinfo.show_image` are both on by default and both live via
`Backend.apply_mobinfo_settings`; the first is also a third reason
composition builds a `NetWorker`, which is why two tests that assert "no
feature asked for network" now turn it off. Fixed en route, both found
against live pages: `_FIELD_RE`'s `\s*` after the `=` crossed the newline of
an empty field and captured the NEXT field's line as its value, and the
respawn line now shows the zone database's figure AND the wiki's, because
`ZoneDatabase.spawn_time` always answers (global 6:40 fallback) and so
cannot say it has never heard of this NPC.

**Camping saves, hides and restores the character's own timers** (post-2.11,
#120). Four pieces, and the first one is a prerequisite rather than a feature.

*The camp event moved onto the driver tick.* `CampParser` published
`CampEvent` from a `threading.Timer` daemon thread, so every subscriber ran off
the driver thread — already a latent race for `FightTracker.clear` and the
sharing keepalive state, and a hard blocker for a subscriber that touches
`TimersService` (not thread-safe; driver tick only). The start line now records
a wall-clock deadline, the abandon line clears it, and `CampParser.tick` —
registered on `driver.on_tick` from composition, which is the one reason the app
holds a handle on a parser — publishes it. Same 6 s delay, same abandon
semantics, one thread. The deadline is wall-clock, not log time, so a catch-up
read of a backlog does not fire it instantly.

*Buffs freeze, cooldowns don't.* That split is the whole design: a reuse timer
runs in the real world whether or not you are logged in, so `you_cooldowns`
(YOU_GROUP `TimerRow`s **and** the spell-recast `SpellRow`s `export_you_spells`
deliberately skips) store absolute ends and drop what came up while away, the
way `respawn_timers` always has; `you_spells` keeps storing seconds-left.
`you_counters` is the third store — a bard tally has no end time, so what runs
in the real world is its `COUNTER_IDLE_EXPIRY`, and the last-updated stamp is
what is stored. A cooldown snapshot carries `spell_name` so a recast row is
rebuilt as the `SpellRow` it was, gem and all, falling back to a `TimerRow` when
the loaded database no longer has the spell.

*Removal is narrower than `clear_you_spells`.* That method drops every YOU_GROUP
row while `restore_you_spells` only re-adds buffs, so using it here would
destroy cooldowns and counters with no restore path. `remove_self_rows` removes
exactly the three kinds the three exports cover and nothing outside YOU_GROUP —
boats, roll windows, custom/shared timers and mob respawns are world state and
stay visible and counting through a camp.

*`_camped` suppresses export, for the same reason `_restoring` does.* Export
runs on **every** `TimersService` change, so removing the rows on camp would
re-export an empty set over the snapshot just taken. The guard lives inside
`export_now`, so `BeforePlayerChangedEvent` and `Backend.stop` respect it too —
camp-then-quit must not lose the buffs. `WelcomeEvent` is the counterpart and
does **not** gate on `_camped`: whatever YOU_GROUP rows are on screen at a login
belong to a session that ended, so they are dropped and the stores restored,
re-anchored at the login moment. That is also what covers the app running
straight through a link death and relog. A different character logging in still
comes through `After/BeforePlayerChangedEvent` (the log path changes), which
clears the flag and restores that character's stores.

**Seconds-left is measured against the last log line, not the wall clock**, and
that is what makes link death survivable. Link death emits nothing — the client
just stops writing — so with a wall-clock anchor the app kept draining the rows,
kept exporting, and erased the buffs it exists to save. `LogPipeline.last_entry_time`
already existed; `TimerPersistenceHandler` takes it as `log_clock` and, on top
of that, an `on_change` export whose anchor has not advanced since the last one
does not write at all: a change the log did not explain is the wall clock
draining rows out from under a client that may not be there. Harmless during
play (the store is a full snapshot, so the next line re-exports the second), and
`export_now` is still authoritative for camp/quit/player-change. Known limit,
stated in the docs: a player idle in a silent zone has a stale anchor, so a link
death after long silence restores slightly generously — bounded by the silence,
and erring in the right direction. Deliberately **no** silence-based
"presumed gone" heuristic; that is a separate decision.

**The SDK learned about the EQ install** (SDK 1.2, #123): `ctx.eq_dir` +
`ctx.eq_is_running()` and the `nparseplus_sdk.eqfiles` re-export — the
prerequisite for offering the P99 login middleman (#114) as an add-on instead
of a core feature. Three additive names, so no `check_compat` fallout; a
plugin that touches the install declares `requires_sdk=">=1.2,<2"`, since on
an older host the attribute is simply absent and there is nothing to degrade
to.

**`eq_dir` grants no capability that was not already reachable** — and that
is the argument for it, not against. `pluginbootstrap.py` already hands every
plugin *window factory* a `PluginWindowContext(settings=...)` carrying the
whole mutable `Settings` root, so `general.eq_install_dir` was reachable by
any plugin willing to declare a window it did not want. A scoped property is
strictly narrower than that leak and puts the value where `activate()` can
use it. (The leak itself is a wart to narrow later, not a precedent to
extend — which is why there is deliberately no `ctx.settings`.) It is a
**property, re-read per access**: #70 made that setting live for every other
consumer, and a plugin caching it at activate time would be the one thing
left pointing at the old install.

`eqfiles` is a lazy host re-export of `core/eqini.py`'s pure plumbing on the
`nparseplus_sdk.events` pattern, and exists so CLAUDE.md's "do not invent a
second way to touch the install" survives a second party touching it —
without it every install-editing plugin reinvents backup-first and does it
worse. The forwarded set is an **explicit `EXPORTS` allowlist** rather than
blanket attribute forwarding, so the additive-only 1.x promise covers names
we chose rather than everything that happens to live in `eqini`.
`eq_is_running()` is exposed for the "restart EQ for this to take effect"
warning an install edit owes the user, and is exposed *because* #88 was that
`pgrep` landing on the wrong thread: one docstring saying "never from a tick"
beats every plugin rolling its own. The host imports the probe inside the
method, so `core.plugins.context` — which is on the activate path — does not
pull subprocess plumbing in for a capability most plugins never touch.

**The registry moved to a live server** (post-2.15, #130): the built-in
catalogue is `https://nparseplugins.prokopto.dev/index.json` — one Go binary
over SQLite (`prokopto-dev/nparse-plugin-regserve`) — instead of a static
`index.json` on the curated repo's GitHub Pages, so that publishing can
become a step in a plugin's own release pipeline with the server fetching and
hashing the artifact itself rather than storing a digest its author supplied.
**The client did not change and must not**: same schema-1 document, same
single unconditional GET, and the server keeps `/index.json` and
`/plugins/{id}/index.json` outside its `/api/v1` precisely so the path a
released binary is compiled with never moves. No caching, no `ETag`, no
`User-Agent` — those are their own decision, not a rider on this one.

**The work was the provenance record, not the constant.** The registry *list*
needed no migration — nothing about the built-in row is persisted, which is
the whole anti-stranding design — but `PluginEntry.registry_url` is a record
written once at install time, so every plugin installed before the move
pointed at a URL no longer configured anywhere: Source falls back to a bare
host, Browse offers "Installed (other source)", and `best_update`'s rule that
the installed-from registry wins turns the built-in registry's own offer into
a cross-source confirmation between two names for one catalogue.
`PluginsSettings._follow_the_moved_default` rewrites those records inside the
validator documented to never raise, and **the condition is the user's own
registry list, re-checked on every load**: a record naming the old URL moves
only while no row holds that URL. Then nothing else it could have meant is
left — `add_registry` refused a URL equal to `DEFAULT_REGISTRY_URL` and
`resolve_registries` collapsed a stored copy into the built-in row, so an
install cannot have come through a copy. List it and both the row and its
records stand untouched, which is what makes the old index safe to add
deliberately now that it is an ordinary third-party URL; remove it and the
next load folds those records into the built-in catalogue, which is the
documented way back for a row that only ever duplicated the default.

**Stateless on purpose.** A one-shot `plugins.registry_move_applied` marker
was written and then removed: v2.16.0 moved the constant and introduced no
marker, so "no marker" proves nothing about a document's era, and every
derived era signal (e.g. "does any record name the NEW url") has the same
hole — a user who added the old index on that release and installed only from
it. Deciding from what the document says *now* has no hole to argue about.
The list is edited in exactly one case: a row the same validation
manufactured from the deprecated `plugins.registry_url` override, an artifact
of the fold and never a list entry. Only an exact normalized match is ever
touched; a fork's Pages URL is somebody else's registry.

**One literal, and it lives in `config/settings.py`.** `BUILTIN_REGISTRY_URL`
sits beside `normalize_registry_url` for the reason that function is there:
settings has to migrate a stored URL without importing the plugin subsystem,
because that import gate is what keeps a plugins-off launch from touching the
SDK. `core.plugins.registry` re-exports it as `DEFAULT_REGISTRY_URL`, which
stays the name everything else uses. The URL-pin test was **refocused, not
deleted** — it now asserts the shape (`https://…/index.json`) and that
`docs/plugins/registry.md` and `docs/plugins/security.md` both name whatever
the constant says, so moving the catalogue again is a one-line change plus the
two documents that tell a human where it went. (It used to point at
`templates/registry-repo/SETUP.md`, which #147 deleted along with the rest of
the submission scaffolding — refocused a second time, never dropped.)
`SCHEMA_ID` in `tools/gen_registry_schema.py` names the new host too; the
generated schema is vendored verbatim by the server (its `SCHEMA001` gate), so
a regeneration has to be copied there.

**Add-ons load and unload without a restart** (post-2.17, ~2834 tests, #45):
install, uninstall, enable and disable all take effect the moment you click,
end to end — core and Qt.

*The one new primitive is a driver-thread command inbox.* `LogDriver.
submit_to_driver(fn, *, label)` runs a closure on the driver thread at a loop
boundary. The unsafety was narrower than #45 assumed: `EventBus.publish`
already iterates a snapshot of both subscriber lists (deliberately — a handler
may (un)subscribe during dispatch), so the bus was never the problem. The
genuine race is `LogPipeline.process`, which walks the live `_parsers` with an
early `break`, so a GUI-thread `remove_parser` mid-line shifts the index and
silently skips a parser for that line. `append_parser`/`remove_parser`/
`add_supervised_tick`/`remove_tick` route through the inbox, and the fast-path
tick loop copies unconditionally now. Same pattern as
`SharingCoordinator.enqueue_inbound`, one layer down.

*Per-plugin lifecycle.* `PluginHost.activate_one`/`deactivate_one` were
already the loop bodies of `activate_enabled()` and `shutdown()` in all but
name; `deactivate_one` adds the three things process-exit skips (retire the
row to `disabled`, clear the context, drop what the plugin built) and keeps
`shutdown`'s ordering — `deactivate()` **then** `unwind()`, so a plugin still
has its registrations while it shuts down. `adopt_installed(path)` classifies
a plugin installed this session. `set_enabled` is the live entry point and
never stands in for consent. Two things `unwind()` could not reverse now
reverse: `ctx.tick_dropped` resets (a re-enabled plugin inherited "tick
disabled (too slow)"), and **timer rows carry an owner** (`BaseRow.owner`,
empty for every app-owned row, plus `TimersService.remove_owner`) — `ctx.timers`
stamps the plugin's id on rows it adds, which is also where the mutation
crosses back onto the driver thread now that `activate`/`deactivate` run on
the GUI thread. The `WindowState` on disk is left alone on purpose: it is how
a window comes back where you left it.

*The Qt half is six couplings and no private copies.* The host exposes
`on_ui_build` (handed the `LoadedPlugin`) and `on_ui_teardown` (handed a bare
plugin id — core never sees a widget); `PluginUi.attach_live` in
`pluginbootstrap.py` subscribes both from `create_app` **after** the settings
window, layout manager and tray exist, because those are built *from* what
`build_plugin_ui` returned and subscribing earlier would build the startup
windows twice. Each seam is the live collection its owner already had: the
chat-command dict and the tray dict mutated in place
(`Application.add_backend_window`/`remove_backend_window` — the menu re-reads
its dict on open), `WindowLayoutManager.add_window`/`remove_window`,
`UnifiedSettingsWindow.add_page`/`remove_page` (sidebar row and stack widget
taken TOGETHER — row N maps to stack index N and only construction order
maintained it), `set_plugin_window_rows` (that grid is index-addressed, so it
is rebuilt in place at the same stack index; the plain-tuple typing stays, so
`test_master_toggle.py` never wakes the subsystem), and `chrome_surfaces` for
the skin sweep. Teardown runs AFTER the unwind and `deleteLater()`s the widget
— hiding leaves the plugin's QTimers firing into a widget the host no longer
manages — hiding first, since `deleteLater` only schedules.

*Two things stay restart-only, and both say so.* The master Settings >
Advanced switch, **by design**: "off" means the machinery is never imported,
which is what `test_master_toggle.py` pins structurally (it passes unchanged —
no third gated import site, `GATES` untouched), so a live switch would have to
import, consent for and activate everything at once on the GUI thread and
prove nothing is left on the way back. And an in-place **update**: re-importing
replaces only the top-level `sys.modules` key, so stale `<stem>.helper`
submodules survive and the old objects keep the old module globals (the import
key is the file stem, not the plugin id — `demo.py` and `demo/` can coexist).
Install, uninstall, enable and disable are live precisely because none of them
re-imports anything. `"Installed — restart to load"` survives for exactly one
case: an install `adopt_installed` refused, which the plugins-folder sweep will
pick up next launch.

**The plugin boundary got a measurement baseline** (post-2.18, #132 —
Phase 0 of the #131 epic, and measurement ONLY: no interval-aware ticks, no
COW bus snapshot, no serial queues). Four pieces.

*Benchmarks with fixtures that are real.* `tests/perf/profiles.py` composes
solo/group/raid traffic out of line shapes taken verbatim from the
`EQtoolsTests` corpus (each template carries the C# test it came from); only
the names, numbers and interleaving are ours, and `tests/perf/test_profiles.py`
— NOT marked `benchmark`, so it runs in CI — asserts every line still parses,
that each profile reaches the parsers it claims to, that >70% of lines are
claimed by a parser, and that the line rates still match the situation
described. A fixture that quietly stopped matching anything would otherwise
keep producing beautiful, meaningless numbers. `tests/perf/test_benchmarks.py`
covers `EventBus.publish` at 0/1/10/50 subscribers, `LogPipeline.process` over
every profile plus the capture, plugin dispatch at 1/10/50 with collection off
and on, a plugin parser, the Qt bridge, and end-to-end latency; the map
benchmark moved here from the deleted `tests/test_benchmarks.py`. Two
measurement bugs found and fixed en route, both from the pre-existing suite's
shape: replaying into ONE backend accumulated fights and timer rows so the
cost grew with the round count (fresh backend per round via
`pedantic(setup=)`), and publishing a `DamageEvent` on the *backend's* bus ran
the fight tracker, swamping the microseconds being measured (plugin dispatch
uses a bare bus). Seed numbers: ~21 us/line through the full chain, +19-25 ns
per plugin callback for the guard wrapper, +270 ns more when collecting,
74 us parse->UI against 102 ms append->UI — i.e. the poll interval is the
end-to-end latency and everything else is noise beside it.

*The collector is cheap by construction, and that is asserted.*
`core/plugins/telemetry.py` is Qt-free rolling stats per plugin per channel
(handler/parser/tick): a 512-sample ring for nearest-rank p50/p95/p99, an
all-time `worst` that deliberately does NOT roll, running mean, a bucketed
15 s rate window, error/drop counts, and `busy_fraction` (share of ONE
thread). Deliberately lock-free — writers are the driver thread, the reader
is the GUI, and a lock would roughly double what collection costs to buy a
count nothing decides on. **Only plugin callbacks are ever wrapped**, so the
no-plugin case keeps `core/driver.py`'s property untouched, and the gate when
a plugin IS loaded is one attribute read on a `__slots__` object *before* any
clock. Ticks are free: `add_supervised_tick(on_duration=)` hands the channel
the elapsed the watchdog already measured, which is why the tick channel has
no gate. `tests/core/plugins/test_telemetry_cost.py` counts `perf_counter`
calls rather than timing anything: 0 for the driver tick loop with no
plugins, 0 with a plugin and collection off, exactly 2 with it on, and 2 (the
watchdog's own) for a plugin tick. `settings.plugins.telemetry` defaults True
and lives live through `PluginHost.set_telemetry`.

*Two small seams the measurement needed.* `_MeasuredParser` wraps a plugin
parser, so `core/parsers/base.describe_parser` exists to keep the pipeline's
exception log naming the real parser through any wrapper (`parser_label`) —
the pipeline itself learns nothing about measurement. And `Channel.reset()`
is IN PLACE, never rebinding: wrappers capture the channel object at
activate, so handing out a new one on re-enable would leave the plugin
writing into a channel nobody reads.

*The dashboard.* `tools/perf_report.py` (stdlib only, hand-written SVG — the
`gen_icons.py` no-new-deps rule) normalizes pytest-benchmark JSON into a
compact run record, appends to a persistent history, compares, and renders
`docs/development/performance.md` plus one ratio-to-first trend chart per
group. `.github/workflows/performance-nightly.yml` runs it at 04:10 UTC,
archives the JSON as an artifact, and redeploys the mike `dev` docs.
**A run's identity is `$GITHUB_RUN_ID`, never the commit** — a scheduled run
measures whatever the default branch is at 04:10, so the SHA repeats every
night until the next merge, and keying the history (or the `runs/` archive)
on it would keep one point per merge and discard exactly the repeated
measurements that separate runner noise from a real regression. Re-running
one workflow run replaces its own entry, which is the only case that IS a
correction.
**History lives on the orphan `perf-history` branch, not gh-pages** — the
flatpak release job force-pushes gh-pages as a single-commit orphan and would
destroy it — and not on master, where a nightly bot commit would spin CI and
semantic-release every night. **Nothing fails on a slow number**: hosted
runners are shared VMs, `compare` only exits non-zero for `--fail-over`, and
nothing passes it. A benchmark mean is a fact about a machine, so the
baseline is per-runner and self-seeding: the first CI run on a runner becomes
that runner's `baseline-<runner>.json`, and until then the page says out loud
that it is comparing across hardware. `tests/perf/baseline.json` is the
committed seed (recorded locally, labelled as such) so `compare` works for a
developer with no history branch.

**The registry move finished** (post-2.20, #147): #130 repointed the
constant at the live server; this repointed the *story*, which was still the
old one in six places, and picked up the one wire field that was waiting.

*The trust argument was rewritten, not quietly dropped.*
`docs/plugins/registry.md` still said there was deliberately no server, that
submission was a pull request against `nparseplus-plugins`, and that a
maintainer confirmed each listing. What is true: publishing is
`POST /api/v1/plugins/{id}/releases` with a scoped PAT and an
`Idempotency-Key`, from the plugin's own release pipeline; **the server
downloads the artifact and hashes it ITSELF and a submitted hash is never
stored** (regserve ADR-0008), so the published digest is a measurement
rather than a claim; a brand-new id always waits for a human and trusted
owners' version bumps publish automatically unless a quarantine rule fires
(ADR-0007); ownership is a permanent, never-recycled database row, not
`owners.json`; artifacts stay on GitHub Releases, so the URL is transport
and the hash is the security boundary (ADR-0002). The docs say the
re-hashing is **stronger** than the human confirmation it replaced instead
of letting the old claim lapse silently — a trust document that goes vague
costs more than one that is wrong and obvious — and they state the two
things that got weaker in the same breath: a trusted owner (or a stolen
token) can ship unreviewed code to that plugin's existing users, and a
server-computed digest proves the server measured those bytes, not that the
server is honest. That last one is what index signing would be for, and it
is still the only thing on the registry roadmap here.

*`templates/registry-repo/` is two files.* README, SETUP, CONTRIBUTING,
`owners.json` and `validate-index.yml` all documented the vanished route —
SETUP's Pages-enablement runbook actively pointed a reader at standing up
the wrong thing. The generated `schema/index-v1.schema.json` and the seed
`index.json` stay, because the schema drift guard is the only reason the
directory ever survived, and `test_registry_schema.py` now asserts the
directory holds *exactly* those two. Its URL-in-docs test lost SETUP.md as
one of its two documents and was refocused onto `docs/plugins/security.md`
rather than halved. `templates/plugin-repo` stops composing a
`registry-entry.json` for a PR nobody opens; it prints the artifact URL and
digest a publish request carries, and a test asserts it does **not** grow
the request itself — the reusable publish-on-tag workflow is the registry
server's own next phase, and writing it twice is how two versions of it
start disagreeing.

*`RegistryRelease.notes` ships ahead of the server serving it.* The field is
additive on a format the server cannot change (released clients parse it),
so landing the client half now means it lights up with no app release. **Two
spellings are accepted deliberately**: the index document calls it
`release_notes` while the publish request calls it `notes` — the server's
conventions forbid a column named after a wire field — and only one of those
is on this wire yet. Rendering is the whole constraint: ADR-0013 chose plain
text precisely so that **no client needs a sanitiser or a Markdown
renderer**, so every sink is one that cannot interpret the text — a
read-only `QPlainTextEdit` under the Browse table, and a constructed
`QMessageBox` with `setTextFormat(PlainText)` for the cross-source update
confirmation. The static `QMessageBox.question` helper had to go for that:
its default `AutoText` would hand anything tag-shaped in a registry's
display name or an author's notes to a rich-text renderer.

**A parse leaves the meter, and Best stops resetting daily** (post-2.21,
~3000 tests): the DPS parity cluster, #78 then #83 — one branch, because they
share `core/dps.py` and `ui/dpswindow.py`.

*Copying a parse* (#78). The single most common thing a raider does with a
meter had no affordance at all: no copy, no export, no right-click on
`ui/dpswindow.py`. `core.dps.format_fight_details` is the port of
`DPSMeter.xaml.cs copytoclipboard`, and the format is a **wire format** —
EQTool users paste these into the same raid channels — so the four spaces
after the group total, the `" / "` between attackers and `DPS:` being
`TotalDPS` rather than the trailing-window number the row displays are pinned
by exact-string tests. There is no `EQtoolsTests` case for it; the call site
at `d8e8084f` is the spec. `snapshot()` already sorted by total damage
descending, which IS the copy order, and it re-sorts anyway so the function is
true to `OrderByDescending` on any input.

Auto-copy on a notable kill ports `LogParser_DeathEvent`, and its gate is
`ZoneDatabase.is_notable_kill`: notable in the zone you are standing in,
**minus** `kael_faction_mobs`. That second clause is the whole reason the
predicate is not "is this notable" — Kael's faction giants are listed as
notable so the map and spawn timers treat them properly, and they die by the
hundred. The decision stays Qt-free in core and the window only writes the
clipboard; the balloon EQTool raised from inside the copy is a `parse_copied`
signal `app.py` connects to the tray. DEVIATION, commented: the C# compares
NPC names with `==`, this casefolds, like every other lookup in
`ZoneDatabase` — a raid target that silently fails to copy is
indistinguishable from the feature not working.
**Both halves of the auto-copy are decided on the driver thread, and that is
not a stylistic choice.** `QtEventBridge` delivers a coalesced batch some time
after the driver parsed it, and two things keep moving in the meantime.
`player.zone` does, so a `SlainEvent` judged when the GUI drained it is judged
against a zone reached *after* the kill — kill the boss, take the zone line
out, and the copy is silently skipped. And the meter itself does: zoning
CLEARS it (`DpsHandler._on_zoned`), so by the time that batch is drained the
boss has no rows left and a window that formatted then would copy nothing at
all. So `DpsHandler` answers "is this notable" against `player.zone` one
statement after the slain line, formats the parse there too, and publishes
`NotableKillEvent(victim, zone, parse)`. Formatting at the kill also dates the
numbers to the kill rather than to whenever the GUI woke up. The window keeps
only what really is the UI's: whether the user asked for automatic copies, and
the clipboard — which is what #78 said the split should be before the first
attempt put the gate in the window.

`NotableKillEvent` has no `LogEvents.cs` counterpart; EQTool asked the
question inline in `LogParser_DeathEvent` and copied from the same method,
which WPF let it do because there was no thread to cross. One ordering quirk
is pinned by a test rather than left to surprise someone: `EventBus.publish`
runs typed subscribers before the firehose, so the nested announcement
completes before the `SlainEvent` that caused it reaches the bridge, and the
batch reads `NotableKillEvent, SlainEvent, YouZonedEvent`. Harmless — what
matters is that it is ahead of everything the log said next.

`system_clipboard_copy` moved out of `dumpswindow` into `ui/clipboard.py`, so
the two windows that write to the clipboard share one injectable seam.

*Best is a record again* (#83). It reset every launch, which makes it a second
copy of "this session". It now persists per character in
`PlayerInfo.best_damage` — EQTool's granularity and the only one that means
anything — through `core/handlers/dps_persistence.py` on the same
`Before/AfterPlayerChangedEvent` pair `TimerPersistenceHandler` uses.
`last_session` is deliberately NOT persisted: it is explicitly a
within-session record.

**A stored best carries what it is a reading OF.** `reset_session_stats`
already drops the live best when a counting knob moves — a best-dps over 12 s
is not comparable to one over 4 s — and persisting the number alone would let
a restart bring the incomparable reading back. Worse, a live reset **cannot
reach a character who was not logged in when the knob moved**. So the record
stores `measurement_rules_key()` and a record whose fingerprint disagrees with
the current rules is dropped on restore *and overwritten*, so it is gone from
disk rather than merely hidden. The key normalizes numbers, or `20` and `20.0`
would be different rules.

Two things the export path needs. `FightTracker.on_change` fires on every
damage line and `request_save` arms a fresh `threading.Timer` per call, so the
guard is a three-integer comparison — the fingerprint is deliberately left out
of it, because the only thing that changes the rules is `configure()`, which
resets a non-empty best to zero on its way through, and a reset IS a change of
reading. And `DpsHandler` now clears the meter on **`BeforePlayerChangedEvent`**
— Before, not After, so the outgoing character's last fight folds into their
own stats while they are still the active one, and so nothing mutates the
tracker between After firing and the restore. Without it the previous
character's still-live fight was re-merged into the incoming one's session by
the next `end_fight` or `tick`: a bleed the per-character best exists to
prevent.

*The three session controls, together.* `end_session()` and
`remove_last_session()` had **zero callers outside `core/dps.py`** — EQTool had
them as buttons and the port never grew any — and #83's "user-visible way to
reset the stored best" had nowhere to sit, since the footer is three label
cells. All three land in #78's new context menu: Start new session, Clear last
session (disabled when there is none), and Reset best… `FightTracker.reset_best`
is narrower than `reset_session_stats` on purpose (resetting a record is not
abandoning the session you are in) and is the one irreversible action in the
menu, so it confirms, defaulting to No, through an explicitly constructed
`QMessageBox` in `PlainText` — the reason #147 retired `QMessageBox.question`,
and a character name comes from the log.

**That confirmation is also why the reset is the one session control that
goes through `submit_to_driver`.** Start/Clear session rebind app-level
values and finish in microseconds; a reset waits on a MODAL event loop while
the driver keeps parsing and checks for a log-file switch every three
seconds. Switch characters with that dialog open and an unbound reset zeroes
the best `DpsPersistenceHandler` has just restored for the INCOMING
character, and exports the zero over their profile — the lifetime record of
someone the user was not even looking at. So `Backend.dps_best_owner()` is
captured before the dialog and handed back to `reset_dps_best`, which
re-checks it on the driver thread, where the player-change pair also runs and
so cannot overtake the comparison.

**The window deliberately does not re-check locally, and that is the whole
point of `DpsBestResetEvent`.** A local check after the dialog looks like a
free fast path and is not: `submit_to_driver` queues the command and the
driver drains it up to a poll interval later, so the switch can still land
after the local check passed — leaving two deciders that disagree in exactly
the window that matters, with the reporting one wrong. The driver publishes
its answer instead, and `reset_refused` is raised only from that. A command
outcome on the bus rather than a return value because the caller is on
another thread and is no longer waiting, and the bus is the only way back to
the GUI. The rule the whole layer follows: a
GUI-thread write to driver state is fine when nothing can happen in the
middle of it, and needs the driver the moment something can.

**An add-on can see what the app looks like** (post-2.25, SDK 1.4, #166):
`git grep -i skin -- docs/plugins/ sdk/` used to return nothing, so a plugin
author had two options and both were bad — hardcode hex (stale the moment the
user switches skin, and the switch is LIVE) or `import nparseplus.ui.skins`
(host internals, 636 lines that move with every design pass).

**The dispatch already existed; there was nothing to read.**
`app._apply_appearance` sweeps `chrome_surfaces` duck-typing
`apply_chrome`/`apply_skin`, and `pluginbootstrap.attach_live` has always put
plugin windows in that list. So a plugin window defining `apply_skin()` was
already being called on every change. What #166 added is the value side.

`nparseplus_sdk.skin` is a **curated façade, not a re-export**. The other
lazy modules (`events`, `timers`, `ui`) forward wholesale because the class
IS the contract; doing that to `ui/skins.py` would freeze ~30 internal
builders under the additive-only 1.x promise. `ui/pluginskin.py` is the host
half — the ONLY nParse+ code the SDK's `skin` module forwards to, which is
what keeps skins.py/chrome.py/theme.py free to move — and the SDK side is an
explicit `EXPORTS` allowlist like `eqfiles`. It composes all three of those
modules, which is why it is its own module rather than an addition to
skins.py: the semantic accents live in `chrome.py`, and chrome imports skins,
so a snapshot carrying both cannot live in either.

**The value/hue split is the whole contract, and it is measured rather than
asserted.** `AppSkin`'s fields are grouped by owner: the value group (`text`,
`surface`, `field_bg`, `panel_bg`, …) comes from the palette and is
*identical* under all three skins; the hue group (`accent`, `band`, `plate`,
`chip_*`, …) is the skin's. `tests/ui/test_pluginskin.py` asserts the value
group is one set of values across the skins, that `text` on every ground
clears WCAG AA on each — and that `text` on flat `accent` FAILS the same
measurement under EVERY skin (1.2:1 Velious, 1.7:1 Duxa, 3.3:1 Ledger), which
is the gold-on-gold a plugin gets from "just use the accent for everything".
Without that second half the guard is a tautology about constants.

**`band` exists because the obvious pairing is the one that fails.** A
selection wants a filled ground, and the app's own config chrome puts the
skin's caps colour on it (`chrome.accent_text = skin.title_color`) — but that
is tuned for the Settings sidebar and measures **3.4:1 on Ledger's band**,
2.9:1 on a naive `rgba(accent, .28)` tint. An earlier cut of the façade
exported that pairing as `accent_text` while a test asserted it was
unreadable; both could not be true. So the façade carries the band FILL
(`Skin.chrome_band`, a wash on Ledger and opaque stone on Velious — no single
alpha serves both) and takes its foreground from the value group like every
other ground. The guard composites the translucent band over `surface` before
measuring, or Ledger's 22% wash would be flattered by its declared value.

**Sizes are pushed nowhere.** A skin has its own module global
(`skins.set_skin`) but font size and frame opacity live in settings, so
`pluginskin.use_settings(settings)` points the façade at the live tree once in
`create_app` and `current()` reads through. Push-on-change would need every
call site to remember; the Settings root is loaded once per process and its
`general` section is mutated in place, so the reference cannot go stale.
Unbound (a plugin's unit tests, the validate CLI) it answers the shipped
defaults rather than raising.

**`PluginWindow` now dresses itself.** The default `apply_skin()` sets the
overlay stylesheet and the frame clearance, and a new `paintEvent` paints the
skin's plate and glass — via `skinwidgets.paint_skin_frame`, factored out of
`SkinPanel` so the two cannot drift. A plugin window cannot USE `SkinPanel`:
the plugin owns its layout and sets it on `self`, so there is no container to
wrap. Construction calls the private `_dress_from_skin`, **not** the virtual
`apply_skin()` — the base runs inside the subclass's `super().__init__(...)`,
before its own widgets exist, and an override touching them would raise.
`skins.OBJ_TITLE`/`OBJ_ROW_NAME`/`OBJ_ROW_VALUE` became constants (re-exported
as `skin.TITLE`/`ROW_NAME`/`ROW_VALUE`) so "stamp an object name and the label
is skinned" is a contract rather than a coincidence of two string literals.

**The window owns the WHOLE sheet, which is why `skin_stylesheet()` exists.**
Inheriting an `apply_skin()` that calls `setStyleSheet` silently unstyled
every plugin window written before SDK 1.4 — they set their own sheet in
`__init__` and there was no hook to be called by, so the app's duck-typed
sweep found nothing and their QSS survived forever. A replacement discards it
on the first skin change (immediately for a live-enabled plugin, whose
registration re-runs the sweep), which an additive release must not do. So
`_dress_from_skin` keeps the exact sheet it last wrote and re-assembles
`dressing + adopted + skin_stylesheet()` each time: a sheet that is not ours
is ADOPTED and re-applied after our rules (so the plugin still wins), and the
known prefix is stripped first so a subclass that appends to
`self.styleSheet()` contributes its addition rather than a stale copy of ours
— which is also what stops that pattern growing the sheet by one copy per
change. `skin_stylesheet()` is the documented route precisely because it makes
the split explicit instead of reconstructed; `apply_skin()` stays the hook for
what QSS cannot express (the example rebuilds its price cells there, since
their colours are on the items, not in a sheet).

**Neither virtual runs during `super().__init__()`, and the reason is the
host's own error handling.** `skin_stylesheet()` is as virtual as
`apply_skin()`, so calling it from the base constructor runs it before the
subclass has assigned `self._plugin` — and `pluginbootstrap` wraps the window
factory in `try/except … continue`, so an `AttributeError` there does not
degrade the styling, it makes the add-on **silently not appear**. Construction
therefore applies `_dress_from_skin(with_hook=False)`, and `_finalize_skin()`
dresses the window once the subclass is built: from `restore_visibility()` (the
documented last call) and from `showEvent` (a window opened straight from the
tray never called it), idempotent via `_skin_finalized`. Both hooks are
guarded and logged wherever they run — a cosmetic callback must never be what
stops a window from showing, and `showEvent` is not a place an exception can
usefully go.

**Finalization calls the full virtual `apply_skin()`, not just
`_dress_from_skin`**, and that is not tidiness: `app._apply_appearance` runs
only on a *change*, so a startup plugin window never receives a sweep, and an
override doing what a stylesheet cannot express (child widgets, painted
colours, coloured model items) would sit uninitialized until the user happened
to switch skins. Deferring the first dress is what makes calling the virtual
safe here, so the two halves of the fix are the same decision.

**`AppSkin.accent_text` is deprecated, not removed, and that distinction is
the SDK's whole promise.** The first cut of the fix dropped it on the
reasoning that SDK 1.4.0 never reached PyPI so nobody could hold it. That
reasoning is wrong twice: app **v2.26.0 shipped the bundled façade** and its
docs told plugins to read the field, so the contract exists regardless of the
wheel; and "who could possibly be depending on this" is exactly the judgment
an additive-only promise exists to remove. So the NAME stays for the whole 1.x
line and the VALUE is corrected to the palette's `heading` — which keeps a
plugin written against v2.26.0 both loading AND readable, rather than merely
un-crashed. Removal is an SDK 2.0 decision.

`examples/plugins/merchant_prices/window.py` is the reference consumer, and
`docs/plugins/appearance.md` carries the rule with its counter-example.

**An add-on can draw INSIDE the event overlay** (post-2.26, SDK 1.5, #155):
`ctx.add_overlay_region(OverlayRegionSpec(...))` mirrors `add_window` — a
one-line append cleared by `unwind()` alongside `window_specs`/`page_specs` —
and the Qt half materializes it onto the region registry #154 made
runtime-mutable. Region keys are namespaced `plugin.<id>.<key>` like window
keys, and the surface is registered NOWHERE else: no tray entry, no chat
toggle, no Settings > Windows row. A region is not a window; it lives inside
one.

**Display-only, permanently, and that is the whole design** (owner decision on
#155). `WindowTransparentForInput` is a top-level flag with no per-child
exemption, so carving input out for one region means a second always-on-top
window stacked on the overlay — `PluginWindowSpec` with extra steps. So
`OverlayRegionSpec` carries **no** input-related field and never will: an
additive-only 1.x SDK makes a speculative `accepts_input` permanent, and
`test_overlay_region_specs.py` asserts the field set directly rather than
leaving that as intent. **Position mode is the trap** — it drops the flag so
the overlay can be dragged, so a raw widget would receive real clicks there
and nowhere else. `PluginOverlayRegion` seals itself and its whole subtree
with `WA_TransparentForMouseEvents` + `NoFocus`, recursively on `ChildAdded`
**and `ChildPolished`** (a widget class that sets its own focus policy —
`QPushButton` — does so after its parent is assigned), re-swept on show and on
`notify_content_changed`. Sealing rather than accept-and-ignore is load-bearing
in the other direction too: the press must fall THROUGH to the overlay, which
hit-tests the region rectangles itself, or the region could not be dragged at
all. The docs say this in the first paragraph of `docs/plugins/overlay-regions.md`
and name the alternative in the same breath, because "minimap" promises
interactivity to most readers — and say out loud that a real minimap also
needs #156 (player location + Qt-free zone geometry), which is not built.

**`has_content` is required on the spec**, because `_update_visibility` ORs
the per-region predicates and a region with no opinion could never keep the
overlay on screen by itself. It is asked on every visibility pass — i.e. every
overlay event, on the GUI thread — so the first exception RETIRES the
predicate: logged once, never called again, region treated as empty. Silencing
only the log line leaves a permanently broken (or simply expensive) predicate
running on every overlay event for the rest of the session, which is the cost
the guard exists to avoid; the test asserts the INVOCATION count, because a
log count alone passes against exactly that bug.

**A region factory's result is type-screened where it is first seen.**
`OverlayRegionSpec` documents that the factory returns a QWidget and a region
host is placed, resized, moved and stylesheeted by the overlay, so nothing
else can stand in. Carrying anything non-None past the build was a real
hazard, not pedantry: `add_region` raises on `layout()` INSIDE the isolation
guard, and the refusal path then calls `deleteLater()` OUTSIDE one — on the
startup sweep that second exception aborts `build_plugin_ui` for EVERY plugin
and takes the plugin manager page with it. `_discard_region_widget` is
guarded too, so the disposal does not depend on the screen still holding.

**A host-backed capability needs `min_app_version`, not just `requires_sdk`.**
The range is weighed against the SDK the app RESOLVED, not the contract it
IMPLEMENTS, and every released app declares an SDK FLOOR rather than a pin:
v2.27.0 asks for `nparseplus-sdk>=1.4,<2`, so a plain pip/source install of it
resolves SDK 1.5 quite legitimately once that is published — the same seam
`tests/test_sdk_floor.py` exists for, seen from the other side. `SDK_VERSION`
then reports 1.5, `requires_sdk=">=1.5,<2"` passes, and
`HostPluginContext.add_overlay_region` does not exist, so the plugin dies
inside `activate()` and reads as a broken add-on rather than an old app.
`min_app_version` is the one input to the handshake that comes from the host
itself, which is what makes it the lever — and it works RETROACTIVELY, since
v2.27.0's own `check_compat` call already passes its `app_version`. The
example pins it, the docs say to, and the identical (false) "no
`min_app_version` needed" claim on the SDK 1.3 row was corrected with it.

**The content hook holds the overlay WEAKLY, and that is #154's segfault one
step removed.** The overlay owns the region's host widget, the widget holds its
`OverlayRegionContext`, and the context holds the hook — so closing over the
overlay strongly puts the WINDOW in a Python reference cycle, which takes its
destruction away from refcounting and hands it to the cyclic collector; a
QWidget freed there rather than by Qt is a use-after-free the next repaint
walks into. `_region_content_hook` uses a `WeakMethod` for the same reason
`weak_hook` exists, and a test pins the lifetime with the collector switched
off (measured: strong = not freed by refcounting, weak = freed).

**The region tells the overlay; the overlay cannot look inside.**
`OverlayRegionContext.on_content_changed` → `EventOverlayWindow.region_content_changed(key)`
is one call covering three consequences: re-anchor at the new height, re-decide
visibility, and **re-assert the position-mode chrome**. That last one is not
tidiness — a skin change reaches a region as a `setStyleSheet`, and it can land
while position mode is up, which silently dropped the dashed border the user
was dragging by. `_apply_region_chrome` now reads the host's CURRENT sheet
through `_region_own_style` (a suffix strip of `RegionRecord.chrome_suffix`)
rather than trusting the snapshot taken at registration, so turning chrome off
restores what the region actually wears rather than a pre-skin-change copy.

Two smaller decisions. `PluginOverlayRegion` owns its widget's WHOLE
stylesheet and deliberately does NOT adopt one set with `setStyleSheet` the
way `PluginWindow` does — that adoption exists to keep pre-1.4 windows styled,
nothing predates 1.5, and adopting could not work here anyway: the overlay
appends its chrome to this widget's sheet and strips it by suffix, so
re-writing an adopted sheet after that appendix would leave the dashed border
on when position mode ends. Replacing it silently is the #166 failure one step
removed, so `_warn_if_overwritten` says it ONCE and names `skin_stylesheet()`;
a sheet that merely STARTS with ours is the overlay's own chrome, not a
plugin's rules, and must not trip it. And the persisted placement is kept when a plugin is
disabled or uninstalled (nothing prunes `overlay_regions`): a stale key is a
few bytes, losing someone's placed chrome to a reinstall costs more.

Remote: `origin` = github.com/prokopto-dev/nparse-plus (the updater points
there too); `upstream` = nomns/nparse. The release pipeline is exercised
through v1.10.0 (semantic-release + platform builds + flatpak repo publish).
Remaining human step: confirm bidirectional dots with a real EQTool user
in-game (probe-level interop is verified). Post-1.0 parking lot lives in
README.md.
