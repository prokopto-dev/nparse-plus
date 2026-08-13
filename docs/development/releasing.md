# Release flow

Releases are driven by [Conventional
Commits](https://www.conventionalcommits.org/) and
[Python Semantic Release](https://python-semantic-release.readthedocs.io/):

- `fix:` / `perf:` → patch release
- `feat:` → minor release
- `feat!:` / `BREAKING CHANGE:` → major release
- `chore:` / `ci:` / `docs:` etc. → no release

Every PR's commits are checked against these types by `pr-commit-check.yml`
(see [`tools/check_conventional_commits.py`](https://github.com/prokopto-dev/nparse-plus/blob/master/tools/check_conventional_commits.py)),
so a non-conventional commit can't reach `master`. Merge PRs with a **merge
commit** (not squash) so the individual conventional commits are preserved for
versioning.

## The pipeline

1. **Semantic Release workflow** — runs **automatically on every merge to
   `master`** (also available via manual dispatch, or `uv run semantic-release
   version` locally). It runs the ruff+pytest gate, computes the next version
   from the commit log, bumps `pyproject.toml` and `nparseplus.__version__`,
   updates `CHANGELOG.md`, commits, tags `v<X.Y.Z>`, and dispatches the package
   workflow (tags created with `GITHUB_TOKEN` don't trigger workflows on their
   own). A merge with only `chore`/`ci`/`docs` commits runs the gate and
   no-ops — no version bump, no release — but CI still builds it.
2. **Release workflow** (`release.yml`) verifies the tag matches both
   version files, then builds in parallel:
   - macOS DMG (ad-hoc signed)
   - Windows zip
   - Linux tarball **and** Flatpak bundle (GPG-signed; smoke-tested
     headless inside the sandbox)
   - publishes the Flatpak OSTree repo to the `gh-pages` branch —
     preserving the deployed docs — so `flatpak update` works
3. The **release job** collects the artifacts, extracts that version's
   changelog section, and publishes the GitHub release.
4. The **docs job** deploys this documentation as version `<X.Y>` with
   the `latest` alias (via [mike](https://github.com/jimporter/mike)),
   from the tagged tree.

Between releases, pushes to `master` that touch `docs/` redeploy the
**dev** docs version automatically (`docs-dev.yml`).

## What the app checks before it installs a download

The in-app updater streams the release artifact to a `.part` staging file
under a byte budget, re-asserting `https` on **every** redirect hop (a
release URL that 302s to `http` is refused, not downloaded in plaintext),
and pins the result to the `sha256:` digest GitHub publishes for the asset
(`assets[].digest`). A mismatch is refused before anything opens the file
and names both digests.

That digest arrives over the same TLS session as the release metadata that
describes it, so it is a **channel** guarantee: it proves the object the
CDN served is the object the API described — catching a corrupted,
truncated or substituted artifact — and proves nothing against anything
able to publish a release. A per-release signed `SHA256SUMS` (minisign,
public key compiled into the app) would be the actual signature; that is
still to do.

## Flatpak: adding a permission breaks in-app update once

Flatpak refuses an update whose new version requests a permission the
installed version lacks — the portal's `UpdateMonitor.Update` fails with
`org.freedesktop.DBus.Error.NotSupported` and the user has to update with
the host tools instead. Any addition to `finish-args` in
`packaging/flatpak/io.github.prokopto_dev.nparse_plus.yml` therefore breaks
in-app update across that one release hop, and belongs in the release
notes. A permission a feature needs must ship one release *before* the
feature.

## gh-pages layout

One branch serves both consumers:

```
gh-pages/
  repo/                      # Flatpak OSTree repo (URL must never move)
  nparseplus.flatpakref      # embed GPG key; flatpak install source
  nparseplus.flatpakrepo
  1.4/  dev/  latest/        # mike-managed docs versions
  versions.json  index.html  # mike: version list + redirect to latest
```

The Flatpak publish step rebuilds the branch as a **single orphan
commit** each release (so OSTree objects never pile up in git history)
but seeds it from the previous tree, so the docs directories survive.
mike then commits its docs updates on top. Don't hand-edit gh-pages.
