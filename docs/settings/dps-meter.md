# Settings → DPS Meter

The counting rules behind the [DPS Meter overlay](../windows/dps-meter.md).
Everything on this page applies as soon as you hit **Apply** — no restart,
including on fights already running.

| Setting | What it does |
|---|---|
| **Count damage from** | Which damage a row may count: **melee only**, **melee + my spells** (default), or **all damage**. See below. |
| **Spell credit window** | How long after one of your casts a non-melee hit still counts as yours (default 2 s). Only used by the two modes that count non-melee damage. |
| **Count pet damage as mine** | Off by default. Adds your pet's damage to the session **Best / Now / Last** footer. The pet keeps its own row, marked `(pet)` and highlighted as yours, either way — this only decides whose damage the footer is a reading of. |
| **Attacker dropoff** | How long a target's group stays on screen after the last hit against it from anyone (default 40 s; `never` keeps groups until you zone, camp or die). Individual attackers are never dropped, so an opener who stops swinging does not vanish mid-fight. |
| **DPS averaging window** | The span each row's `dps` number is averaged over (default 12 s, EQTool's value). Damage is always divided by the full window, so a burst reads low until the window fills: 400 damage two seconds in shows 33 dps at 12 s. Shorter reacts faster; longer is steadier. |
| **Session stat minimum fight** | A fight must run longer than this before it counts toward the footer (default 20 s, EQTool's rule). Most trash dies faster, which is why that footer can sit at zero all session. |

## What each mode counts

EverQuest logs spell, proc and damage-over-time damage as
`<target> was hit by non-melee for N points of damage.` — a line that names
**no attacker**. Every mode here is a different answer to that.

| Mode | Counts | Trade-off |
|---|---|---|
| **Melee only** | Weapon and fist damage: slashes, crushes, pierces, kicks, punches, backstabs. | A caster's row stays empty all night. This is what nParse+ 2.2–2.4 did by default. |
| **Melee + my spells** *(default)* | Melee, plus non-melee damage landing while you cast or within the credit window after. | Another player nuking the same target in that moment can be miscredited to you. |
| **All damage** | Melee, plus every non-melee line. The ones that follow no cast of yours are listed under `(spell damage)` instead of being credited to you. | Group totals include damage nobody can be identified as the source of. |

If you never cast anything, **melee + my spells** behaves exactly like
**melee only** — nothing ever arms the credit window.

Three limits are worth knowing, and no setting removes them:

- **Damage shields and weapon procs** follow no cast, so they are never
  attributed. A tank's proc damage is under-counted.
- **Two casters** nuking one target inside the same window are
  indistinguishable from your log.
- **Damage over time cannot appear in any mode.** Project 1999 does not log
  DoT ticks — the per-tick message was added in 2003 and removed as
  non-classic — so there is nothing for any parser to read.

## Changing a rule resets the session footer

**Best** and **Now** are running maxima, so nothing in them can be
recomputed once the rule that produced them moves. A best-DPS measured over
a 12-second window is not comparable to one over 4; a best measured while
spell damage counted is unreachable once you switch to melee only. Changing
**Count damage from**, **Spell credit window**, **Count pet damage as
mine**, **DPS averaging window** or **Session stat minimum fight**
therefore clears them. **Last** is untouched — you moved that aside
deliberately.

**Attacker dropoff** does not clear anything: it decides how long a row is
displayed, never what a reading measured.

## Counting your pet

A magician reasonably reads their pet as part of their own output; someone
comparing numbers against a raid parse reasonably does not. nParse+ does not
pick for you — **Count pet damage as mine** is off, so your headline number
means the same thing after upgrading as it did before, and one checkbox
gives you the combined figure if that is what you want.

The pet's row is always marked `(pet)` and highlighted as yours regardless.
Saying whose pet that is, is identification; adding its damage to your
number is measurement, and only the second one is a matter of taste.

## Upgrading from 2.4 or earlier

The old **Melee damage only** checkbox became this page's three-way mode,
and your existing setting is carried over **literally**: ticked becomes
**melee only**, unticked becomes **all damage**. Nothing about what your
meter counts changes because you updated.

**If you play a caster, this is the one setting to change.** Set **Count
damage from** to **melee + my spells** and your nukes start counting. A
fresh install of nParse+ 2.5 or later starts there already; existing
configs are left alone on purpose, because a number that quietly starts
meaning something else after an update is worse than one you had to opt
into.

Likewise **Count pet damage as mine** is off, so a pet's damage stays its
own row until you say otherwise.
