# Migrating from EQTool

[EQTool/PigParse](https://github.com/smasherprog/eqtool) is nParse+'s
feature blueprint — the parsers, trigger library, zone databases, and
network protocol are 1:1 ports (pinned source commit in
[CREDITS.md](https://github.com/prokopto-dev/nparse-plus/blob/master/CREDITS.md)).
If you know EQTool, you know nParse+; this page is mostly "where things
live now."

The usual reason to switch: **you're not on Windows anymore.** nParse+
runs the same feature set natively on macOS and Linux — and stays on the
same network, so your raid sees no difference.

!!! note "No settings importer"
    There's no importer for EQTool's settings file — you'll re-pick your
    options in the [Settings window](../settings/index.md). It's one
    window; it takes a few minutes. Custom triggers must be recreated in
    the [Trigger Editor](../windows/trigger-editor.md) (the built-ins are
    already there — same library, same folders).

## Where things live

| In EQTool | In nParse+ |
|---|---|
| Settings window tabs | One [Settings window](../settings/index.md) with sidebar pages |
| Trigger window | [Trigger Editor](../windows/trigger-editor.md) — same folder tree, same built-ins, editable with Revert |
| Spell timers (DPS window's buff panel) | [Spell Timers overlay](../windows/spell-timers.md) with gem icons and class filters |
| DPS meter | [DPS Meter](../windows/dps-meter.md) — same 12 s trailing window, session best/current/last |
| Map window | [Maps](../windows/maps.md) — Brewall set, NPC search, spawn points, plus per-zone z-fade tuning |
| Mob info | [Mob Info](../windows/mob-info.md) — same respawn/notable/loot-price data |
| Overlay (alerts, timer bars, CH) | [Event Overlay](../windows/event-overlay.md) — position it from the tray |
| PigParse login / character browser upload | [Settings → Sharing](../settings/sharing.md): Discord login + inventory upload |
| `PigTimer-` chat timers | [Identical](../features/chat-timers.md) — `StartTimer-` works too |
| Night Vision fix | [Settings → Advanced](../settings/advanced.md) — same fix, but with automatic backups and a Revert button |
| Friends tab | [Settings → Friends](../settings/friends.md) — same merge/push, with ini backups |

## Differences worth knowing

- **Timestamps, timers, and shared feeds behave identically by design** —
  when behavior is ambiguous, EQTool's own test corpus is the spec.
- **nParse+ adds** the [Discord overlay](../windows/discord-overlay.md),
  the self-hostable `nparse` sharing mode, window layout presets, in-game
  [window-toggle commands](../windows/index.md#in-game-chat-commands),
  per-window click-through/opacity, and a light theme.
- **EQTool still gets new features first** — nParse+ ports them after.
  If a brand-new EQTool feature is missing, check the
  [release notes](https://github.com/prokopto-dev/nparse-plus/releases)
  or open an issue.
