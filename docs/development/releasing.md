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
