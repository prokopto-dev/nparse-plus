"""Unit tests for the Conventional-Commit PR gate
(``tools/check_conventional_commits.py``)."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import check_conventional_commits as cc  # noqa: E402

ALLOWED = ["feat", "fix", "perf", "build", "chore", "ci", "docs", "style", "refactor", "test"]
MINOR = ["feat"]
PATCH = ["fix", "perf"]


def _classify(commits: list[tuple[str, str]]) -> tuple[list[str], str]:
    return cc.classify(commits, ALLOWED, MINOR, PATCH)


def test_feat_is_minor() -> None:
    invalid, bump = _classify([("feat(timers): add thing", "")])
    assert invalid == [] and bump == "minor"


def test_fix_and_perf_are_patch() -> None:
    invalid, bump = _classify([("fix: x", ""), ("perf(core): y", "")])
    assert invalid == [] and bump == "patch"


def test_docs_and_chore_are_no_bump() -> None:
    invalid, bump = _classify([("docs: update", ""), ("chore(ci): tidy", "")])
    assert invalid == [] and bump == "none"


def test_highest_bump_wins() -> None:
    invalid, bump = _classify([("docs: d", ""), ("fix: f", ""), ("feat: x", "")])
    assert invalid == [] and bump == "minor"


def test_breaking_bang_is_major() -> None:
    invalid, bump = _classify([("feat(api)!: drop old field", "")])
    assert invalid == [] and bump == "major"


def test_breaking_footer_is_major() -> None:
    invalid, bump = _classify([("fix: adjust", "BREAKING CHANGE: signature changed")])
    assert invalid == [] and bump == "major"


def test_non_conforming_subject_is_invalid() -> None:
    invalid, bump = _classify([("WIP stuff", ""), ("feat: ok", "")])
    assert invalid == ["WIP stuff"]
    assert bump == "minor"  # still computed from the valid commits


def test_unknown_type_is_invalid() -> None:
    invalid, _ = _classify([("wip: not a real type", "")])
    assert invalid == ["wip: not a real type"]


def test_load_tags_matches_release_config() -> None:
    allowed, minor, patch = cc.load_tags()
    assert "feat" in allowed
    assert minor == ["feat"]
    assert set(patch) == {"fix", "perf"}
