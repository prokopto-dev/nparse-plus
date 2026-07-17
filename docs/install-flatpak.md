# Installing nParse+ as a Flatpak (Linux)

Each release ships a `nparseplus-<version>-linux-x86_64.flatpak` bundle on the
[releases page](https://github.com/prokopto-dev/nparse-plus/releases). It runs
on any distro with Flatpak — Fedora, Ubuntu, Arch, SteamOS (desktop mode),
etc. — and is the recommended install if you don't want to manage the tarball
by hand.

nParse+ is not (yet) on Flathub, so this is a "sideloaded" bundle: you install
the file directly, and updates come from the releases page rather than
`flatpak update` (details [below](#updating)).

## 1. Prerequisites

You need `flatpak` itself and the Flathub remote. The remote is required even
though nParse+ isn't on Flathub — the bundle depends on the shared
`org.freedesktop.Platform` runtime, and Flatpak downloads that from Flathub.

- **Fedora / SteamOS**: both are preinstalled and enabled — skip ahead.
- **Ubuntu / Debian**:

  ```bash
  sudo apt install flatpak
  flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
  ```

- **Other distros**: see [flatpak.org/setup](https://flatpak.org/setup/) for
  your distro, then add the Flathub remote as above.

If you skip the remote, the install below fails with
`error: The application ... requires the runtime org.freedesktop.Platform ...
which was not found` — adding the remote and retrying fixes it.

## 2. Install

Download the `.flatpak` file from the
[latest release](https://github.com/prokopto-dev/nparse-plus/releases/latest),
then either double-click it in GNOME Software / KDE Discover, or:

```bash
flatpak install --user ./nparseplus-<version>-linux-x86_64.flatpak
```

Flatpak resolves and downloads the runtime automatically on first install
(a few hundred MB, shared with every other Flatpak on your system).

## 3. Run

Launch **nParse+** from your app menu, or:

```bash
flatpak run io.github.prokopto_dev.nparse_plus
```

Then point it at your EQ Logs folder in Settings, same as any other platform.

### Tray icon on GNOME

Stock GNOME has no system-tray support. Install the
[AppIndicator extension](https://extensions.gnome.org/extension/615/appindicator-support/)
to get the nParse+ tray menu. KDE, Cinnamon, XFCE, etc. work out of the box.

## 4. EQ installs outside your home directory

The sandbox can read and write files under `$HOME` only. That covers the
usual WINE prefixes (`~/.wine`, `~/Games`, Lutris and Bottles defaults). If
your EverQuest install lives elsewhere — a Steam library on another drive,
`/opt`, an NTFS mount — grant access before pointing nParse+ at it:

```bash
flatpak override --user --filesystem=/path/to/EverQuest io.github.prokopto_dev.nparse_plus
```

([Flatseal](https://flathub.org/apps/com.github.tchx84.Flatseal) does the same
thing graphically.) One grant covers everything nParse+ touches there: the
`Logs/` folder, `spells_us.txt`, and the Night Vision fix (which writes into
the EQ install, with backups).

## 5. Updating

`flatpak update` works: every release also publishes an OSTree repository at
`https://prokopto-dev.github.io/nparse-plus/repo`.

- **Bundles downloaded from v1.4.1 onward embed that repo URL** — installing
  the `.flatpak` once configures its origin remote automatically, and from
  then on plain `flatpak update` picks up new nParse+ releases alongside
  everything else. Updates are incremental (only changed files download).
- **Installed from an older bundle?** Wire it up once by hand — the repo is
  GPG-signed and the `.flatpakrepo` file carries the public key, so no extra
  flags are needed:

  ```bash
  flatpak remote-add --user nparseplus \
    https://prokopto-dev.github.io/nparse-plus/nparseplus.flatpakrepo
  flatpak update
  ```

  (If you previously added the remote with `--no-gpg-verify`, run
  `flatpak remote-delete --user nparseplus` first and re-add it as above to
  get signature verification.)
- The in-app update check still notifies you from the tray either way, and
  downloading a newer `.flatpak` over the top keeps working too.

Your settings and data live in
`~/.var/app/io.github.prokopto_dev.nparse_plus/` and survive upgrades,
reinstalls, and switches between bundle and repo installs (only
`flatpak uninstall --delete-data` removes them). Per-app overrides
(`flatpak override`) persist independently as well.

## 6. Uninstall

```bash
flatpak uninstall io.github.prokopto_dev.nparse_plus
```

Add `--delete-data` to also remove the settings under `~/.var/app/`.

## Fedora Atomic (Kinoite / Silverblue / Bazzite)

The Flatpak is the intended install path on image-based Fedora variants — no
package layering needed, and everything above works with less setup:

- **No prerequisites**: `flatpak` and the Flathub remote ship enabled out of
  the box. Download the bundle and install it (KDE Discover on Kinoite
  handles a double-clicked `.flatpak` file, or use the CLI from step 2).
- **Tray icon works natively** on Plasma — no extension needed (the GNOME
  AppIndicator note only applies to Silverblue).
- **Wayland-only session is fine**: Fedora's Plasma 6 has no X11 session
  anymore, but XWayland is installed and used by default. nParse+ runs its
  windows through XWayland deliberately — that's what keeps the overlays
  always-on-top, draggable, and position-persistent — and EQ under WINE is
  an XWayland client too, so they share one window stack.
- **WINE prefixes need no extra permissions** in the common setups: Bottles
  and Lutris flatpaks keep prefixes under `~/.var/app/…`, Steam's default
  library is under `~/`, and all of that is inside your home directory,
  which nParse+ can already see. Only a game library on a separate drive
  (e.g. `/run/media/...`) needs the `flatpak override` from section 4.

## Troubleshooting

- **Overlays vanish behind the game / can't be dragged** — make sure EQ runs
  in windowed or borderless mode, not exclusive fullscreen (true on every
  platform, not just Flatpak). The Flatpak already forces X11/XWayland mode
  internally, which is what makes always-on-top overlays work on Wayland
  desktops; don't override `QT_QPA_PLATFORM` to `wayland` — overlays cannot
  stay on top or remember positions as native Wayland windows.
- **"runtime ... not found" during install** — the Flathub remote is missing;
  see [Prerequisites](#1-prerequisites).
- **No tray icon** — on GNOME, install the AppIndicator extension
  ([above](#tray-icon-on-gnome)).
- **Log file picker can't see your EQ folder** — it's outside `$HOME`; grant
  access with `flatpak override`
  ([above](#4-eq-installs-outside-your-home-directory)).
- **Reporting a crash or connection problem** — since v1.4.1 the app writes
  two log files; attach them to bug reports:

  ```
  ~/.var/app/io.github.prokopto_dev.nparse_plus/.local/state/nparseplus/log/crash.log
  ~/.var/app/io.github.prokopto_dev.nparse_plus/.local/state/nparseplus/log/nparseplus.log
  ```

  (`crash.log` holds unhandled-error tracebacks; `nparseplus.log` holds
  warnings such as sharing reconnect reasons. For the non-Flatpak tarball
  the same files live under `~/.local/state/nparseplus/log/`.)

## Running the plain (non-Flatpak) Linux build

The release tarball (PyInstaller onedir) applies the same Linux defaults the
Flatpak launcher does, but only when you haven't set the variables yourself:

- `QT_QPA_PLATFORM=xcb` — overlays run through X11/XWayland so always-on-top
  and window positioning work (see the troubleshooting note above).
- `QTWEBENGINE_DISABLE_SANDBOX=1` — set for frozen builds and on kernels
  that restrict unprivileged user namespaces (Ubuntu 24.04's AppArmor
  default), where Chromium's sandbox cannot start and the Discord overlay's
  render processes crash instead. Trade-off: the StreamKit web content runs
  without Chromium's renderer sandbox — same as the Flatpak, where the
  sandbox can't nest either. Export `QTWEBENGINE_DISABLE_SANDBOX=0` before
  launching if you'd rather keep the sandbox and lose the Discord overlay
  on such systems. Each applied default is printed to stderr at startup.
