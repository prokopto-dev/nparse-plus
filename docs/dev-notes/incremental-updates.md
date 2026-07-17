# Incremental updates from 1.4 onward

> **Status (2026-07-17):** the Flatpak half is live — since v1.4.1 every
> release publishes the OSTree repo to GitHub Pages (`gh-pages` branch) and
> bundles are built with `--repo-url`, so `flatpak update` performs native
> incremental updates. The repo and bundles are GPG-signed (public key at
> `packaging/flatpak/nparseplus-repo.gpg`, private key in the
> `FLATPAK_GPG_PRIVATE_KEY` repo secret). tufup for the standalone
> DMG/zip/tarball builds is still unimplemented; those remain full downloads
> offered by the in-app release check.

## Decision

Use [tufup](https://github.com/dennisvang/tufup) for the standalone macOS,
Windows, and Linux PyInstaller bundles, and use Flatpak's native OSTree update
mechanism once nParse+ is published through a Flatpak repository or Flathub.

Do not implement an unsigned custom file-replacement protocol. An updater is a
remote-code execution mechanism by design; hashes stored beside an update are
not sufficient if an attacker can replace both. tufup uses The Update Framework
(TUF) for signed metadata, rollback/freeze protection, archive downloads, and
binary patch chains.

## What users will experience

- **1.3.x -> 1.4.0:** one final full DMG/zip/tarball installation. A 1.3 client
  cannot safely acquire updater code and an embedded TUF trust root any other
  way.
- **1.4.0 -> 1.4.1 and later:** the app checks signed TUF metadata and chooses
  one or more binary patches when their total is smaller than the full archive.
- **Skipped releases:** tufup can apply a chain of consecutive patches. If that
  chain is missing, invalid, or larger than the current archive, it falls back
  to the full archive.
- **Flatpak:** the standalone `.flatpak` bundle remains a full download, but
  installing it configures the published repo as its origin remote
  (`build-bundle --repo-url`), after which `flatpak update` pulls
  incrementally — OSTree owns content deduplication and update transactions.
  Live since v1.4.1, GPG-signed (commits, summary, and bundles; the embedded
  public key makes the auto-configured remote a verifying one).

The existing GitHub release check can remain the user-facing announcement and
fallback. It queries published releases, semantically sorts them, and shows
every release body newer than the installed version in the update-details
window. The actual in-place update must use TUF metadata and targets, not the
mutable GitHub release response as its trust root.

## Why archive patches fit this repository

nParse+ already ships PyInstaller **onedir** bundles. PyInstaller documents that
an onedir application can sometimes update only its executable when imports and
dependencies are unchanged. In practice, nParse+ also changes Qt/Python
dependencies and bundled data, so the updater must account for additions,
changes, and removals across the entire directory.

tufup creates a versioned archive for each complete application directory and a
binary patch between consecutive archives. The client reconstructs and verifies
the complete target archive before an install helper replaces the application
after the running process exits. This avoids maintaining a fragile, bespoke
per-file manifest format while still transferring only the compressed binary
difference in the common case.

## Release architecture

```text
platform release job
  -> build PyInstaller onedir
  -> sign the completed app/binaries
  -> create nparseplus-<platform>-<version>.tar.gz
  -> create patch from the preceding platform archive
  -> add archive + patch hashes to signed TUF targets metadata
  -> publish targets and metadata

1.4+ client
  -> refresh embedded TUF root metadata
  -> select patch chain or full archive
  -> download and cryptographically verify targets
  -> reconstruct and verify the new archive in staging
  -> launch an external install helper
  -> quit nParse+
  -> atomically replace the app directory and relaunch
```

Patches must be generated **after** macOS code signing. Reconstructing the exact
signed target bundle preserves its code signature; modifying an already-signed
installed bundle without also delivering the target signature metadata would
invalidate it.

## Required 1.4.0 bootstrap work

1. Add `tufup` as a runtime dependency and include its trusted `root.json` in
   the PyInstaller bundle.
2. Choose a stable HTTPS update-repository URL. GitHub Pages is suitable for
   static TUF `metadata/` and `targets/`; GitHub Releases can remain the manual
   full-package fallback.
3. Generate separate target streams for `macos-arm64`, `windows-x86_64`, and
   `linux-x86_64`. A client must never consume a target from another platform.
4. Integrate `Client.check_for_updates()` into the existing background update
   check and expose download/install progress through queued Qt signals.
5. Stage outside the installed directory, verify before quitting, and run the
   platform-specific installer only after the main PID exits. Keep the old
   directory until the replacement succeeds so rename failure can roll back.
6. Retain the current full DMG/zip/tarball action whenever the app is not frozen,
   the install location is not writable, metadata refresh fails, or no valid
   patch path exists.
7. Test archive/patch creation and application on every release runner. The
   smoke test must start the reconstructed target, not merely inspect hashes.

Release versions and notes use standard Conventional Commits through Python
Semantic Release. `feat:` increments the minor version, `fix:` and `perf:`
increment the patch version, and `!` or `BREAKING CHANGE:` increments the major
version. The Semantic Release workflow updates both version declarations and
`CHANGELOG.md`, then dispatches the platform package workflow for its new tag.

## Signing-key policy

This setup needs a deliberate key ceremony before client code is enabled:

- Keep the TUF **root** private key offline. Only its public root metadata is
  embedded in 1.4.0.
- Store narrowly scoped targets/snapshot/timestamp signing keys in protected CI
  environments, or sign release metadata on a dedicated release machine.
- Use expirations and key thresholds appropriate for recovery. Document key
  rotation and loss recovery before the first 1.4 build.
- Never commit private signing keys or print them in CI logs.

Until those keys and the repository URL exist, 1.4 updater code should fail
closed and keep the existing full-download flow.

## Acceptance criteria

- A clean 1.4.0 installation can update to 1.4.1 on macOS and Windows without
  downloading the full platform installer.
- A skipped-version test applies at least two patches in sequence.
- The update window shows each changelog section between the locally installed
  version and the target version, in descending semantic-version order.
- Corrupt payloads, expired metadata, rollback metadata, wrong-platform
  targets, unwritable installs, and interrupted swaps all fail safely.
- macOS `codesign --verify --deep --strict` passes after patch reconstruction.
- The updater never touches `settings.json`, EQ logs, or any directory outside
  the resolved application bundle and its explicitly-created staging/backup
  siblings.
- Full installers remain available as recovery artifacts for every release.
