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

### Antivirus flagged the download

Quarantined, or gone from your Downloads folder without warning? It is a
**false positive**. Some engines — AVG and Avast most often — flag
nParse+ with a generic name like `Win64:Evo-gen` or `Trojan:Script/Wacatac`.
A generic detection name is the tell: it means "this resembles a pattern",
not "this file is known malware".

**Why it happens.** nParse+ is a Python app packaged with
[PyInstaller](https://pyinstaller.org). Every PyInstaller build starts from
the same small C launcher — the *bootloader* — which unpacks the bundle and
starts Python. Malware authors use PyInstaller too, so engines learned to
match on those launcher bytes, and everything built with the tool inherits
the verdict. Unpacking-an-embedded-payload is also the behaviour a heuristic
scanner is built to distrust, whoever does it.

**What nParse+ does about it.** The Windows build compiles its own bootloader
in our release CI rather than shipping the prebuilt one every PyInstaller
user ships, is packaged as a plain folder (not a self-extracting single
file), uses no executable compressor, and carries a real version resource you
can read in Explorer → Properties → Details. What it does **not** have is a
code-signing certificate — that is
[issue #19](https://github.com/prokopto-dev/nparse-plus/issues/19), and it is
the one thing that would settle this for good. Until it lands, expect the
occasional flag.

**Verify what you downloaded.** GitHub publishes a sha256 for every release
asset, so you can check the zip is exactly the file we uploaded:

```powershell
Get-FileHash .\nparseplus-<version>-win64.zip -Algorithm SHA256
```

Compare it against the `digest` GitHub reports for that asset — open
`https://api.github.com/repos/prokopto-dev/nparse-plus/releases/latest` in a
browser and look for your file under `assets`, or run:

```powershell
gh api repos/prokopto-dev/nparse-plus/releases/latest --jq '.assets[] | "\(.digest)  \(.name)"'
```

A match proves you have the file GitHub served, unaltered in transit. It is
not a signature and does not prove anything about who built it — the same
[caveat that applies to update
downloads](features/updater.md#verified-downloads).

**Get it back and keep it.** Restore the file from your antivirus'
quarantine, then add an exclusion for the extracted folder (in AVG or Avast:
**Menu → Settings → Exceptions**; in Windows Security: **Virus & threat
protection → Manage settings → Exclusions**). Do that only once the checksum
above matches.

**Report it — this is the part that actually helps.** Vendors fix false
positives from user submissions, and one report benefits everyone:

| Vendor | Where |
|---|---|
| AVG / Avast (one engine, one report covers both) | [Avast false-positive form](https://www.avast.com/false-positive-file-form.php) |
| Microsoft Defender | [Microsoft security intelligence submission](https://www.microsoft.com/en-us/wdsi/filesubmission) |
| Anything else | Check the vendor's site for a "false positive" or "sample submission" form |

Please also open a [GitHub
issue](https://github.com/prokopto-dev/nparse-plus/issues) with the engine
name, the detection name and the nParse+ version — and, if you can, a
[VirusTotal](https://www.virustotal.com/) link for the zip, which shows how
many engines agree.

If you would rather not wait, the [source is
public](https://github.com/prokopto-dev/nparse-plus) and
[building it yourself](development/building.md) is documented.

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
   restart after turning it **on** or switching networks (turning it off
   applies immediately, no restart).
2. This character's location sharing on?
   ([Settings → Character](settings/character.md))
3. You only receive a zone's players after **sending** a location — type
   `/loc` in game.
4. Check `nparseplus.log` for reconnect reasons (paths above); the tray
   menu's top line shows live sharing status.

## Timers are wrong or missing

- Class/level not set → durations can't scale — type `/who` in game and
  they auto-fill ([Settings → Character](settings/character.md)).
- "Show only your own spells" hides other players' casts
  ([Settings → Timers](settings/timers.md)).
- A whole section missing (Boats / Custom Timer / Timers / rolls)? Its
  **Show …** toggle in Settings → Timers may be off — or someone
  right-clicked and cleared it (cleared timers restart on their next
  trigger; hidden ones come back instantly when re-enabled).
- Ambiguous cast messages need **Guess ambiguous spells** on to show a
  best-guess row.

## Macro changes vanished / "Save to character" didn't work

Almost always the EQ client overwriting the file: it rewrites the whole
`<Name>_<Server>.ini` when you camp or log out, discarding anything edited
while it was running. Edit macros while that character is **logged out**.
nParse+ warns when it detects the client running — on every platform, and
including a client running under wine or CrossOver. The check is best
effort, though: if it cannot tell, it stays quiet rather than nagging, so
treat a missing warning as "no news" rather than "the game is definitely
closed".

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

- **Installed — restart to load** — the install landed somewhere the
  plugins-folder sweep doesn't pick up, so nParse+ couldn't adopt it
  in place. Restart once. (An ordinary install starts running immediately.)
- **Ready** — approved and enabled, and due to activate; normally you only
  catch this mid-startup. If it stays that way, `nparseplus.log` says why.
- **Awaiting consent** — the approval dialog runs before the plugin does
  anything: right after an install, or at the next launch for a file you
  dropped into the plugins folder yourself. Answer **Enable plugin**.
- **Disabled** — tick its **Enabled** box. It starts there and then; no
  restart.
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

## An update download was refused

**Update download refused** is not a network error. It means the bytes that
arrived are not the ones the release describes — nParse+ checks every
download against the sha256 checksum GitHub publishes for that asset (or, on
older releases that carry none, against its published length) and deletes
anything that doesn't match, so nothing was installed. Almost always a
corrupted or interrupted transfer: try again.

The release page is deliberately *not* opened for you here, because it
serves the same artifact that was just refused. **Update download failed**
is the other one — that's a real transport problem, and it does offer the
release page.

**Show Details** on the refusal carries both checksums, the published one
and the one that arrived; paste those into a bug report. Full explanation of
what the check does and does not prove:
[Self-updater → Verified downloads](features/updater.md#verified-downloads).

## Settings seem lost / where is settings.json?

See [First run → Where settings
live](getting-started/first-run.md#where-settings-live). Note the
Flatpak keeps its own copy under `~/.var/app/…` — a tarball install and
a Flatpak install have separate settings.

Still stuck? Open a
[GitHub issue](https://github.com/prokopto-dev/nparse-plus/issues) with
your platform, the app version (tray menu, top entry), and the two log
files.
