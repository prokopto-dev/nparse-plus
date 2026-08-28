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

## Release channels

nParse+ publishes on two channels, and you are on **Stable** unless you
change it in [Settings → General](../settings/general.md).

| Channel | What you are offered | Who it is for |
|---|---|---|
| **Stable** (default) | Released versions only | Everyone |
| **Beta** | Also the test releases cut from each merge | People who want fixes early and can live with breakage |

Every merge to `master` now publishes a **beta** — something like
`v2.30.0-beta.3` — instead of a release for everyone. A stable version ships
when one of those beta lines is deliberately promoted, which turns
`v2.30.0-beta.3` into `v2.30.0`. If you are on Stable, nothing about this is
visible to you: nParse+ has always ignored prereleases, so betas simply do not
appear, and you are offered a version only once it is promoted.

### Switching to beta

Set **Update channel** to *Beta* in Settings → General. nParse+ re-checks
immediately, so the badge beside it tells you straight away whether a beta is
waiting. Betas install exactly like stable releases.

A beta client is still offered stable releases. Because `2.30.0` counts as
newer than `2.30.0-beta.3`, you roll onto the stable version when the line you
are testing is promoted — you do not get stuck on the beta.

!!! warning "Betas are not published for Flatpak"

    Betas ship as **DMG, zip and tarball only**. The Flatpak repository that
    `flatpak update` follows carries stable releases exclusively, so a Flatpak
    install has nothing to download on the beta channel. Leave it on Stable.

### Getting back to stable

Set **Update channel** back to *Stable*. What happens next depends on where
your beta sits:

- **The usual case** — your beta line gets promoted, and the stable release is
  offered to you as a normal update. Nothing else to do.
- **The line was abandoned** — development moved on without promoting it. Your
  installed version is then *newer* than the newest stable, so the update
  check honestly reports that there is nothing to install, and it will keep
  doing so until a stable release passes it. If you would rather go back
  immediately, download the version you want from the
  [releases page](https://github.com/prokopto-dev/nparse-plus/releases) and
  install it over the top. Your settings are untouched either way.

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
