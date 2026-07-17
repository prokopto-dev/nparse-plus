# CH chains

On P99 raids, clerics coordinate Complete Heal casts by calling their slot
in a chain: `CA 001 CH -- Targetname`. nParse+ parses those calls and turns
them into a visual chain monitor — the same protocol EQTool uses, so mixed
raids stay in sync.

![CH chain lanes on the Event Overlay](../assets/screenshots/feature--ch-chain.png)

## What a call looks like

```
GG 014 CH -- Wreckognize
AAA CH -- Bigtank
RAMP1 CH -- Ramptank
```

An optional raid tag (`GG`, `CA`, …), a chain position (three digits, a
repeated letter, or `RAMP1`-style), the word `CH` (or `RCH` for a re-CH),
and the heal target.

## What you see

Each heal target gets a **lane** on the
[Event Overlay](../windows/event-overlay.md). Every CH call becomes a green
chip labeled with the caster's position, sliding across the lane over the
CH cast time (~11 s) — so the lane literally shows the chain flowing.
Idle lanes linger for a configurable retention period (default 20 s;
[Settings → Audio & Overlays](../settings/audio-overlays.md)) so healers
keep a stable anchor per target, then disappear.

A chain entry goes stale after 20 s without a call for its target, matching
EQTool's chain bookkeeping.

## Your-slot warning

If you're in the chain, nParse+ tracks the cadence and warns you — on
screen and spoken — when your slot is coming up, so you start your cast on
time even when the raid channel is scrolling.
