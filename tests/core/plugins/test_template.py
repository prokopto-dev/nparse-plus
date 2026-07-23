"""Keep templates/plugin-repo from rotting while it lives in this repo."""

from __future__ import annotations

from pathlib import Path

from nparseplus_sdk.validate import validate_plugin

TEMPLATE = Path(__file__).resolve().parents[3] / "templates" / "plugin-repo"


def test_template_plugin_validates() -> None:
    report = validate_plugin(TEMPLATE / "my_nparse_plugin")
    assert report.ok, report.errors
    assert report.meta is not None and report.meta.id == "my-nparse-plugin"
    assert report.window_count == 1


def test_template_files_present_and_consistent() -> None:
    for relative in (
        "README.md",
        "TEMPLATE_SETUP.md",
        ".gitignore",
        "pyproject.toml",
        "my_nparse_plugin/__init__.py",
        "my_nparse_plugin/window.py",
        "tests/test_plugin.py",
        ".github/workflows/ci.yml",
        ".github/workflows/release.yml",
    ):
        assert (TEMPLATE / relative).is_file(), f"template file missing: {relative}"

    # The workflows' PLUGIN_DIR must match the actual package directory.
    for workflow in ("ci.yml", "release.yml"):
        text = (TEMPLATE / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
        assert "PLUGIN_DIR: my_nparse_plugin" in text, workflow
    # The release flow packages the single-root layout the installer expects.
    release = (TEMPLATE / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "sha256" in release and "registry-entry.json" in release


def test_template_unit_tests_pass_standalone() -> None:
    """Run the template's own pytest suite in a subprocess from its root.

    A template user gets imports via ``pip install -e .``; here PYTHONPATH
    stands in for that.
    """
    import os
    import subprocess
    import sys

    env = dict(os.environ, PYTHONPATH=str(TEMPLATE))
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests"],
        cwd=TEMPLATE,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
