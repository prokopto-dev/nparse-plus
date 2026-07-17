# Settings → Spell Timers

Behavior toggles for the [Spell Timers overlay](../windows/spell-timers.md)
and timer engine. (Per-class spell filters live on the
[Character](character.md) page.)

![Spell Timers settings](../assets/screenshots/settings--spell-timers.png)

| Setting | What it does |
|---|---|
| **Show only your own spells** | Hide the spell rows other players cast. Boats, respawn/trigger timers, counters, and rolls are unaffected. |
| **Show random rolls** | Show `/random` results as amber rows ([Combat tracking](../features/combat.md#random-rolls)). |
| **Show boat timers** | Show the **Boats** section (boat schedule countdowns). |
| **Show mob respawn timers** | Show the **Custom Timer** section: mob death/respawn countdowns, FTE and shared timers. |
| **Show trigger & chat timers** | Show the **Timers** section: countdowns started by [triggers](../features/triggers.md) and [chat commands](../features/chat-timers.md). |
| **Guess ambiguous spells** | When several spells share one cast message, show the best guess instead of nothing. |
| **Auto raid-mode grouping** | Automatically condense the overlay when many casters are around. |
| **Announce respawn-timer expiry** | Speak/alert when a [respawn timer](../features/respawn-timers.md) hits zero. |
| **Buff-fade warning** | Seconds of remaining time at which a buff row switches to its warning state (0 disables). |
| **Speak buff-fade warnings** | Also speak the warning via [TTS](../features/tts.md). |

The category toggles are display-only: hidden timers keep running in the
background (respawn-expiry audio still fires), and re-enabling a category
brings its rows straight back.
