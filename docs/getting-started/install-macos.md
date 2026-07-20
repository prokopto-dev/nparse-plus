# Install on macOS

nParse+ ships a DMG for each Mac architecture:

- Apple Silicon (M1/M2/M3…): `nParse+-<version>-macos-arm64.dmg`
- Intel: `nParse+-<version>-macos-x86_64.dmg`

Not sure which you have?  → **About This Mac**: "Apple M…" is Apple Silicon,
"Intel Core…" is Intel.

## 1. Download and install

1. Download the DMG for your architecture from the
   [latest release](https://github.com/prokopto-dev/nparse-plus/releases/latest).
2. Open it and drag **nParse+** to **Applications**.

## 2. Clear the quarantine flag

The app is ad-hoc signed, not notarized, so macOS quarantines it on first
download ("nParse+ is damaged and can't be opened" or "cannot be opened
because the developer cannot be verified"). Clear it once from Terminal:

```bash
xattr -dr com.apple.quarantine "/Applications/nParse+.app"
```

Then launch **nParse+** normally. It lives in the menu bar (system tray) —
there's no Dock window; look for the icon at the top right.

!!! note "Why not notarized?"
    Notarization requires a paid Apple Developer account; it's on the
    project's roadmap. The command above is the standard workaround for
    open-source apps distributed outside the App Store — you can inspect
    exactly what you're running, since [the source is
    public](https://github.com/prokopto-dev/nparse-plus).

## 3. Point it at your logs

If you play P99 through a WINE/CrossOver/Whisky wrapper, your EQ `Logs`
folder lives inside the wrapper's drive. Continue with
[First run](first-run.md) to find and select it.

## Updating

nParse+ checks GitHub for new releases at startup and offers the DMG download
from the tray menu. See [Updating](updating.md). You'll need to repeat the
`xattr` command after installing a new version.

## Uninstall

Drag `/Applications/nParse+.app` to the Trash. Settings live separately in
`~/Library/Application Support/nparseplus/` and logs in
`~/Library/Logs/nparseplus/` — delete those too if you want a clean slate.
