# Chat-command timers

Start an ad-hoc timer from inside the game — no editor, no setup — by
sending a chat message that starts with `StartTimer-` or `PigTimer-` (they
are interchangeable; `PigTimer` is EQTool's original name, so both tools
respond to the same call):

```
StartTimer-<duration>[-<label>]
PigTimer-<duration>[-<label>]
```

`<duration>` is `ss`, `mm:ss`, or `hh:mm:ss` — no spaces anywhere.

| You type | You get |
|---|---|
| `StartTimer-30` | A 30-second timer named "StartTimer-30" |
| `PigTimer-10:00` | A 10-minute timer |
| `PigTimer-120-inc` | A 2-minute timer named "inc" |
| `PigTimer-1:02:00-LongTimer` | A 1 h 2 m timer named "LongTimer" |

Timers appear as rows in the **Timers** section of
[Spell Timers](../windows/spell-timers.md). Right-click one to clear it
early; hide the section entirely with "Show trigger & chat timers" in
[Settings → Spell Timers](../settings/spell-timers.md).

## The group-coordination trick

**Any sender counts, on any channel** — say, tell, group, guild, auction,
ooc, shout. When your puller calls `PigTimer-16:00-Trak` in group chat,
*everyone's* parser starts the timer — EQTool users included, since this is
the same protocol. One person announces, the whole group is synchronized.

(Contrast with the [window-toggle chat
commands](../windows/index.md#in-game-chat-commands), which only respond to
you — a groupmate can start a timer for you, but can't hide your windows.)
