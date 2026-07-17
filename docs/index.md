# nParse+

**An EverQuest Project 1999 companion overlay for macOS, Windows, and Linux.**

nParse+ tails your EQ log file and gives you, in real time: per-target spell
and buff timers, a full trigger engine with voice alerts, a DPS meter, live
maps with an NPC finder, respawn timers, complete-heal chain tracking,
raid-AOE countdowns, and mob info with live loot pricing.

It reads the log file only — no memory reading, no injection, no game files
modified (the optional [Night Vision fix](features/night-vision.md) is applied
only when you ask, with backups).

![The nParse+ overlays over the game](assets/screenshots/home--overview.png)

## Standing on the shoulders of giants

nParse+ exists because of three excellent tools that came before it, and it
gladly acknowledges its debt to each:

- **[nParse](https://github.com/nomns/nparse)** by nomns — the original
  cross-platform P99 parser this project is forked from (GPL-3.0). The map
  overlay heritage and the self-hostable location sharing come from here.
- **[EQTool / PigParse](https://github.com/smasherprog/eqtool)** by
  smasherprog and contributors (MIT) — the Windows tool whose feature set,
  trigger library, zone/respawn databases, and network protocol nParse+ ports
  1:1. nParse+ interoperates live with EQTool users on the PigParse network.
- **[GINA](https://eq.gimasoft.com/gina/)** by Gimagukk — the archetypal EQ
  trigger tool whose trigger/overlay/TTS model shaped what players expect
  from any parser.

See [Why nParse+](comparison.md) for how the feature sets compare, and
[Migrating](migrating/index.md) if you're coming from one of them.

## What you get

| Area | What you get |
|---|---|
| **[Spell timers](windows/spell-timers.md)** | Per-target buff/debuff countdowns from the real spell DB (durations by your level/class), gem icons, per-class spell filters, worn-off removal, cooldowns, self-buffs restored across camps |
| **[Triggers](features/triggers.md)** | 65 built-in triggers (raid AOEs with countdown bars, invis/levitate fading, failed feign, charm break, death touch, …) plus custom triggers with `{name}`/`{c}`/`{COUNTER}` tokens, zone gating, text-to-speech, timers, and counters. Start ad-hoc timers from chat: `StartTimer-45-Label` |
| **[Maps](windows/maps.md)** | Brewall map set, player marker with direction arrow via `/loc`, tracking-skill radius circle (Druid/Ranger/Bard), smooth z-axis fading tuned per zone, spawn-point timers, waypoints, path recording, and NPC search with live P99-wiki lookup |
| **[DPS meter](windows/dps-meter.md)** | Per-fight attacker breakdown, 12-second trailing DPS, session best/current/last |
| **[Combat tracking](features/combat.md)** | Respawn timers on kill (per-NPC times for 121 zones), faction/exp kill guessing, random-roll tracking, quake/Ring War/FTE alerts, death-loop detection, pet tracking |
| **[CH chains](features/ch-chains.md)** | `CA 001 CH -- Target` calls render as chips sliding across an 11-second lane per heal target; voice and on-screen warning when your slot is next |
| **[Event overlay](windows/event-overlay.md)** | Full-screen click-through alerts and countdown bars; position it over your game window from the tray |
| **[Mob info](windows/mob-info.md)** | `/consider` a mob → respawn time, notable flag, known loot with live auction prices, one-click wiki page |
| **[PigParse network](features/sharing.md)** | Live interop with EQTool users: shared player dots on the map, shared Kael pull timers and dragon roars, quake/boat/roll-timer feeds, a shared `/who` roster, pigparse.org Discord login with inventory upload — plus a self-hostable `nparse` websocket mode |
| **[Night Vision fix](features/night-vision.md)** | One click applies the community shader/sky fix over your EQ install (with backups) — and one click reverts it |
| **[Friends sync](features/friends-sync.md)** | Merge every character's in-game friends list on a server and push it back to all of them (originals backed up) |
| **[Boats](features/boats.md)** | Boat schedule tracking synced from sightings, yours or shared over the network |

## Get going

- [Install on macOS](getting-started/install-macos.md) ·
  [Windows](getting-started/install-windows.md) ·
  [Linux (Flatpak)](getting-started/install-flatpak.md) ·
  [Linux (tarball)](getting-started/install-linux-tarball.md)
- [First run](getting-started/first-run.md) — point it at your logs and go
- [Migrating from nParse, GINA, or EQTool](migrating/index.md)
- [Settings Reference](settings/index.md) · [FAQ](faq.md) ·
  [Troubleshooting](troubleshooting.md)
- [Roadmap](roadmap.md) — what's coming · [Changelog](changelog.md) —
  what's shipped

nParse+ is free software (GPL-3.0), not affiliated with Daybreak Games or
Project 1999. Full attribution lives in
[CREDITS.md](https://github.com/prokopto-dev/nparse-plus/blob/master/CREDITS.md).
