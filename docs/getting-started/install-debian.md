# Install on Linux (Debian package)

Debian 12 (bookworm) and derivatives get their own build:
`nparseplus_<version>_amd64.deb` on the
[latest release](https://github.com/prokopto-dev/nparse-plus/releases/latest).

## Which Linux download do I want?

| Download | Needs | Use it when |
| --- | --- | --- |
| `.flatpak` | Flatpak on the host | Recommended. The sandbox brings its own glibc, so it runs anywhere |
| `.deb` | glibc 2.36+ (Debian 12+) | You want a normal system package, or the tarball won't start |
| `.tar.gz` | glibc 2.39+ (Ubuntu 24.04, Fedora 40, Debian 13…) | Any other distribution, no Flatpak |

nParse+ bundles its own Python and Qt, so none of these needs a distribution
Python. What they cannot bundle is **glibc**, which is why there are two
native builds: a program built against a newer glibc cannot run on an older
one. If the tarball fails with

```
/lib/x86_64-linux-gnu/libc.so.6: version `GLIBC_2.38' not found
```

then your glibc is older than the tarball's floor — `ldd --version` will tell
you which you have — and this package is the fix.

## Install

```bash
sudo apt install ./nparseplus_<version>_amd64.deb
```

Use `apt`, not `dpkg -i`: the package declares its Qt and X dependencies and
apt resolves them from the archive. `dpkg -i` will install it and leave those
unmet, which surfaces later as a failure to start.

Then launch it from your desktop menu, or run `nparseplus`.

Trigger audio and text-to-speech work out of the box: `espeak-ng` is a hard
dependency, so apt installs it with the package. (Without an espeak binary on
PATH the app falls back to a silent speaker and says nothing about it, which
is why it is a `Depends` and not a `Recommends`.)

### Without root

The package is a plain archive, so you can unpack it anywhere:

```bash
dpkg-deb -x nparseplus_<version>_amd64.deb ~/nparseplus
~/nparseplus/opt/nparseplus/nparseplus
```

You then own the dependencies yourself — the list is in
`dpkg-deb -I nparseplus_<version>_amd64.deb`, and that includes `espeak-ng`,
without which trigger audio is silently disabled.

## What it installs

| Path | |
| --- | --- |
| `/opt/nparseplus/` | the application (it bundles Python and Qt, so it stays out of `/usr`) |
| `/usr/bin/nparseplus` | the launcher |
| `/usr/share/applications/` | the desktop entry |
| `/usr/share/icons/hicolor/` | icons, 16px through 256px plus the SVG |

Settings and logs are per-user and live outside all of it — see
[Uninstall](#uninstall).

## Running it

nParse+ lives in the system tray. On stock GNOME you need the
[AppIndicator extension](https://extensions.gnome.org/extension/615/appindicator-support/)
to see tray icons; KDE, Cinnamon and XFCE work out of the box.

The launcher applies the same two Linux environment defaults the tarball does
(`QT_QPA_PLATFORM=xcb` and `QTWEBENGINE_DISABLE_SANDBOX=1`, each only when you
have not set it yourself) — see
[Wayland and environment defaults](install-linux-tarball.md#wayland-and-environment-defaults)
for what they do and how to override them.

Continue with [First run](first-run.md) to point it at your logs. WINE-prefix
EQ installs are fine; the log folder is just a directory under your prefix.

!!! note "If you also have the Flatpak installed"

    Both carry the same desktop entry ID, because they are the same
    application. Your desktop will show one menu entry, and which of the two
    it launches is not defined. Keep one, or launch this build with
    `nparseplus` from a terminal.

## Updating

Download the new `.deb` and `sudo apt install ./nparseplus_<version>_amd64.deb`
again. Settings are untouched.

!!! warning "The in-app update check does not offer this package yet"

    nParse+ checks GitHub for new releases at startup, but on a `.deb`
    install it currently offers the generic `.tar.gz` — the build that does
    not run on Debian 12. Ignore that offer and download the `.deb` from the
    [releases page](https://github.com/prokopto-dev/nparse-plus/releases/latest)
    instead. Tracked in
    [#163](https://github.com/prokopto-dev/nparse-plus/issues/163).

## Uninstall

```bash
sudo apt remove nparseplus
```

Settings stay in `~/.config/nparseplus/` and logs in
`~/.local/state/nparseplus/log/`; delete those by hand if you want them gone.
