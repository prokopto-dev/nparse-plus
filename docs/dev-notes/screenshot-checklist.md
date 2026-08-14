# Screenshot checklist

Drop PNGs with these exact filenames into `docs/assets/screenshots/` and
they appear on the docs site automatically (no page edits needed — until a
file exists, its page shows a dashed "screenshot pending" placeholder
naming it). This file lives in `dev-notes/` and never publishes.

**35 wanted; 33 are automated and current as of v2.9.2; 2 need a human.**
The two are marked ⌨ below and are tracked in the "remaining screenshots"
issue — both need EverQuest actually running on a real display.

## Generating

```bash
uv run python tools/capture_screenshots.py            # all automatable shots
uv run python tools/capture_screenshots.py --phase a  # isolated windows only
uv run python tools/capture_screenshots.py --only window--dps-meter,settings--maps
```

The tool builds each window headless (`QT_QPA_PLATFORM=offscreen`), injects
synthetic-but-realistic data (real spell/zone/item names), and writes the PNGs
below. Phase A builds isolated windows on a bare `QApplication`; phase B boots
the full app (`create_app`) for the maps window, the tray menu and the README
product shot, and runs in its own subprocess because a process may hold only
one `QApplication`.

**Rerun the whole set after any UI change and look at the output.** A missing
screenshot degrades to an honest placeholder; a stale one silently lies, which
is worse. Two failure modes have actually bitten this repo:

- **The settings pages are selected by sidebar *title*, never row index.** They
  used to be indexed, and when a "DPS Meter" page landed at row 5 every later
  index shifted by one — `settings--maps.png` would have captured the DPS page
  and Advanced would never have been captured at all, each saved under a
  filename that lied about it. A renamed page now stops the run instead.
- **Sample content ages too.** `window--update-available.png` shipped a mock
  offering v1.12.0 long after the app reached 2.9.x. Keep the release notes in
  `cap_update_dialog` roughly current when you regenerate.

Capture tips (for the two manual shots): crop tight to the window (no desktop),
and capture overlays **over the game** so readers see real context. Retina/2x
captures are fine. PNG only.

## README / hero

| File | Used on | What to capture |
|---|---|---|
| `readme--product-shot.png` | README.md | **Automated.** Four windows composed on one canvas: the Event Overlay alert banner, maps, spell timers, DPS meter. |
| ⌨ `home--overview.png` | Docs home | **Manual.** EQ windowed with Spell Timers, DPS Meter, Maps and an Event Overlay alert visible at once, mid-fight. |

These two are **not interchangeable** — do not "fix" one with the other.
`readme--product-shot` is a *product shot*: no game behind it, composed
offscreen from the same windows and synthetic data as every other automated
shot, so it regenerates with them and can never depict a UI that no longer
ships. `home--overview` is the *hero*: a photograph of the real thing, over a
live client, which is the one claim a composed image cannot make.

## Tray

| File | Used on | What to capture |
|---|---|---|
| `tray--menu.png` | First run, Windows index | The open tray menu showing version, sharing status, window toggles, UI Skin, Window Layouts. |

## Windows

| File | Used on | What to capture |
|---|---|---|
| `window--spell-timers.png` | Spell Timers | Several rows across kinds: your buffs (green), a debuff on a target (red), a purple timer — gem icons visible. |
| `window--dps-meter.png` | DPS Meter | A fight with 3+ attackers and the session footer; your row highlighted; the damage-source mode badge in the title bar. |
| `window--maps.png` | Maps | A busy zone map: your marker with direction arrow, another player's dot, a spawn-point countdown, the hover chrome visible. |
| `window--mob-info.png` | Mob Info | After considering a named mob with loot data: respawn time, notable flag, loot prices. |
| `window--event-overlay.png` | Event Overlay | An alert (kicker + headline) plus draining countdown bars. |
| `window--console.png` | Console | The console with a dozen log lines, Pause checkbox visible. |
| `window--trigger-editor.png` | Trigger Editor | Folder tree expanded (Built In folders + a custom folder), a trigger selected showing the form and test box. |
| `window--trigger-activity.png` | Trigger Editor, Migrating from GINA | The Activity tab with a handful of fires: a match, a muted timer follow-up, nested GINA folder paths in the Group column. |
| `window--macro-editor.png` | Macro Editor, Macros & socials | A character loaded with a populated Page 1 grid (origin badges visible), one macro selected showing the name/colour/line form. |
| `window--character-dumps.png` | Character Dumps | Two characters in the tree, one with both Inventory and Spellbook plus history behind it; a spellbook snapshot selected, showing its entries and the "since the last one" change line. |
| `window--spell-timers-raid.png` | Spell Timers | Raid mode (group buffs by spell): one spell header with a row per target. |
| `window--update-available.png` | Self-updater, Updating | The update dialog: version heading, per-version release notes, View on GitHub / Later / Download buttons. |
| ⌨ `window--discord.png` | Discord Overlay | **Manual.** The Discord voice overlay over the game with 2+ users, one speaking. |

## Features

| File | Used on | What to capture |
|---|---|---|
| `feature--ch-chain.png` | CH chains | The Event Overlay with a CH lane and 2–3 chips in flight. |
| `feature--sharing-dots.png` | Sharing | The map with several shared player dots (a raid or busy zone). |
| `feature--boats.png` | Boats | The Boats section of Spell Timers with a few boat-route countdowns. |
| `feature--respawn-timers.png` | Respawn timers | The Mob Timers section with `--Dead--` respawn countdowns (incl. a numbered duplicate). |
| `feature--roll-rows.png` | Combat | Amber `/random` roll rows (highest first) plus an `xN` resist counter. |
| `feature--rebuff-flash.png` | Spell Timers | A flagged self-buff that expired, lingering as a flashing **REBUFF** prompt. |
| `feature--overlay-utility.png` | Event Overlay | The overlay's Utility header section with rebuff / out-of-mana lines. |

## Settings pages

One capture per sidebar page of the Settings window, cropped to the whole
window with that page selected. The order here matches
`SETTINGS_PAGES` in `tools/capture_screenshots.py`, which drives the capture
by page title.

| File | Page |
|---|---|
| `settings--overview.png` | Any page — shows the sidebar itself (a duplicate of `settings--general.png`) |
| `settings--general.png` | General |
| `settings--appearance.png` | Appearance |
| `settings--character.png` | Character (with a real character profile loaded) |
| `settings--friends.png` | Friends (ideally after Load, with names in the box) |
| `settings--spell-timers.png` | Spell Timers |
| `settings--dps-meter.png` | DPS Meter |
| `settings--maps.png` | Maps |
| `settings--windows.png` | Windows |
| `settings--audio-overlays.png` | Audio & Overlays |
| `settings--sharing.png` | Sharing (logged-out state is fine) |
| `settings--advanced.png` | Advanced |

**Two of these are captured but not yet shown anywhere.**
`settings--appearance.png` and `settings--dps-meter.png` have no `![…]`
reference on any docs page, so nothing renders them — the pages that would
host them predate both features. That is a docs-prose gap, not a capture gap;
the PNGs are current and waiting.

## Status

| | Count |
|---|---|
| Wanted | 35 |
| Automated, regenerated and visually checked at v2.9.2 | 33 |
| Manual, still outstanding | 2 (`home--overview`, `window--discord`) |

All 33 automated shots were regenerated after the visual redesign (skins), the
chrome layer, the Appearance and DPS Meter settings pages, the #103/#108 alert
changes and the new app mark. Before that pass the oldest of them
(`window--console`, `window--trigger-editor`, `window--macro-editor`,
`window--update-available`, `window--trigger-activity`) predated the chrome
layer entirely and still showed default-Fusion blue selection bands.
