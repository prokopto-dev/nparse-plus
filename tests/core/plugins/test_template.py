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


# The body below runs the template's own suite the way the template repo's
# CI does: SDK installed, app absent. Kept as source (not a helper import)
# because it executes in a subprocess where nparseplus cannot be imported.
_SDK_ONLY_PYTEST = """
import os
import sys

TEMPLATE = {template!r}
sys.path.insert(0, TEMPLATE)
os.chdir(TEMPLATE)

# Sanity-check the poison before spending time in pytest.
try:
    import nparseplus  # noqa: F401
except ImportError:
    pass
else:
    raise SystemExit("nparseplus was importable — the poison did not take")

import pytest

raise SystemExit(pytest.main(["-q", "-p", "no:cacheprovider", "tests"]))
"""


def test_template_unit_tests_pass_with_only_the_sdk_installed(poisoned_import) -> None:
    """The template must work in its OWN CI, where nparseplus is NOT installed.

    ``templates/plugin-repo/.github/workflows/ci.yml`` installs only
    ``nparseplus-sdk``, so the lazy host re-exports
    (``nparseplus_sdk.events``/``.timers``) raise ImportError. The plugin has
    to degrade gracefully — register its window, skip the host subscription —
    or a template user's very first push goes red. Running the suite in-repo
    (the test above) can never catch that, because here nparseplus *is*
    importable.
    """
    result = poisoned_import(["nparseplus"], _SDK_ONLY_PYTEST.format(template=str(TEMPLATE)))
    assert result.returncode == 0, result.stdout + result.stderr
