# Why nParse+

nParse+ set out to give every platform what Windows players get from
EQTool/PigParse — and to fold in the best of nParse and the trigger workflow
GINA made standard. This page shows where it stands next to those tools.

!!! quote "First, the debt"
    None of this is a knock on the tools below — nParse+ literally *is*
    [nParse](https://github.com/nomns/nparse) (GPL-3.0 fork lineage) plus a
    1:1 port of [EQTool](https://github.com/smasherprog/eqtool)'s feature set
    and data (MIT, pinned source commit in
    [CREDITS.md](https://github.com/prokopto-dev/nparse-plus/blob/master/CREDITS.md)),
    with [GINA](https://eq.gimasoft.com/gina/) as the archetype for how
    trigger tools should feel. If you're happy on one of them, keep using it —
    nParse+ interoperates with EQTool users on the network either way.

## Feature matrix

Legend: ✅ full support · 🟡 partial / different approach · ❌ not available

| Feature | nParse+ | [EQTool](https://github.com/smasherprog/eqtool) | [nParse](https://github.com/nomns/nparse) | [GINA](https://eq.gimasoft.com/gina/) |
|---|---|---|---|---|
| Platforms | ✅ macOS, Windows, Linux | ❌ Windows only | ✅ cross-platform | ❌ Windows only |
| Spell/buff timers (real spell DB, level-scaled) | ✅ | ✅ | 🟡 casting-based guesses | ❌ |
| Custom triggers (text + TTS + timers + counters) | ✅ | ✅ | 🟡 simple custom timers | ✅ |
| Built-in raid trigger library | ✅ (ported from EQTool) | ✅ | ❌ | ❌ (community shares packages) |
| Trigger tokens (`{name}`, `{c}`, `{COUNTER}`) | ✅ | ✅ | ❌ | ✅ (`{S}`, `{C}` style) |
| Zone-gated triggers | ✅ | ✅ | ❌ | ❌ |
| Maps with live position | ✅ | ✅ | ✅ | ❌ |
| NPC search + live P99 wiki lookup | ✅ | ✅ | ❌ | ❌ |
| Spawn-point / respawn timers (per-NPC times) | ✅ | ✅ | 🟡 manual spawn timers | ❌ |
| DPS meter | ✅ | ✅ | ❌ | ❌ |
| CH chain tracking | ✅ | ✅ | ❌ | ❌ |
| Mob info on `/consider` (respawn, loot, prices) | ✅ | ✅ | ❌ | ❌ |
| PigParse network (shared map dots, timers, feeds) | ✅ live interop | ✅ | ❌ | ❌ |
| Self-hostable location sharing | ✅ (`nparse` websocket mode) | ❌ | ✅ | ❌ |
| Death-loop detection | ✅ | ✅ | ❌ | ❌ |
| Boat schedules | ✅ | ✅ | ❌ | ❌ |
| Night Vision fix (apply/revert) | ✅ | ✅ | ❌ | ❌ |
| Friends-list sync across characters | ✅ | ✅ | ❌ | ❌ |
| Per-character profiles | ✅ | ✅ | ❌ | ✅ (per-character trigger sets) |
| Text-to-speech alerts | ✅ native on all 3 OSes | ✅ | ❌ | ✅ |
| Discord overlay | ✅ | ❌ | ✅ | ❌ |
| Self-updater | ✅ | ✅ | ❌ | ✅ |
| Trigger import from GINA | ❌ ([manual recreation](migrating/from-gina.md)) | 🟡 | ❌ | — |

## What only nParse+ offers

- **The EQTool feature set outside Windows.** Everything in the EQTool column
  above, running natively on macOS and Linux (including a
  [Flatpak](getting-started/install-flatpak.md) with `flatpak update`
  support) as well as Windows.
- **Live PigParse interop from any OS** — your map dots, shared timers, and
  feeds appear to EQTool users and theirs to you, from a Mac or Linux box.
- **Both sharing networks in one tool**: the PigParse hub *and* nParse's
  self-hostable websocket mode for private groups.
- **One unified settings window** with per-character profiles, per-window
  opacity/always-on-top/click-through, spell class filters, and window layout
  presets.
- **nParse's map + Discord overlay heritage** combined with EQTool's
  spell/trigger/combat engine — previously you had to pick one.

## What the others still do better

Honesty cuts both ways:

- **GINA** has a huge ecosystem of shared trigger packages and an
  import/export format nParse+ cannot read (yet) — see
  [Migrating from GINA](migrating/from-gina.md).
- **EQTool** is the reference implementation of the PigParse network and gets
  its own features first; nParse+ ports them after.
- **nParse** (upstream) is smaller and simpler if all you want is the map.

If you're coming from any of these, the [Migrating](migrating/index.md) pages
map each tool's concepts to their nParse+ equivalents.
