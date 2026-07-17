# Updating

nParse+ checks GitHub for new releases at startup (disable in
[Settings → General](../settings/general.md)). When one is available, the
tray menu shows **Install update vX.Y.Z**, and a dialog lists the release
notes for every version between yours and the newest.

What "install" means depends on your platform:

| Install type | What you get | What you do |
|---|---|---|
| macOS DMG | The new `.dmg` downloads and opens | Drag to Applications, re-run the [`xattr` command](install-macos.md#2-clear-the-quarantine-flag) |
| Windows zip | The new `.zip` downloads | Extract over (or beside) the old folder, run `nparseplus.exe` |
| Linux tarball | The new `.tar.gz` downloads | Unpack over the old directory |
| Linux Flatpak | The new `.flatpak` is handed to your software installer — **but see below**, `flatpak update` is better | Confirm the install prompt |

Your settings always survive updates — they live in a
[separate config directory](first-run.md#where-settings-live), not in the
app folder.

## Flatpak: just use `flatpak update`

Flatpak installs have a better path than downloading bundles: every release
publishes a GPG-signed OSTree repository, and bundles from v1.4.1 onward
configure it as their update origin automatically. That means:

```bash
flatpak update
```

picks up new nParse+ releases alongside everything else on your system, and
downloads only what changed. Details (including wiring up installs from
pre-1.4.1 bundles) in the
[Flatpak guide](install-flatpak.md#5-updating).

## Versioned documentation

This documentation site is versioned too — use the version selector in the
header to match the docs to the release you're running. **latest** always
tracks the newest release; **dev** tracks unreleased work on master.
