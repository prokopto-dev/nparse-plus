# Triggers

A trigger watches every log line for a pattern and, on a match, can show
alert text on the [Event Overlay](../windows/event-overlay.md), speak via
[TTS](tts.md), start a countdown/countup timer bar, and count matches. The
model is a 1:1 port of EQTool's, so trigger behavior matches what EQTool
users see; the workflow will feel familiar to GINA users too.

Create and edit triggers in the
[Trigger Editor](../windows/trigger-editor.md); a library of
[built-ins](builtin-triggers.md) ships with the app.

## Search text

- **Plain text** (default): a case-insensitive substring match against the
  log line (without its timestamp).
- **Regex**: full regular-expression matching. EQ names can contain
  backticks and spaces, so name-shaped captures should allow them.

### Tokens

Tokens work in the search text and in outputs:

| Token | In search text | In output text |
|---|---|---|
| `{c}` / `{C}` | Your current character's name (updates when you switch characters) | Your character's name |
| `{word}` (any word) | Becomes a named capture matching an EQ name — letters, digits, backticks, spaces | The captured value from the match |
| `{COUNTER}` | — | The trigger's current match tally |

Example — a tell alert:

```
Search text:  {sender} tells you, '{message}'
Display text: Tell from {sender}
TTS text:     {sender} tells you {message}
```

Example — counting with `{COUNTER}`:

```
Search text:  You have slain {npc}
Display text: Kill #{COUNTER}: {npc}
```

## Outputs

Each trigger can combine any of:

- **Display text** — shown large on the Event Overlay in a chosen color
  (Red, Yellow, Gold, Orange, ForestGreen, SteelBlue, MediumPurple, White),
  auto-cleared after the
  [alert duration](../settings/audio-overlays.md).
- **Audio** — text-to-speech of a separate (usually shorter) phrase, with an
  optional **interrupt speech** flag that cuts off any in-progress
  announcement so this one is heard right away ([TTS](tts.md)).
- **Timer** — a **CountDown** or **CountUp** bar on the Event Overlay (and
  a row in the **Custom Timers** section of
  [Spell Timers](../windows/spell-timers.md)), with:
  - duration and bar color
  - **restart behavior** when the trigger fires again mid-timer: start a
    second timer, restart the running one, or do nothing
  - optional **timer ending** / **timer ended** alerts (text and/or
    speech) — e.g. "10 seconds left on your camp timer"
- **Counter** — tallies matches for `{COUNTER}`; the tally restarts with
  the trigger.

## Zone gating

Triggers can be restricted to specific zones, so a Plane of Growth trigger
never fires from someone quoting the emote in the Commonlands. The
[built-in](builtin-triggers.md) encounter triggers ship zone-gated.

## Character scope

By default every trigger fires for every character. Scope one to specific
characters — choose them in the
[Trigger Editor](../windows/trigger-editor.md) — and it fires only while one
of those characters is logged in: no [CH-chain](ch-chains.md) alerts on your
warrior, no FTE calls while leveling an alt. Enabling and disabling is
automatic as you switch characters, and the default
[built-ins](builtin-triggers.md) stay global. The scope is per install and is
cleared on [export/import](#sharing-triggers-export-import) — it names your own
characters, which mean nothing on someone else's machine.

## Sharing triggers (export & import)

Triggers travel as plain JSON files, so you can share them with friends
and guildies or keep themed packs around.

**Export** (Trigger Editor → **Export…**) writes what's selected in the
tree: a selected trigger exports alone, a selected folder exports its
triggers, and with nothing selected everything exports. Unmodified
[built-ins](builtin-triggers.md) are left out — every install already has
them — but built-ins you've customized are included.

**Import** (Trigger Editor → **Import…**) reads:

- **nParse+ trigger files** (`.json`) — the format Export writes.
- **GINA trigger packages** (`.gtp`, or their raw XML) — groups become
  categories, `{S}`-style tokens and timers carry over as-is. Sound
  files, clipboard actions, and phrase modifiers are skipped; see
  [Migrating from GINA](../migrating/from-gina.md).

Imported triggers arrive as your own editable copies (never merged into
the built-in library), duplicates you already have are skipped, and
nothing is kept until you click **Apply / Save**.

A hand-crafted pack is just JSON — an object with a
`"format": "nparseplus-triggers"` key and a `"triggers"` list, or even a
bare list of trigger objects.

## Tips

- Grab exact log lines from the [Console](../windows/console.md) and use
  the Trigger Editor's **test box** to check matches before saving.
- Coming from GINA? See
  [Migrating from GINA](../migrating/from-gina.md) for a concept mapping
  (`{S}` → captures, Timer Behaviors → restart behavior, and so on).
