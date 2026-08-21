# Updating

nParse+ checks GitHub for new releases at startup (disable in
[Settings → General](../settings/general.md)). When one is available, the
tray menu shows **Install update vX.Y.Z**, and a dialog lists the release
notes for every version between yours and the newest.

What "install" means depends on your platform:

| Install type | What you get | What you do |
|---|---|---|
| macOS DMG | The new `.dmg` for your Mac's architecture (Apple Silicon or Intel) downloads and opens | Drag to Applications, re-run the [`xattr` command](install-macos.md#2-clear-the-quarantine-flag) |
| Windows zip | The new `.zip` downloads | Extract over (or beside) the old folder, run `nparseplus.exe` |
| Linux tarball | The new `.tar.gz` downloads | Unpack over the old directory |
| Linux Flatpak | The update installs **in place** through Flatpak, then offers to restart | Press **Install Update**, then **Restart Now** |

Your settings always survive updates — they live in a
[separate config directory](first-run.md#where-settings-live), not in the
app folder.

## Flatpak: one click, no download

Inside a Flatpak the update dialog's button says **Install Update**, and it
does exactly that — nParse+ asks Flatpak to update it in place from the
GPG-signed OSTree repository every release publishes, so only the parts that
actually changed download. When it finishes, **Restart Now** brings the app
back on the new version.

Two things it may say instead:

- **"Nothing to install yet"** — the update repository lags the GitHub
  release by a few minutes on release day. Try again shortly.
- **"This update needs the Flatpak tools"** — the new version asks for a
  sandbox permission the installed one does not have, and Flatpak only allows
  an in-app update when the permissions are the same or fewer. Run
  `flatpak update io.github.prokopto_dev.nparse_plus` (or use your software
  manager) instead. Release notes call this out when it applies.

If the in-app route is not available at all — an old Flatpak, or a desktop
without the portal — nParse+ falls back to downloading the `.flatpak` bundle
for your software installer, and

```bash
flatpak update
```

always works from a terminal. Details (including wiring up installs from
pre-1.4.1 bundles) in the
[Flatpak guide](install-flatpak.md#5-updating).

## Versioned documentation

This documentation site is versioned too — use the version selector in the
header to match the docs to the release you're running. **latest** always
tracks the newest release; **dev** tracks unreleased work on master.
