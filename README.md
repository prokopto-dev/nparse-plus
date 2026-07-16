# nParse+ (nparseplus)

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
| **Spell timers** | Per-target buff/debuff countdowns from the real spell DB (durations by your level/class), worn-off removal, cooldowns, self-buffs restored across camps |
| **Triggers** | 65 built-in triggers (raid AOEs with countdown bars, invis/levitate fading, failed feign, charm break, death touch, …) + custom triggers with `{name}`/`{c}`/`{COUNTER}` tokens, zone gating, text-to-speech, timers, counters. Start ad-hoc timers from chat: `StartTimer-45-Label` |
| **Maps** | Brewall map set, player tracking via `/loc`, smooth z-axis fading tuned per zone, spawn-point timers, waypoints, path recording, **NPC search** (map labels + notable NPCs + live P99-wiki lookup with click-to-flash) |
| **DPS meter** | Per-fight attacker breakdown, 12-second trailing DPS, session best/current/last |
| **Combat automation** | Respawn timers on kill (per-NPC times for 121 zones), faction/exp kill guessing, random-roll tracking, boat schedules, quake/Ring War/FTE alerts, death-loop detection, pet tracking |
| **CH chains** | `CA 001 CH -- Target` calls render as chips sliding across an 11s lane per heal target; voice + on-screen warning when your slot is next |
| **Event overlay** | Full-screen click-through alerts and countdown bars; position/size it over your game window from the tray |
| **Mob info** | `/consider` a mob → respawn time, notable flag, one-click wiki page |

## Install & run (from source)

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12+ (uv installs Python for you).

```bash
git clone <this repo> nparseplus && cd nparseplus
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
uv run pytest                             # ~460 tests, a few seconds
uv run ruff check . && uv run ruff format .
QT_QPA_PLATFORM=offscreen uv run pytest   # headless (CI does this)
```

See [CLAUDE.md](CLAUDE.md) for the architecture guide — the one rule that
matters most: **`nparseplus.core` (and `config`/`net`) never import Qt**; a
test enforces it. Parsers/handlers are 1:1 ports of EQTool's C# (pinned commit
in [CREDITS.md](CREDITS.md)) and the `EQtoolsTests` corpus is our golden spec.

## Status / roadmap

- **M1 (done):** core engine, spell timers, trigger engine + TTS, settings, maps.
- **M2 (nearly done):** DPS, spawn timers, CH chains, encounter AOEs, event
  overlay, mob info, NPC finder, z-fade; trigger editor & settings UI in flight.
- **M3 (next):** PigParse network interop (shared player locations, dragon
  roars, wiki pricing), Night Vision fix, self-updater, signed .app/DMG +
  Windows/Linux builds.
- **Stretch:** 3D map view.

## License & credits

GPL-3.0 (inherited from nParse). Feature design, trigger library, zone/respawn
databases, and test corpus ported from [EQTool](https://github.com/smasherprog/eqtool)
(MIT) — see [CREDITS.md](CREDITS.md) for details and the pinned source commit.
Maps are the community Brewall/P99 set. Not affiliated with Daybreak Games or
Project 1999.
