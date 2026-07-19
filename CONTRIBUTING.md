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

## Development

See [the docs](https://prokopto-dev.github.io/nparse-plus/latest/development/) for
setup, tests (`uv run pytest`), linting (`uv run ruff check .`), and the release
pipeline. The one architecture rule that matters most: `nparseplus.core` /
`config` / `net` never import Qt (a test enforces it).
