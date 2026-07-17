# nParse+ (nparseplus)

[![CI](https://github.com/prokopto-dev/nparse-plus/actions/workflows/ci.yml/badge.svg)](https://github.com/prokopto-dev/nparse-plus/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/prokopto-dev/nparse-plus/graph/badge.svg)](https://codecov.io/gh/prokopto-dev/nparse-plus)
[![Latest release](https://img.shields.io/github/v/release/prokopto-dev/nparse-plus)](https://github.com/prokopto-dev/nparse-plus/releases/latest)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Platforms](https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-lightgrey)](#install-macos)
[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue)](LICENSE)

**An EverQuest Project 1999 companion overlay for macOS, Windows, and Linux.**

nParse+ tails your EQ log file and gives you, in real time: per-target spell/buff
timers, a full trigger engine with voice alerts, a DPS meter, live maps with an
NPC finder, respawn timers, complete-heal chain tracking, raid-AOE countdowns,
and mob info — everything a Windows user gets from
[EQTool/PigParse](https://github.com/smasherprog/eqtool), running natively in
Python. It began as a fork of [nParse](https://github.com/nomns/nparse) by nomns
and keeps its map overlay heritage.

It reads the log file only — no memory reading, no injection, no game files
modified (the optional Night Vision fix is applied only when you ask).

## Features

| Area | What you get |
|---|---|
| **Spell timers** | Per-target buff/debuff countdowns from the real spell DB (durations by your level/class), gem icons, per-class spell filters, worn-off removal, cooldowns, self-buffs restored across camps |
| **Triggers** | 65 built-in triggers (raid AOEs with countdown bars, invis/levitate fading, failed feign, charm break, death touch, …) + custom triggers with `{name}`/`{c}`/`{COUNTER}` tokens, zone gating, text-to-speech, timers, counters. Start ad-hoc timers from chat: `StartTimer-45-Label` |
| **Maps** | Brewall map set, player marker with direction arrow via `/loc`, tracking-skill radius circle (Druid/Ranger/Bard), smooth z-axis fading tuned per zone (opacity floor/strength/fallback all adjustable), map label size setting, spawn-point timers, waypoints, path recording, **NPC search** (map labels + notable NPCs + live P99-wiki lookup with click-to-flash) |
| **DPS meter** | Per-fight attacker breakdown, 12-second trailing DPS, session best/current/last |
| **Combat automation** | Respawn timers on kill (per-NPC times for 121 zones), faction/exp kill guessing, random-roll tracking, boat schedules, quake/Ring War/FTE alerts, death-loop detection, pet tracking |
| **CH chains** | `CA 001 CH -- Target` calls render as chips sliding across an 11s lane per heal target; voice + on-screen warning when your slot is next |
| **Event overlay** | Full-screen click-through alerts and countdown bars; position/size it over your game window from the tray |
| **Mob info** | `/consider` a mob → respawn time, notable flag, known loot with live auction prices, one-click wiki page |
| **PigParse network** | Live interop with EQTool users: shared player dots on the map (10s cadence, guild-only option), shared Kael pull timers and dragon roars, quake/boat/roll-timer feeds, a shared `/who` player roster (classes appear on buff timers), pigparse.org Discord login with inventory upload to the character browser — plus a self-hostable `nparse` websocket mode and a per-character off switch |
| **Night Vision fix** | One click applies the community shader/sky fix over your EQ install (with backups) — and one click reverts it |
| **Friends sync** | Merge every character's in-game friends list on a server and push it back to all of them (originals backed up) |

## Install (macOS)

Download the latest `nParse+-<version>-macos-arm64.dmg` from the releases
page, drag **nParse+** to Applications, and run:

```bash
xattr -dr com.apple.quarantine "/Applications/nParse+.app"
```

(The app is ad-hoc signed, not notarized — macOS quarantines it on first
download; the command above clears that. Windows users: unzip
`nparseplus-<version>-win64.zip` and run `nparseplus.exe`; Linux users:
untar and run `nparseplus/nparseplus`, or use the Flatpak below.)

nParse+ checks GitHub for new releases at startup (turn off in
Settings) and offers the download from the tray menu.

## Install (Linux Flatpak)

Download `nparseplus-<version>-linux-x86_64.flatpak` from the releases page
and install it with `flatpak install --user <file>` (or a double-click in
GNOME Software / KDE Discover). **Full instructions — prerequisites, EQ
installs outside your home directory, GNOME tray icon, updating,
troubleshooting — are in [docs/install-flatpak.md](docs/install-flatpak.md).**

## Install & run (from source)

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12+ (uv installs Python for you).

```bash
git clone https://github.com/prokopto-dev/nparse-plus.git nparseplus && cd nparseplus
uv sync
uv run python -m nparseplus
```

nParse+ lives in your system tray — the tray icon toggles every window
(Maps, Spell Timers, DPS Meter, Mob Info, Console) and holds Settings.

First run: pick your EQ **Logs** directory from the tray if it isn't
auto-detected (default: `~/Games/EverQuest/Logs`), and make sure logging is on
in game (`/log on`). On macOS with a wine/CrossOver wrapper, that's the same
Logs folder inside your EQ install.

Settings persist to `settings.json` in your platform config directory
(macOS: `~/Library/Application Support/nparseplus/`). An old
`nparse.config.json` is migrated automatically on first run.

## Window tips

- Overlay windows are frameless: **hover** to reveal the title/menu bar, drag
  anywhere to move, ☷ toggles a native frame on/off.
- Tray → **Position Event Overlay** lets you drag/resize the alert region to
  sit exactly over your game window; double-click to lock.
- On the map: type in the search box to find NPCs (including a live P99 wiki
  search for anything not in local data); **☰ NPCs** lists the zone's notables
  with respawn times.

## Development

```bash
uv sync                                   # deps (incl. dev group)
uv run pytest                             # ~750 tests, a few seconds
uv run pytest --cov=nparseplus --cov-branch --cov-report=term-missing
uv run ruff check . && uv run ruff format .
QT_QPA_PLATFORM=offscreen uv run pytest   # headless (CI does this)
```

See [CLAUDE.md](CLAUDE.md) for the architecture guide — the one rule that
matters most: **`nparseplus.core` (and `config`/`net`) never import Qt**; a
test enforces it. Parsers/handlers are 1:1 ports of EQTool's C# (pinned commit
in [CREDITS.md](CREDITS.md)) and the `EQtoolsTests` corpus is our golden spec.

Commits use [Conventional Commits](https://www.conventionalcommits.org/):
`feat:` creates a minor release, `fix:` and `perf:` create a patch release,
and `feat!:`/`fix!:` or a `BREAKING CHANGE:` footer creates a major release.
Run the **Semantic Release** workflow when the release branch is ready; it
calculates the version, updates `pyproject.toml`, `nparseplus.__version__`, and
`CHANGELOG.md`, commits and tags the result, and lets the tag-triggered package
workflow build the release. Because GitHub does not start another workflow for
tags created with `GITHUB_TOKEN`, Semantic Release also dispatches the package
workflow explicitly.

## Building the packages

```bash
uv sync --group build
uv run pyinstaller packaging/nparseplus.spec --noconfirm   # dist/nParse+.app
codesign --force --deep -s - "dist/nParse+.app"            # ad-hoc sign
uv run dmgbuild -s packaging/dmg_settings.py "nParse+" dist/nParse+.dmg
```

Tagged pushes (`v*`) build the macOS DMG, Windows zip, and Linux tarball +
Flatpak bundle in CI and attach them to a GitHub release
([.github/workflows/release.yml](.github/workflows/release.yml)). The
Flatpak wraps the PyInstaller onedir build via the manifest in
[packaging/flatpak/](packaging/flatpak/); building it locally needs a Linux
box with `flatpak-builder` (commands are in the manifest header).

The incremental-update plan for 1.4+ is documented in
[docs/incremental-updates.md](docs/incremental-updates.md). Version 1.4.0 is
the one-time full bootstrap; signed patch updates can begin with 1.4.1.

## Status / roadmap

- **M1 (done):** core engine, spell timers, trigger engine + TTS, settings, maps.
- **M2 (done):** DPS, spawn timers, CH chains, encounter AOEs, event overlay,
  mob info, NPC finder + wiki lookup, z-fade, trigger editor, preferences,
  log archiving.
- **M3 (done):** PigParse network interop (shared map dots, timers, quake/
  boat/roll feeds, loot pricing), nparse websocket fallback, Night Vision
  fix, self-updater, .app/DMG + Windows/Linux release builds.
- **M4 / 1.1 (done):** ONE unified "nParse+ Settings" window (per-window
  opacity sliders + always-on-top, live preview), per-character profiles
  (class/level/zone/track skill) that feed the engine, EQTool spell class
  filters + a Guess Spells toggle, EQTool-style drawn map markers with the
  tracking-skill radius circle, and spell gem icons on timer rows.
- **M5 (done):** the last EQTool parity gaps — shared `/who` player roster
  with PigParse upserts and buff-timer class labels, pigparse.org Discord
  login + inventory upload (character browser), friends-list sync, map
  label sizing, configurable z-fade, the raid-mode toggle — plus the
  settings-window character-profile refresh fix.
- **M6 (done):** community-request sweep — persistent map markers (spawn
  points/way points survive zone changes and restarts), corpse waypoints
  saved locally and shared over the nparse wire, respawn timers that
  survive camping (plus the self-buff restore finally wired), audio on
  respawn-timer expiry, buff-fade pre-warnings (GINA parity), stacked
  detrimental timers (EQTool TimerRecast, roots always refresh),
  hide-others'-dots map toggle, in-game `show_/hide_/toggle_<window>`
  chat commands, and a light theme.
- **Parking lot:** notarization + Windows signing/installer, Flathub
  submission (a `.flatpak` bundle ships with each release since 1.3),
  3D map view.

## License & credits

GPL-3.0 (inherited from nParse). Feature design, trigger library, zone/respawn
databases, and test corpus ported from [EQTool](https://github.com/smasherprog/eqtool)
(MIT) — see [CREDITS.md](CREDITS.md) for details and the pinned source commit.
Maps are the community Brewall/P99 set. Not affiliated with Daybreak Games or
Project 1999.
