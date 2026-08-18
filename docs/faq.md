# FAQ

## Does nParse+ read game memory or modify the game?

No. nParse+ works by **reading the log file** the game itself writes —
the same file you can open in a text editor. It never reads game memory,
injects anything, or touches game files in normal operation. The one
opt-in exception is the [Night Vision fix](features/night-vision.md),
which replaces shader/texture files only when you click Apply (with
backups and a Revert button). Whether any third-party tool is permitted
is always the server's call — check your server's current rules.

That describes **nParse+ itself**. If you enable the optional
[add-on system](plugins/index.md) and install someone else's plugin, that
plugin is ordinary Python running with the same access to your computer as
nParse+ — none of the guarantees above extend to it. Add-ons are off by
default precisely so this promise holds for everyone who never turns them
on.

## Which servers does it work with?

It's built for **Project 1999** (Green, Blue, Red) — the zone, respawn,
and spell databases are P99-era. The parsing itself works with any
classic-era EQ client that writes standard log lines.

## Does it work with WINE / CrossOver / Whisky / Lutris?

Yes — that's the main reason it exists. nParse+ runs natively on your Mac
or Linux desktop and just reads the log file inside the wrapper's
filesystem. See [First run](getting-started/first-run.md) for where the
Logs folder hides per setup.

## Can I import my GINA triggers?

Yes. In the [Trigger Editor](windows/trigger-editor.md), **Import…** reads
GINA `.gtp` packages directly (and the raw XML, if that's what you have).
Trigger groups become categories — the whole nested path, so
`Raid Pack / Sebilis` stays `Raid Pack / Sebilis` — and `{S}` tokens,
timers, early-enders, counters and text-to-speech carry over. Sound media
files, copy-to-clipboard and phrase modifiers have no equivalent here and
are skipped. Nothing is kept until you hit Apply.

See [Migrating from GINA](migrating/from-gina.md) for the full concept map.
Before importing a big raid pack, note the [built-in
triggers](features/builtin-triggers.md) already cover the standard raid
alerts.

## Do I need an account for the shared map dots?

No. The [PigParse network](features/sharing.md) features — map dots,
shared timers, feeds — work without any account. The optional Discord
login adds one thing: uploading an inventory dump to your pigparse.org
character page. Sending dumps to
[p99planner.com](settings/sharing.md#character-dump-upload) instead needs
no account at all, and the local [Character Dumps](windows/character-dumps.md)
library needs nothing either.

## Can other people see where I am?

Only if you turn sharing on, and you control it per character:
everyone / guild-only / off ([Settings →
Character](settings/character.md)). With sharing off nothing leaves your
machine.

With it on, precisely what happens is: typing `/loc` sends that position,
and **the same position is then resent every 10 seconds** as a keepalive so
your dot doesn't go stale on other people's maps. The keepalive stops after
5 minutes with no activity from you, and immediately when you camp — after
which nothing is sent until your next `/loc`.

The distinction worth understanding is that nParse+ only ever learns where
you are *from* `/loc`: the keepalive repeats your last known position and
cannot report movement between them. Your last `/loc` also rides along with
a dragon-roar broadcast and with the NPC activity posts nParse+ makes on
pigparse mode, and dying sends a corpse waypoint (on `nparse` mode only —
the PigParse hub has no waypoint wire).

## Why does macOS say the app is damaged?

The app isn't notarized (that needs a paid Apple developer account), so
macOS quarantines the download. One command clears it — see
[Install on macOS](getting-started/install-macos.md).

## The overlays disappear when I click the game — why?

EQ is probably running in exclusive fullscreen. Overlays can only draw
over a **windowed or borderless** game. This applies on every platform.

## My spell timer durations look wrong.

Set your **class and level** in
[Settings → Character](settings/character.md) — durations scale with
both. Also consider pointing **EQ install directory**
([Settings → General](settings/general.md)) at your install so nParse+
reads your actual `spells_us.txt`.

## Does it work with multiple characters / two boxes?

Per-character [profiles](settings/character.md) are automatic — nParse+
follows the newest log file, so camping to another character switches
everything over. It watches one log at a time, though: for true two-boxing
you'd run two instances with separate settings.

## My log files are getting huge.

The game only ever appends. Turn on **Archive oversized logs** in
[Settings → Advanced](settings/advanced.md).

## Can I share my macros, or copy them to my alts?

Yes — see [Macros & socials](features/macros.md). The
[Macro Editor](windows/macro-editor.md) edits a character's in-game
macros, copies a set onto your other characters, and exports a pack you
can hand to someone else. Edit while the character is logged out: the
client rewrites those files when you camp.

## Are plugins safe to install?

Treat them like any other program you download. A plugin is Python that
runs inside nParse+ with your user account's full permissions — it can read
and write your files and reach the network, and nParse+ cannot sandbox or
audit it. What nParse+ does give you: add-ons are **off entirely** until you
turn them on; every one has to be approved by you the first time it loads,
before it runs; installs from the curated
[registry](plugins/registry.md) are pinned to a reviewed sha256 and refused
if the bytes don't match; and the manager shows where each installed add-on
came from. Note that installing already runs the plugin's module code (that
is how nParse+ checks it loads), so the decision point is the download, not
the first launch. The honest version of all this is in
[Plugin security & trust](plugins/security.md).

## Can I write my own add-on?

Yes. `nparseplus-sdk` is a documented, semantically-versioned contract:
subscribe to parsed events, add your own log parsers, own timer rows,
speak through the TTS engine, and add your own overlay windows and settings
pages. Start at [Developing plugins](plugins/developing.md); the
[API reference](plugins/api.md) lists everything the SDK exports.

## Something's broken — where do I look?

[Troubleshooting](troubleshooting.md), and the two log files listed
there. Bug reports: [GitHub
issues](https://github.com/prokopto-dev/nparse-plus/issues).
