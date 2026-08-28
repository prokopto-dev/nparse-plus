# Settings → Character

nParse+ keeps a **profile per character** (created automatically the first
time it sees a character's log). The Character page edits the profile
selected in the top dropdown — switch characters in game and the active
profile follows.

![Character settings](../assets/screenshots/settings--character.png)

| Setting | What it does |
|---|---|
| **Character** | Which profile you're editing. |
| **Class** / **Your Level** | Drive spell-duration math for [Timers](../windows/timers.md) — a level 60 enchanter's Clarity lasts longer than a level 20's. Auto-filled from your own `/who` row (and from level-up / class-detect log lines); a quick `/who` in game refreshes them, even while this window is open. |

!!! note "Clicky items are timed from the item, not from you"

    An item's effect is cast at the **item's** level, so your own level does
    not lengthen it — a level 60 character clicking a low-level item gets the
    item's duration. nParse+ knows a cast came from an item when the spell is
    one your class cannot cast, and takes the level from a table generated
    from the [P99 wiki](https://wiki.project1999.com) (371 spells), falling
    back to the spell's lowest class level when the wiki does not state one.

    Two cases it cannot tell apart, and both deliberately keep the old
    behaviour: an **instant** clicky with no cast time prints only its effect
    message, which reads exactly like another player buffing you; and a spell
    somebody else cast on you or on a mob is timed from *their* class's level,
    which is the better guess there.

| **Zone** | The character's current zone — auto-detected from zone-change lines and from a plain `/who` (a global `/who all` carries no zone, so it can't update this). |
| **Track Skill** | Your tracking skill; draws the tracking-radius circle on the [map](../windows/maps.md) for Druids/Rangers/Bards. |
| **Location sharing** | Per-character: everyone / guild-only / off. Guild-only shows your dot only to guildmates ([Sharing](../features/sharing.md)). |
| **Share timers** | Whether this character's kill timers are shared to (and received from) the network. |
| **Timer recast** | What happens when a tracked detrimental is recast mid-timer: **Restart Current Timer** or **Start New Timer** (stacked rows). Roots always refresh. |
| **Show spells for classes** | Per-class filter checkboxes — hide spell timer rows for classes you don't care about (e.g. hide warrior discs on your cleric). |
