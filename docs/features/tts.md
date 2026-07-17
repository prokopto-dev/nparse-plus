# Text-to-speech

Trigger alerts, buff-fade warnings, CH-slot calls, and respawn
announcements can all be **spoken**, so you get the information without
looking away from the fight.

## How it works

nParse+ uses your operating system's native speech engine — no cloud, no
extra installs:

| Platform | Engine |
|---|---|
| macOS | `say` |
| Windows | PowerShell / System.Speech |
| Linux | `espeak` (install it from your distro if missing) |

Utterances queue and play one at a time on a background thread, so speech
never stutters the app. The queue is capped — under heavy spam the oldest
pending alerts are dropped, because a stale alert in EQ combat is worse
than none.

## Configuration

[Settings → Audio & Overlays](../settings/audio-overlays.md):

- **Voice** — pick from your system's installed voices, with a **Test**
  button.
- **Volume**.

What gets spoken is decided per feature: each
[trigger](triggers.md) has its own TTS text and toggle; buff-fade warnings
have a speak toggle in
[Settings → Spell Timers](../settings/spell-timers.md).
