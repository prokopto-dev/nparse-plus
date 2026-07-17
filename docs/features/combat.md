# Combat tracking

The combat engine behind the [DPS Meter](../windows/dps-meter.md), plus the
smaller combat helpers that don't need windows of their own.

## The fight tracker

Every melee hit, spell hit, and non-melee damage line in your log feeds a
per-fight, per-attacker damage model:

- fights are grouped by target; each attacker's **total damage** and
  **trailing DPS over the last 12 seconds** (EQTool's window, so numbers
  are comparable) are tracked live;
- a session summary keeps **best / current / last** fight DPS — fights
  must run longer than 20 seconds to count toward *best*;
- your per-character best persists across sessions.

## Death-loop detection

Four of your own deaths inside two minutes, with no signs of life in
between (no melee, casting, or chat), is a death loop — the respawn-die
cycle every P99 player fears. nParse+ detects it and warns you loudly so
you can sit at your bind point instead of clicking through.

## Pet tracking

Your pet's name is recognized from the master pet-name list, and its rank
and level are inferred from max-hit tables (ported from EQTool) — check
[Mob Info](../windows/mob-info.md) after your pet lands hits to see which
rank the summon gods gave you. Pet damage is attributed near your row in
the DPS meter, and other players' pets are flagged so you don't `/con` them
as camps.

## Random rolls

`/random` results are tracked in a rolling window — amber rows in
[Spell Timers](../windows/spell-timers.md) show each roller's result during
loot rolls, so nobody "forgets" the order. Toggle with "Show random rolls"
in [Settings → Spell Timers](../settings/spell-timers.md). Roll feeds are
also shared over the [PigParse network](sharing.md).

## Faction and exp guessing

Kill messages are cross-referenced against the NPC database to guess
faction hits and exp-worthy kills, matching EQTool's behavior. The built-in
**Exp Timer** trigger (disabled by default) can run a 400-second bar after
kills if you like pacing your grind.

## Quake, Ring War, and FTE alerts

Server-wide events parsed from the log (and enriched over the
[network](sharing.md)): earthquake announcements, Ring War status, and
first-to-engage callouts for contested targets raise overlay alerts.
