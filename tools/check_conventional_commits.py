#!/usr/bin/env python
"""Check that a commit range's subjects are Conventional Commits that
python-semantic-release can version, and report the resulting bump.

The allowed / minor / patch commit types are read straight from
``pyproject.toml``'s ``[tool.semantic_release.commit_parser_options]`` so this
gate can never drift from the release config. Used by
``.github/workflows/pr-commit-check.yml`` over a PR's commits; also runnable
locally against any range:

    python tools/check_conventional_commits.py origin/master HEAD

Exit code is non-zero when any non-merge commit subject is not a valid
Conventional Commit of an allowed type. A range whose commits are only
non-bumping types (docs/chore/ci/...) passes and reports "no version bump" —
that is expected and allowed.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"

_BUMP_ORDER = {"none": 0, "patch": 1, "minor": 2, "major": 3}


def load_tags() -> tuple[list[str], list[str], list[str]]:
    """(allowed, minor, patch) commit types from the semantic-release config."""
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    opts = data["tool"]["semantic_release"]["commit_parser_options"]
    return list(opts["allowed_tags"]), list(opts["minor_tags"]), list(opts["patch_tags"])


def subject_pattern(allowed: list[str]) -> re.Pattern[str]:
    """Conventional-commit subject matcher: ``type(scope)!: description``."""
    types = "|".join(re.escape(t) for t in allowed)
    return re.compile(rf"^(?:{types})(?:\([^)]+\))?!?: .+")


def _commit_type(subject: str) -> str:
    return subject.split("(", 1)[0].split("!", 1)[0].split(":", 1)[0].strip()


def _is_breaking(subject: str, body: str) -> bool:
    prefix = subject.split(":", 1)[0]
    return prefix.rstrip().endswith("!") or "BREAKING CHANGE" in body


def classify(
    commits: list[tuple[str, str]],
    allowed: list[str],
    minor: list[str],
    patch: list[str],
) -> tuple[list[str], str]:
    """Return (invalid_subjects, bump) where bump is major|minor|patch|none.

    ``commits`` is a list of (subject, body) tuples.
    """
    matcher = subject_pattern(allowed)
    invalid: list[str] = []
    bump = "none"
    for subject, body in commits:
        if matcher.match(subject) is None:
            invalid.append(subject)
            continue
        if _is_breaking(subject, body):
            level = "major"
        elif _commit_type(subject) in minor:
            level = "minor"
        elif _commit_type(subject) in patch:
            level = "patch"
        else:
            level = "none"
        if _BUMP_ORDER[level] > _BUMP_ORDER[bump]:
            bump = level
    return invalid, bump


def _range_commits(base: str, head: str) -> list[tuple[str, str]]:
    """(subject, body) for each non-merge commit in ``base..head``."""
    result = subprocess.run(
        ["git", "log", "--no-merges", "--format=%s%x00%b%x1e", f"{base}..{head}"],
        capture_output=True,
        text=True,
        check=True,
    )
    commits: list[tuple[str, str]] = []
    for record in result.stdout.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        subject, _, body = record.partition("\x00")
        commits.append((subject.strip(), body))
    return commits


def _emit(line: str) -> None:
    print(line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def main(argv: list[str]) -> int:
    base = argv[1] if len(argv) > 1 else "origin/master"
    head = argv[2] if len(argv) > 2 else "HEAD"
    allowed, minor, patch = load_tags()
    commits = _range_commits(base, head)
    if not commits:
        _emit(f"No non-merge commits in `{base}..{head}` — nothing to check.")
        return 0

    invalid, bump = classify(commits, allowed, minor, patch)
    _emit(f"### Conventional Commits check ({len(commits)} commit(s) in `{base}..{head}`)")
    if invalid:
        _emit("**Non-conforming commit subjects:**")
        for subject in invalid:
            _emit(f"- `{subject}`")
        _emit("")
        _emit(f"Allowed types: {', '.join(allowed)} — e.g. `feat(scope): summary`.")
        return 1

    if bump == "none":
        _emit("All commits conform. **No version bump** (docs/chore/ci-only) — still builds.")
    else:
        _emit(f"All commits conform. This merge would trigger a **{bump}** release.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
