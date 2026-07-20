# Screenshot checklist

Drop PNGs with these exact filenames into `docs/assets/screenshots/` and
they appear on the docs site automatically (no page edits needed — until a
file exists, its page shows a dashed "screenshot pending" placeholder
naming it). This file lives in `dev-notes/` and never publishes.

## Generating

Most of these are generated offscreen from real UI + simulated data:

```bash
uv run python tools/capture_screenshots.py            # all automatable shots
uv run python tools/capture_screenshots.py --phase a  # isolated windows only
uv run python tools/capture_screenshots.py --only window--dps-meter,settings--maps
```

The tool builds each window headless (`QT_QPA_PLATFORM=offscreen`), injects
synthetic-but-realistic data (real spell/zone/item names), and writes the PNGs
below. **26 of 29 are automated.** The remaining three need a human on a real
display (see the ⌨ rows): the hero shot and Discord overlay want the live game,
and the tray menu's modal `exec` wedges offscreen (`CAPTURE_TRAY=1` attempts it
on a real display).

Capture tips (for the manual shots): use the **dark theme**, crop tight to the
window (no desktop), and for overlays capture them **over the game** so readers
see real context. Retina/2x captures are fine. PNG only.

## Hero & tray

| File | Used on | What to capture |
|---|---|---|
| ⌨ `home--overview.png` | Home | The money shot: EQ windowed with Spell Timers, DPS Meter, Maps, and an Event Overlay alert visible at once. Mid-fight if possible. |
| ⌨ `tray--menu.png` | First run, Windows index | The open tray menu showing version, sharing status, window toggles, Window Layouts. |

## Windows

| File | Used on | What to capture |
|---|---|---|
| `window--spell-timers.png` | Spell Timers | Several rows across kinds: your buffs (green), a debuff on a target (red), a purple timer — gem icons visible. |
| `window--dps-meter.png` | DPS Meter | A fight with 3+ attackers and the session footer; your row highlighted. |
| `window--maps.png` | Maps | A busy zone map: your marker with direction arrow, another player's dot, a spawn-point countdown, the search box visible. |
| `window--mob-info.png` | Mob Info | After considering a named mob with loot data: respawn time, notable flag, loot prices. |
| `window--event-overlay.png` | Event Overlay | An alert text + a countdown bar over the game (fire a builtin trigger or a `StartTimer-30`). |
| `window--console.png` | Console | The console with a dozen log lines, Pause checkbox visible. |
| `window--trigger-editor.png` | Trigger Editor | Folder tree expanded (Built In folders + a custom folder), a trigger selected showing the form and test box. |
| `window--spell-timers-raid.png` | Spell Timers | Raid mode (group buffs by spell): one spell header with a row per target. |
| `window--update-available.png` | Self-updater, Updating | The update dialog: version heading, per-version release notes, View on GitHub / Later / Download buttons. |
| ⌨ `window--discord.png` | Discord Overlay | The Discord voice overlay over the game with 2+ users, one speaking. |

## Features

| File | Used on | What to capture |
|---|---|---|
| `feature--ch-chain.png` | CH chains | The Event Overlay with a CH lane and 2–3 chips in flight (raid night material — grab it when you can). |
| `feature--sharing-dots.png` | Sharing | The map with several shared player dots (a raid or busy zone). |
| `feature--boats.png` | Boats | The Boats section of Spell Timers with a few boat-route countdowns. |
| `feature--respawn-timers.png` | Respawn timers | The Mob Timers section with `--Dead--` respawn countdowns (incl. a numbered duplicate). |
| `feature--roll-rows.png` | Combat | Amber `/random` roll rows (highest first) plus an `xN` resist counter. |
| `feature--rebuff-flash.png` | Spell Timers | A flagged self-buff that expired, lingering as a flashing **REBUFF** prompt. |
| `feature--overlay-utility.png` | Event Overlay | The overlay's Utility header section with rebuff / out-of-mana lines. |

## Settings pages

One capture per sidebar page of the Settings window, cropped to the whole
window with that page selected:

| File | Page |
|---|---|
| `settings--overview.png` | Any page — shows the sidebar itself (General selected is fine; can be the same capture as `settings--general.png`) |
| `settings--general.png` | General |
| `settings--character.png` | Character (with a real character profile loaded) |
| `settings--friends.png` | Friends (ideally after Load, with names in the box) |
| `settings--spell-timers.png` | Spell Timers |
| `settings--maps.png` | Maps |
| `settings--windows.png` | Windows |
| `settings--audio-overlays.png` | Audio & Overlays |
| `settings--sharing.png` | Sharing (logged-out state is fine) |
| `settings--advanced.png` | Advanced |

## Status

29 wanted, **26 captured** by `tools/capture_screenshots.py`. The 3 remaining
are the ⌨ (manual) rows above — `home--overview`, `window--discord`, and
`tray--menu` — which need a real display/game. Rerun the tool after a UI change
to refresh the automated set; the site tracks reality automatically.
