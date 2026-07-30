# Troubleshooting

## First: the two log files

Since v1.4.1 nParse+ writes its own diagnostics — check these before
anything else, and attach them to bug reports:

| File | Contents |
|---|---|
| `crash.log` | Unhandled-error tracebacks |
| `nparseplus.log` | Warnings and info — sharing reconnects, update checks, applied defaults |

Where they live:

| Platform | Location |
|---|---|
| macOS | `~/Library/Logs/nparseplus/` |
| Windows | `%LOCALAPPDATA%\nparseplus\nparseplus\Logs\` |
| Linux (tarball/source) | `~/.local/state/nparseplus/log/` |
| Linux (Flatpak) | `~/.var/app/io.github.prokopto_dev.nparse_plus/.local/state/nparseplus/log/` |

## Nothing is parsing / windows never update

1. Is logging on in game? Type `/log on` (it persists, but check).
2. Is the **EQ Logs directory** right?
   ([Settings → General](settings/general.md)) It must be the folder
   containing `eqlog_<Name>_<server>.txt` files.
3. Open the [Console](windows/console.md) and say something in game — if
   it appears, parsing is fine and the issue is elsewhere.
4. Remember nParse+ follows the **newest** log file — a stray recently
   -touched `eqlog_` file from another character can steal the tail.

## Overlays vanish behind the game, or won't stay on top

- Run EQ **windowed or borderless**, never exclusive fullscreen (all
  platforms).
- Check the window's **On top** flag in
  [Settings → Windows](settings/windows.md).
- On Linux, nParse+ must run through X11/XWayland (it does this by
  default) — don't force `QT_QPA_PLATFORM=wayland`; native Wayland
  windows cannot stay on top or remember positions.

## A window is stuck somewhere / can't be clicked

- **Click-through on?** You can't grab it with the mouse — turn
  click-through off in [Settings → Windows](settings/windows.md).
- **Off-screen** (e.g. after unplugging a monitor)? Apply any saved
  layout from tray → **Window Layouts**, or toggle the window off and on.

## macOS

- **"App is damaged / unverified developer"** — the quarantine flag; run
  the [`xattr` command](getting-started/install-macos.md#2-clear-the-quarantine-flag).
  Needed again after each update.
- **No tray icon?** It's in the **menu bar** (top right), not the Dock.

## Windows

- **SmartScreen blocks the exe** — More info → Run anyway
  ([details](getting-started/install-windows.md)).

## Linux

- **No tray icon on GNOME** — install the
  [AppIndicator extension](https://extensions.gnome.org/extension/615/appindicator-support/).
- **Flatpak can't see the EQ folder** — it's outside `$HOME`; grant
  access with `flatpak override`
  ([guide](getting-started/install-flatpak.md#4-eq-installs-outside-your-home-directory)).
- **Discord overlay crashes/blank** — the Chromium sandbox issue; see the
  [tarball notes](getting-started/install-linux-tarball.md#wayland-and-environment-defaults).

## Sharing won't connect / no dots

1. Sharing mode set? ([Settings → Sharing](settings/sharing.md)) — and
   restart after changing it.
2. This character's location sharing on?
   ([Settings → Character](settings/character.md))
3. You only receive a zone's players after **sending** a location — type
   `/loc` in game.
4. Check `nparseplus.log` for reconnect reasons (paths above); the tray
   menu's top line shows live sharing status.

## Spell timers are wrong or missing

- Class/level not set → durations can't scale — type `/who` in game and
  they auto-fill ([Settings → Character](settings/character.md)).
- "Show only your own spells" hides other players' casts
  ([Settings → Spell Timers](settings/spell-timers.md)).
- A whole section missing (Boats / Custom Timer / Timers / rolls)? Its
  **Show …** toggle in Settings → Spell Timers may be off — or someone
  right-clicked and cleared it (cleared timers restart on their next
  trigger; hidden ones come back instantly when re-enabled).
- Ambiguous cast messages need **Guess ambiguous spells** on to show a
  best-guess row.

## Macro changes vanished / "Save to character" didn't work

Almost always the EQ client overwriting the file: it rewrites the whole
`<Name>_<Server>.ini` when you camp or log out, discarding anything edited
while it was running. Edit macros while that character is **logged out**.
nParse+ warns when it detects the client running, but that check uses
`pgrep` and so [does not fire on
Windows](https://github.com/prokopto-dev/nparse-plus/issues/33) yet.

If it already happened: open the [Macro Editor](windows/macro-editor.md),
Load that character, and check the **Local library** tab — macros nParse+
wrote that are no longer in the file are listed there, and **Restore from
local copy** puts them back. Turning on **Sync macros when EQ exits**
([Settings → Advanced](settings/advanced.md)) makes that copy stay current
by itself.

Failing that, `socials_backup/` beside the character ini holds the
pristine original from before nParse+'s first write.

## No characters listed in the Macro Editor

The **EQ install directory** in [Settings → General](settings/general.md)
must point at the folder containing `eqgame.exe` and `uifiles/` — the
editor says which of those is missing. Also check the **Server** dropdown
matches: P1999Red characters are stored with a `P1999PVP` filename suffix,
which nParse+ maps for you, but picking the wrong server shows an empty
list rather than an error.

## Plugins (add-ons)

Only relevant if you turned add-ons on in
[Settings → Advanced](settings/advanced.md#add-ons-plugins) — they're off by
default. Anything a plugin logs is tagged `nparseplus.plugins.<plugin-id>`
in `nparseplus.log`, so grepping that prefix separates an add-on's noise
from nParse+'s own.

### An add-on breaks startup

Launch once with add-ons forced off:

=== "macOS / Linux"

    ```bash
    NPARSEPLUS_NO_PLUGINS=1 /Applications/nParse+.app/Contents/MacOS/nparseplus
    ```

=== "Windows"

    ```powershell
    $env:NPARSEPLUS_NO_PLUGINS=1; .\nparseplus.exe
    ```

The variable is a one-way switch — it can force plugins off, never on — so
it's safe to leave set. nParse+ starts clean, and you can uninstall the
culprit from [Settings → Plugins](settings/plugins.md) (it will be listed;
plugin failures are isolated, so a crashing add-on shows as **Error**
rather than taking the app down). Its traceback is in `nparseplus.log`.

### Installed, but not loading

Check the **Status** column on [Settings → Plugins](settings/plugins.md):

- **Installed — restart to load** or **Ready** — restart nParse+. Installs
  and enable/disable changes only take effect at the next launch.
- **Awaiting consent** — the approval dialog runs at the next launch,
  before the plugin does anything. Answer **Enable plugin**.
- **Disabled** — tick its **Enabled** box, then restart.
- Not listed at all — it isn't in the plugins folder (**Open Plugins
  Folder** shows you where that is), or add-ons are off, or you're running
  with `NPARSEPLUS_NO_PLUGINS=1`.
- **Duplicate id** — two add-ons claim the same plugin id and only the
  first loaded. Uninstall one.

### Incompatible

The version handshake refused it: the add-on declares an SDK range or a
minimum nParse+ version this build doesn't satisfy. Nothing you can
configure — update nParse+ if it's asking for a newer one, otherwise ask
the author to rebuild against the current SDK. See
[Versioning](plugins/versioning.md).

### A plugin tick was disabled

Status reads **— tick disabled (too slow)**. Add-ons may register a
callback that runs on every log-driver poll, and that callback is timed:
the driver runs on the same thread as log parsing, so a slow tick stalls
everything. Two consecutive runs over the 250 ms budget and the tick is
dropped for the rest of the session (the timing is in `nparseplus.log`).
The plugin stays active — its parsers, event handlers, and windows are
unaffected. Nothing to fix on your side; it's a bug report for the add-on's
author.

## Settings seem lost / where is settings.json?

See [First run → Where settings
live](getting-started/first-run.md#where-settings-live). Note the
Flatpak keeps its own copy under `~/.var/app/…` — a tarball install and
a Flatpak install have separate settings.

Still stuck? Open a
[GitHub issue](https://github.com/prokopto-dev/nparse-plus/issues) with
your platform, the app version (tray menu, top entry), and the two log
files.
