# Windows & Overlays

Everything in nParse+ hangs off the **system tray icon** — there is no main
window. The tray menu toggles each window, shows sharing status, offers
updates, and holds Settings.

![The tray menu with window toggles](../assets/screenshots/tray--menu.png)

## The tray menu

Click the tray icon to open the menu. Top to bottom:

| Entry | What it does |
|---|---|
| **Version: X.Y.Z** | The running version. Click to open the [releases page](https://github.com/prokopto-dev/nparse-plus/releases). When an update is available this reads **Install update vX.Y.Z** and opens the [update dialog](../features/updater.md) instead. |
| **Check for updates** | Checks GitHub for a newer release on demand and reports back either way — the update dialog if one is found, or a "you're on the latest version" notification. |
| **Sharing: …** | A read-only line showing your current [location-sharing](../features/sharing.md) state (`off` until you enable it). |
| **Select EQ Logs Directory** | Point nParse+ at the folder EQ writes logs to (same as [Settings → General](../settings/general.md)). |
| **Maps · Discord · Spell Timers · Settings · DPS Meter · Mob Info · Console · Trigger Editor** | One checkable toggle per window — a check means the window is shown. These are the windows detailed below. |
| **Position Event Overlay** | Enters the [Event Overlay](event-overlay.md)'s drag-and-resize [positioning mode](event-overlay.md#positioning-it) so you can line it up over the game. |
| **Window Layouts ▸** | Save, apply, and manage named position/size [presets](#window-layout-presets) (Save Current Layout, Reset Window Positions, and per-layout Apply / Replace / Rename / Delete). |
| **Configure Discord** | Opens the legacy [Discord overlay](discord-overlay.md) settings. |
| **Quit** | Exits nParse+. Windows you left open reopen automatically next launch. |

### The windows

| Window | What it shows |
|---|---|
| [Spell Timers](spell-timers.md) | Buff/debuff/song countdowns, grouped per target |
| [DPS Meter](dps-meter.md) | Per-fight damage breakdown and trailing DPS |
| [Maps](maps.md) | Zone map, your position, other players, NPC search, spawn timers |
| [Mob Info](mob-info.md) | The last-considered mob: respawn, loot, prices |
| [Event Overlay](event-overlay.md) | Full-screen trigger alerts, countdown bars, CH lanes |
| [Console](console.md) | Raw log scrollback (great for debugging triggers) |
| [Trigger Editor](trigger-editor.md) | Browse and edit built-in and custom triggers |
| [Discord Overlay](discord-overlay.md) | Discord voice channel over the game (legacy) |

## Working with frameless overlays

Overlay windows are frameless so they sit cleanly over the game:

- **Hover** near the top to reveal the title/menu bar.
- **Drag anywhere** on the window body to move it.
- The **☷ button** in the hover bar toggles a native window frame on and off
  (handy for resizing).
- Per-window **always-on-top**, **opacity**, and **click-through** live in
  [Settings → Windows](../settings/windows.md). Click-through lets game
  clicks pass straight through an overlay — turn it off again from Settings
  when you need to interact with the window.

!!! tip "Keep EQ windowed"
    Overlays can only draw over the game when EQ runs in **windowed or
    borderless** mode, not exclusive fullscreen. This applies on every
    platform.

## Window layout presets

Tray → **Window Layouts** saves and applies named position/size presets
across every window (including the legacy Maps and Discord windows). Typical
use: one layout for grouping, one for raids, one for your second monitor.
Saving with an existing name overwrites it after confirmation.

## In-game chat commands

You can toggle any window without alt-tabbing by saying (any chat channel,
even one nobody hears — an in-game macro works great):

```
toggle_dps        show_maps        hide_console
```

The pattern is `show_`, `hide_`, or `toggle_` plus the window key: `maps`,
`spells`, `dps`, `mobinfo`, `console`, `discord`, or `triggereditor`. Only
messages *you* send count — a groupmate can't blank your overlays.
