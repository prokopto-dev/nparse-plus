# Contributing

Thanks for helping improve nParse+.

## Commit messages — Conventional Commits (required)

Releases are automated by [Python Semantic
Release](https://python-semantic-release.readthedocs.io/), so every commit that
lands on `master` must be a valid [Conventional
Commit](https://www.conventionalcommits.org/). A CI check
(`.github/workflows/pr-commit-check.yml`) validates each commit in a PR and
blocks the merge if any subject doesn't conform.

Format: `type(scope): summary` — scope optional; add a `!` before the colon (or a
`BREAKING CHANGE:` footer) for a breaking change.

| Type | Effect on the version |
|---|---|
| `feat` | minor release |
| `fix`, `perf` | patch release |
| `feat!` / `BREAKING CHANGE:` | major release |
| `build`, `chore`, `ci`, `docs`, `style`, `refactor`, `test` | no release (still built + tested) |

The exact allowed types come from `[tool.semantic_release.commit_parser_options]`
in `pyproject.toml` — the check reads them from there, so the two never drift.

**Merges to `master` release automatically:** a merge that includes a
`feat`/`fix`/`perf` commit cuts a new release (semantic-release bumps both version
files, tags `vX.Y.Z`, and builds the platform packages); a docs/chore/ci merge
simply doesn't bump the version but still runs full CI. Prefer **merge commits**
(not squash) so the individual conventional commits are preserved for versioning.

Check your commits locally before opening a PR:

```bash
python tools/check_conventional_commits.py origin/master HEAD
```

## Working on the SDK

The repo is a [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/).
`sdk/` holds a second distribution, `nparseplus-sdk` — the plugin contract
third parties build against. `uv sync` resolves both; `uv run pytest` runs
`sdk/tests` alongside `tests/` (they're both in `testpaths`), so an SDK
change is covered by the ordinary test command.

Two things make it different from the rest of the tree:

- **It is versioned separately.** `sdk/src/nparseplus_sdk/__init__.py`'s
  `__version__` is its single source of truth and semantic-release does not
  touch it — semantic-release owns the app's `v*` tags only. Publishing the
  SDK is a deliberate, manual `sdk-v<X.Y.Z>` tag, which triggers
  `.github/workflows/release-sdk.yml`; the workflow refuses to publish if
  the tag and that literal disagree.
- **Everything exported from `nparseplus_sdk/__init__.py` is public API.**
  Under the 1.x promise it is additive-only: new names and new optional
  arguments are fine, renaming, removing, or changing the meaning of an
  existing one is not — plugins pin `requires_sdk` ranges against it, and
  the host refuses a plugin whose range doesn't admit the bundled SDK.
  Anything genuinely internal stays out of `__all__`. The policy users read
  is
  [docs/plugins/versioning.md](https://prokopto-dev.github.io/nparse-plus/latest/plugins/versioning/).

Host-side plugin code (`src/nparseplus/core/plugins/`, `ui/plugin*.py`)
carries no such promise and moves with the app's version.

### Cutting an SDK release

1. Bump `__version__` in `sdk/src/nparseplus_sdk/__init__.py`.
2. Commit, then tag `sdk-v<X.Y.Z>` matching that literal exactly and push the
   tag: `git tag sdk-v1.1.0 && git push origin sdk-v1.1.0`.
3. `.github/workflows/release-sdk.yml` verifies the tag against `__version__`,
   runs `uv build --package nparseplus-sdk`, smoke-tests the built wheel in a
   clean venv (imports it, checks the reported version, runs
   `nparseplus-plugin --help`, and asserts the wheel does not pull in
   `nparseplus`), then publishes to PyPI. It also accepts a `workflow_dispatch`
   with an existing tag.

Raise the app's `nparseplus-sdk` range in the root `pyproject.toml` only after
the matching version is live on PyPI.

Publishing uses **PyPI Trusted Publishing** (OIDC) — no API token, no
repository secret. Two pieces of configuration make that work, both already in
place: the trusted publisher on the
[project's PyPI settings](https://pypi.org/manage/project/nparseplus-sdk/settings/publishing/)
(owner `prokopto-dev`, repository `nparse-plus`, workflow `release-sdk.yml`,
environment `pypi`), and the GitHub `pypi` environment the workflow declares.
Adding a required reviewer to that environment makes every publish pause for a
human approval — the last gate before an immutable upload. If you do, leave
"prevent self-review" off: as a sole reviewer you could not approve your own
tag and the run would hang until it expired.

## Development

See [the docs](https://prokopto-dev.github.io/nparse-plus/latest/development/) for
setup, tests (`uv run pytest`), linting (`uv run ruff check .`), and the release
pipeline. The one architecture rule that matters most: `nparseplus.core` /
`config` / `net` never import Qt (a test enforces it).
