# Roadmap

Where nParse+ is headed. No dates promised — this is a hobby project — but
these are the features actively planned, roughly in the order they're
likely to land. Want something moved up (or something new)?
[Open an issue](https://github.com/prokopto-dev/nparse-plus/issues).

Each item below is tracked as a
[GitHub issue](https://github.com/prokopto-dev/nparse-plus/issues) (labelled by
area / type / size) — the issues are the live status; this page is the prose
overview.

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

Everything that used to be on this list and made it: see the
[changelog](changelog.md) and the
[releases page](https://github.com/prokopto-dev/nparse-plus/releases).
