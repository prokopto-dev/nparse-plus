# Respawn & zone timers

When you kill a mob, nParse+ starts a respawn countdown automatically —
using per-NPC respawn times where they're known (a database covering 121
zones, ported from EQTool), or the zone's default respawn time otherwise.

## Where timers show up

- As purple rows in [Spell Timers](../windows/spell-timers.md), named after
  the mob.
- On the [map](../windows/maps.md), when the kill matches a spawn point —
  the countdown draws at the spawn location.

## Behavior

- **Timers survive camping.** Respawn timers are saved per character and
  restored (elapsed time subtracted) when you log back in — a 6-hour named
  camp doesn't reset because you took a break.
- **Expiry announcements** — optionally get an on-screen and spoken
  announcement when a respawn timer hits zero ("Announce respawn-timer
  expiry" in [Settings → Spell Timers](../settings/spell-timers.md)).
- **Shared timers** — with [sharing](sharing.md) on and "Share timers"
  enabled for your character, kill timers flow to your groupmates on the
  PigParse network (and theirs to you), so the whole group sees the same
  camp clock. The Kael **Avatar of War lockout** and **dragon roar**
  timers are shared network-wide the same way.
- `/consider` a mob to see its respawn time in
  [Mob Info](../windows/mob-info.md) before you commit to the camp.

## Manual spawn points

Right-click the map to place a spawn-point marker anywhere and start its
timer by hand — useful for camps the database doesn't know or PH cycles you
want to track visually. Markers persist across restarts. See
[Maps](../windows/maps.md#spawn-points-waypoints-and-corpses).
