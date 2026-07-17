# Migrating from nParse

nParse+ is a direct fork of [nParse](https://github.com/nomns/nparse)
("Nomns' Parser for Project 1999") — everything you used there exists
here, and your configuration comes along automatically.

## What migrates automatically

On first run, nParse+ looks for your legacy `nparse.config.json` (in the
directory you launch from, or beside the new settings location) and
migrates it:

- **General settings** — EQ logs directory, update-check preference.
- **Sharing settings** — your locationserver host and sharing toggles
  carry into the [nparse sharing mode](../features/sharing.md).
- **Maps / Spells / Discord** — window geometry, opacity, toggled state,
  click-through, and feature settings per window.
- **Custom timers** — converted into real
  [triggers](../features/triggers.md) under a **"Legacy Custom Timers"**
  folder in the [Trigger Editor](../windows/trigger-editor.md), so you
  can keep using them unchanged or upgrade them with TTS/overlay output.
  The originals are also kept verbatim in the settings file, losslessly.

A partial or hand-edited legacy file still migrates — anything
unrecognized falls back to sensible defaults.

## Concept map

| In nParse | In nParse+ |
|---|---|
| `nparse.config.json` in the app folder | `settings.json` in the [platform config dir](../getting-started/first-run.md#where-settings-live) |
| Maps window | The same [Maps window](../windows/maps.md), plus NPC search, spawn-point respawn timers, tracking radius, z-fade tuning |
| Spells window | The new [Spell Timers overlay](../windows/spell-timers.md) — real spell DB durations by class/level instead of cast-time guesses |
| Custom timers | [Triggers](../features/triggers.md) (your old ones land in "Legacy Custom Timers") |
| Discord overlay | The same [Discord overlay](../windows/discord-overlay.md) |
| Self-hosted locationserver sharing | The `nparse` [sharing mode](../features/sharing.md) — same wire protocol, your server keeps working |

## What you gain

Everything in the [EQTool column](../comparison.md) of the comparison:
spell timers from the real spell DB, the trigger engine with TTS and
countdown bars, the DPS meter, CH chains, mob info with loot prices,
respawn timers, death-loop detection, boats — and the
[PigParse network](../features/sharing.md), where the map dots come from
every EQTool user too, not just your nParse friends.
