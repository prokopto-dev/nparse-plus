# Roadmap

Where nParse+ is headed. No dates promised — this is a hobby project — but
these are the features actively planned, roughly in the order they're
likely to land. Want something moved up (or something new)?
[Open an issue](https://github.com/prokopto-dev/nparse-plus/issues).

## Triggers

**Per-character trigger profiles** — don't care about the
[CH chain](features/ch-chains.md) on your warrior? Don't need FTE alerts
while leveling an alt? Triggers will auto-enable/disable based on which
character you're logged in as.

**Apply to character(s)** — default triggers stay global, but after
creating a trigger a button will apply it to all character profiles or
just a specific one.

**New default utility triggers** — a special header section in the timer
overlay for utility alerts such as rebuff requests and OOM indicators,
shipping as new [built-ins](features/builtin-triggers.md).

## Timers & overlays

**CH timer frequency indicator** — an addition to the CH lane: when the
raid leader calls "healers to 4 seconds", a muted indicator will show when
the next cast is expected to be declared. Off by default, toggleable.

**Post-expiration spell alerts** — let chosen spell timers persist for a
fixed period *after* expiry and flash, prompting you to request a rebuff
or recast on a target — click to dismiss. Toggleable.

**Raid-mode grouping, redesigned** — the EQTool-style "group buffs by
spell when targets outnumber spells" view is disabled for now: its global
flip desynced on targets recognized mid-fight, leaving stuck headers. If
it returns, it will track orientation per row and stay strictly opt-in —
targets remain the headers by default.

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
