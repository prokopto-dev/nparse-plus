# Roadmap

Where nParse+ is headed. No dates promised — this is a hobby project — but
these are the features actively planned, roughly in the order they're
likely to land. Want something moved up (or something new)?
[Open an issue](https://github.com/prokopto-dev/nparse-plus/issues).

Each item below is tracked as a
[GitHub issue](https://github.com/prokopto-dev/nparse-plus/issues) (labelled by
area / type / size) — the issues are the live status; this page is the prose
overview.

## Plugins & SDK

The [add-on system](plugins/index.md) shipped in 1.18 — opt-in, off by
default. What it's still missing:

- **A plugin template repository** — a `Use this template` GitHub repo
  (CI, release workflow, a working example plugin) so a new add-on starts
  from something that already builds. Content is written in
  `templates/plugin-repo/`.
- **A declarative manifest** so nParse+ can read an add-on's name, version,
  and compatibility *without importing it*. Reading that metadata means
  importing the module today, which is why installing (and dropping a file
  into the plugins folder) runs plugin code before you approve it — see
  [Plugin security & trust](plugins/security.md).

## Updating in one click

Today the [self-updater](features/updater.md) downloads the right artifact
for your platform, [verifies it against the checksum the release
publishes](features/updater.md#verified-downloads), and hands it to you to
install. The [remaining
half](https://github.com/prokopto-dev/nparse-plus/issues/72) is doing the
install for you:

- **[Swap and relaunch](https://github.com/prokopto-dev/nparse-plus/issues/76)**
  for the macOS app, the Windows folder and the Linux tarball — with the
  pre-flight checks that decide when it *can't* (an install directory it
  cannot write, not enough disk, a translocated bundle) and degrades to
  today's download-and-open with the reason
- **[One-click update inside the Flatpak](https://github.com/prokopto-dev/nparse-plus/issues/74)**
  through the portal's update monitor, instead of sending you to the host
  tools
- **[Rolling back](https://github.com/prokopto-dev/nparse-plus/issues/77)**
  an update that installs but won't start

## Distribution & platform

Longer-horizon packaging work, waiting on time (and in some cases, money):

- **macOS notarization** — removes the
  [`xattr` step](getting-started/install-macos.md#2-clear-the-quarantine-flag)
- **Windows code signing + installer** — removes the SmartScreen warning
- **Flathub submission** — `flatpak install flathub …` instead of
  sideloading (the [self-hosted repo](getting-started/install-flatpak.md)
  already gives `flatpak update`)
- **Delta updates for the standalone builds** — the Flatpak already
  updates incrementally; signed patch updates for the DMG/zip/tarball are
  planned
- **3D map view**

## Shipped

Recently off this list:

- **Add-ons load and unload without a restart** — installing, uninstalling,
  enabling and disabling an add-on all take effect immediately, windows,
  tray entries and settings pages included; see
  [Settings → Plugins](settings/plugins.md#what-applies-now-and-what-needs-a-restart).
  Two things still need a relaunch and both are deliberate: the master
  **Enable plugins** switch, and updating an add-on you already have.
- **A live plugin registry** — the curated index is served by the registry
  server at <https://nparseplugins.prokopto.dev/index.json>, so **Browse
  registry…** works and the app follows the catalogue wherever it lives.
  Publishing into it is an authenticated `POST` from a plugin's own release
  pipeline, and the digest it lists is one the server computed from the
  artifact itself; see [the registry](plugins/registry.md).
- **`nparseplus-sdk` on PyPI** — `pip install nparseplus-sdk` is all a
  plugin author needs; see [developing plugins](plugins/developing.md).

Everything else that used to be on this list and made it: see the
[changelog](changelog.md) and the
[releases page](https://github.com/prokopto-dev/nparse-plus/releases).
