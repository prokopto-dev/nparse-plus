# Migrating from GINA

[GINA](https://eq.gimasoft.com/gina/) defined what an EQ trigger tool
should be, and nParse+'s trigger engine covers the same core workflow —
match a log line, show text, speak, start timers — plus everything GINA
never did (maps, spell timers, DPS, the network). It also runs on macOS
and Linux, where GINA can't follow you.

!!! warning "There is no GINA importer"
    Being upfront: **nParse+ cannot read GINA's trigger packages or
    `.gtp`/XML exports today.** Your triggers must be recreated by hand in
    the [Trigger Editor](../windows/trigger-editor.md). For most players
    that's a handful of triggers that actually matter plus the raid pack —
    and the [built-in library](../features/builtin-triggers.md) already
    covers the standard raid AOEs, so start by enabling those before
    recreating anything.

## Concept map

| In GINA | In nParse+ |
|---|---|
| Trigger groups | Folders in the [Trigger Editor](../windows/trigger-editor.md) |
| Search text / "Use regular expressions" | Search text / regex checkbox — same idea |
| `{S}`, `{S1}` capture tokens | `{word}` named captures: `{sender} tells you, '{message}'` |
| `{C}` (character name) | `{c}` / `{C}` — identical |
| Counters | Trigger counters + `{COUNTER}` in outputs |
| Display Text overlay | Alert text on the [Event Overlay](../windows/event-overlay.md) |
| Text-to-speech | Per-trigger [TTS](../features/tts.md), native voices on all platforms |
| Play sound file | Not yet — TTS covers the alert; a spoken word is usually faster to react to than a wav |
| Timer types (Timer, Repeating, Stopwatch) | Timer output: CountDown / CountUp with restart behavior (start new / restart / do nothing) |
| Timer Ending / Timer Ended notices | The same two hooks, per trigger |
| Early-end / cancel patterns | Restart behavior + reset events cover the common cases |
| Character-specific trigger sets | Zone gating per trigger, plus per-character [profiles](../settings/character.md) for everything else |
| Shared trigger packages | Not yet — built-ins ship with the app; trigger export/import is [on the roadmap](../roadmap.md#triggers) |

## Recreating a trigger, quickly

1. Open the [Console](../windows/console.md), make the event happen (or
   fish the line out of an old log), copy the exact line.
2. Trigger Editor → new trigger → paste into the **test box**, write the
   search text until it matches.
3. Add outputs: display text, TTS phrase, timer if you need a countdown.

Most triggers take under a minute once you've done two or three.

## What you gain

Beyond triggers: [spell timers](../windows/spell-timers.md) that know real
durations (GINA users typically maintained timer triggers per buff — you
can delete that whole category), the [maps](../windows/maps.md), the
[DPS meter](../windows/dps-meter.md),
[respawn timers](../features/respawn-timers.md),
[CH chains](../features/ch-chains.md), and the
[PigParse network](../features/sharing.md). And it all runs on the Mac or
Linux box you actually play P99 on.
