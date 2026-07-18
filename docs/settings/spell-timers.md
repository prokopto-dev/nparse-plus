# Settings → Spell Timers

Behavior toggles for the [Spell Timers overlay](../windows/spell-timers.md)
and timer engine. (Per-class spell filters live on the
[Character](character.md) page.)

![Spell Timers settings](../assets/screenshots/settings--spell-timers.png)

| Setting | What it does |
|---|---|
| **Sort timers by** | Row order under each header: **Time remaining** (default) puts the soonest-to-expire row at the top, or **Alphabetical** by name. Counters (which never expire) always sort last under Time remaining. |
| **Show only your own spells** | Hide the spell rows other players cast. Boats, mob/roll/custom timers, counters, and rolls are unaffected. |
| **Show random rolls** | Show `/random` results as amber rows ([Combat tracking](../features/combat.md#random-rolls)). |
| **Show boat timers** | Show the **Boats** section (boat schedule countdowns). |
| **Show mob timers** | Show the **Mob Timers** section: mob respawn/Sirran countdowns and FTE raid rules (97%/96%/5-minute). |
| **Show roll timers** | Show the **Roll Timers** section: Ring 8 and Scout Charisa server roll windows. |
| **Show custom timers** | Show the **Custom Timers** section: countdowns started by [triggers](../features/triggers.md), [chat commands](../features/chat-timers.md), and shared remote timers. |
| **Guess ambiguous spells** | When several spells share one cast message, show the best guess instead of nothing. |
| **Auto raid-mode grouping** | Automatically condense the overlay when many casters are around. |
| **Announce respawn-timer expiry** | Speak/alert when a [respawn timer](../features/respawn-timers.md) hits zero. |
| **Buff-fade warning** | Seconds of remaining time at which a buff row switches to its warning state (0 disables). |
| **Speak buff-fade warnings** | Also speak the warning via [TTS](../features/tts.md). |

The category toggles are display-only: hidden timers keep running in the
background (respawn-expiry audio still fires), and re-enabling a category
brings its rows straight back.
