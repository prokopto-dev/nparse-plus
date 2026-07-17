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
| Windows | `%LOCALAPPDATA%\nparseplus\Logs\` |
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

- Class/level not set → durations can't scale
  ([Settings → Character](settings/character.md)).
- "Show only your own spells" hides others' casts
  ([Settings → Spell Timers](settings/spell-timers.md)).
- Ambiguous cast messages need **Guess ambiguous spells** on to show a
  best-guess row.

## Settings seem lost / where is settings.json?

See [First run → Where settings
live](getting-started/first-run.md#where-settings-live). Note the
Flatpak keeps its own copy under `~/.var/app/…` — a tarball install and
a Flatpak install have separate settings.

Still stuck? Open a
[GitHub issue](https://github.com/prokopto-dev/nparse-plus/issues) with
your platform, the app version (tray menu, top entry), and the two log
files.
