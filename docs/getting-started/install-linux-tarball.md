# Install on Linux (tarball)

The [Flatpak](install-flatpak.md) is the recommended Linux install — it gets
incremental `flatpak update` support and a sandbox. The tarball
(`nparseplus-<version>-linux-x86_64.tar.gz`) is for systems without Flatpak
or users who prefer a plain unpacked build.

## Install and run

```bash
tar -xzf nparseplus-<version>-linux-x86_64.tar.gz
./nparseplus/nparseplus
```

Download from the
[latest release](https://github.com/prokopto-dev/nparse-plus/releases/latest).
The tarball is a self-contained PyInstaller build — no Python or
distribution packages required.

nParse+ lives in the system tray. On stock GNOME you need the
[AppIndicator extension](https://extensions.gnome.org/extension/615/appindicator-support/)
to see tray icons; KDE, Cinnamon, XFCE, etc. work out of the box.

## Wayland and environment defaults

The launcher applies two Linux defaults, each only when you haven't set the
variable yourself (applied defaults are printed to stderr at startup):

- `QT_QPA_PLATFORM=xcb` — overlays run through X11/XWayland so always-on-top
  and window positioning work. Don't force `wayland`: native Wayland windows
  cannot stay on top of the game or remember positions.
- `QTWEBENGINE_DISABLE_SANDBOX=1` — works around kernels/distros that
  restrict unprivileged user namespaces (e.g. Ubuntu 24.04's AppArmor
  default), where Chromium's sandbox cannot start and the
  [Discord overlay](../windows/discord-overlay.md) would crash. Export
  `QTWEBENGINE_DISABLE_SANDBOX=0` before launching if you'd rather keep the
  sandbox and lose the Discord overlay on such systems.

## Point it at your logs

Continue with [First run](first-run.md). WINE-prefix EQ installs work fine —
the log folder is just a directory under your prefix.

## Updating

nParse+ checks GitHub for new releases at startup; on a tarball install it
offers the new `.tar.gz` (a Flatpak install is offered the `.flatpak`
instead). Unpack the new version over the old one — settings live separately
under `~/.config/nparseplus/`. See [Updating](updating.md).

## Uninstall

Delete the `nparseplus/` directory. Settings are in `~/.config/nparseplus/`
and logs in `~/.local/state/nparseplus/log/`.
