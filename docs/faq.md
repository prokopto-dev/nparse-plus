# FAQ

## Does nParse+ read game memory or modify the game?

No. nParse+ works by **reading the log file** the game itself writes —
the same file you can open in a text editor. It never reads game memory,
injects anything, or touches game files in normal operation. The one
opt-in exception is the [Night Vision fix](features/night-vision.md),
which replaces shader/texture files only when you click Apply (with
backups and a Revert button). Whether any third-party tool is permitted
is always the server's call — check your server's current rules.

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

Not yet — there's no importer. See
[Migrating from GINA](migrating/from-gina.md) for the concept map and a
fast manual workflow; the [built-in
triggers](features/builtin-triggers.md) already cover the standard raid
alerts.

## Do I need an account for the shared map dots?

No. The [PigParse network](features/sharing.md) features — map dots,
shared timers, feeds — work without any account. The optional Discord
login only adds inventory upload to your pigparse.org character page.

## Can other people see where I am?

Only if you turn sharing on, and you control it per character:
everyone / guild-only / off ([Settings →
Character](settings/character.md)). With sharing off nothing leaves your
machine. Locations are only sent when you type `/loc`.

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

## Something's broken — where do I look?

[Troubleshooting](troubleshooting.md), and the two log files listed
there. Bug reports: [GitHub
issues](https://github.com/prokopto-dev/nparse-plus/issues).
