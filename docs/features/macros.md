# Macros & socials

The EQ client keeps your macros — the socials on your hotkey buttons —
**per character**, in the same `<Name>_<Server>.ini` files that hold the
[friends list](friends-sync.md). Your main's carefully built macro set is
invisible to your alt, and there is no way to hand it to a guildmate.

The [Macro Editor](../windows/macro-editor.md) makes that set editable,
copyable between your own characters, and shareable as a file.

## What you can do

1. Set your **EQ install directory** in
   [Settings → General](../settings/general.md).
2. Open **Macro Editor** from the tray, pick a server and character, and
   hit **Load**.
3. Edit any macro's name, colour, and up to five command lines.
4. **Copy to character(s)…** replicates the set onto your alts.
5. **Export…** writes a macro pack; **Import…** merges someone else's.

!!! warning "Edit while logged out"
    The client rewrites the whole character `.ini` when you camp or log
    out, discarding anything changed while it was running. nParse+ warns
    you if it sees the game running, but it will not stop you — so the
    safe habit is to edit macros while that character is logged out. If a
    save looks like it "didn't work", this is almost always why.

Originals are copied into a `socials_backup/` folder beside them before
the first write, the same safety net the friends sync uses.

## Duplicates and conflicts are different things

Users conflate these, so the editor keeps them apart:

| | Same slot | Different slot |
|---|---|---|
| **Same macro** | nothing to do | *duplicate* — skipped, and reported |
| **Different macro** | *conflict* — you are asked what to do | placed |

A **duplicate** is a macro the character already has somewhere, matched on
its name *or* on an identical set of command lines. Imports skip these and
tell you where the existing copy lives; you can place another copy anyway.

A **conflict** is a *different* macro already sitting in the slot an
imported macro wants. Imports keep their original page and button so
muscle memory survives, so when the slot is taken you get a choice:

| Choice | What happens |
|---|---|
| **Overwrite** | The imported macro takes the slot. |
| **Move to free slot** | It lands on the first empty button instead. |
| **Skip** | It is not imported. |

**Find duplicates…** lists every duplicate group in the current character
and jumps to any slot. It never changes anything — clear a slot yourself
if you want it gone.

## Where your macros came from

Every slot carries a badge saying who last wrote it:

| Badge | Meaning |
|---|---|
| ▢ | **From game** — read out of the client's file, not authored here |
| ✎ | **Created in nParse+** — you edited it in the Macro Editor |
| ↧ | **Imported** — it arrived in a macro pack or a copy from another character |

The **Show** filter dims the slots that don't match, rather than hiding
them, so positions stay readable. **Export…** uses the same information:
it defaults to *only what I authored*, so a pack you share doesn't carry
the client's stock macros.

!!! note "\"Last written by\", not \"authored by\""
    nParse+ knows whether a slot still holds exactly what it wrote there.
    It cannot tell who changed it otherwise. Edit a macro in game and it
    correctly flips back to *from game* — but edit it in game back to the
    exact value nParse+ wrote and it will still read as yours.

## The local copy

Each time you Load a character, nParse+ mirrors their macros into its own
data directory. The client's `.ini` stays the source of truth; the mirror
exists so the editor can tell what changed since last time, track the
badges above, and put things back.

The **Local library** tab lists that mirror. Its most useful job is after
a clobber: if the client rewrote the file and dropped macros, they show as
*not in file*, and **Restore from local copy** puts them back into the
working set (you still click Save to character to write them).

The mirror is a convenience, never a dependency. If it can't be written
the editor says so in the status line and saves your macros anyway, and
deleting the directory is a supported reset — everything simply reads as
*from game* again.

## Sharing a pack

Exports are plain JSON with a `format` of `nparseplus-socials`, the same
envelope shape as [trigger packs](triggers.md), so they can be posted,
mailed, or checked into a guild repo. A pack records which character it
came from for display only; that never changes anything on import.

Only socials are covered. Hotbar assignments and key bindings live in
other sections of the same file and are not touched.
