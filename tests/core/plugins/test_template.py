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
    # The release flow packages the single-root layout the installer expects
    # and publishes the digest a registry publish cross-checks against.
    release = (TEMPLATE / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "sha256" in release
    # It must NOT compose a registry entry, and must NOT grow the publish
    # request itself (#147). The listing route is an authenticated POST to
    # the registry server, and a reusable workflow for it is that server's
    # own next piece of work — writing it here means writing it twice, and
    # a template that publishes half-correctly is worse than one that
    # points at the real thing.
    assert "registry-entry.json" not in release
    assert "/api/v1/plugins/" not in release


def _compose_release_body_script() -> str:
    """The `python - <<'EOF'` block out of the template's compose step.

    Run for real below rather than string-matched: this body is what the
    README tells an author to copy their publish request out of, so what it
    *omits* is a silent bug in somebody else's release, not ours.
    """
    text = (TEMPLATE / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    step = text[text.index("- name: Compose release body") :]
    start = step.index("<<'EOF'\n") + len("<<'EOF'\n")
    block = step[start : step.index("EOF", start)]
    lines = block.splitlines()
    indent = min(len(line) - len(line.lstrip()) for line in lines if line.strip())
    return "\n".join(line[indent:] for line in lines)


def _run_compose(tmp_path, monkeypatch, **meta_kwargs) -> str:
    """Execute that script against a stand-in plugin, return the body."""
    import nparseplus_sdk.loading as loading
    from nparseplus_sdk import PluginMeta

    meta = PluginMeta(id="my-nparse-plugin", name="My Plugin", version="1.2.0", **meta_kwargs)
    plugin = type("Stub", (), {"meta": meta})()
    monkeypatch.setattr(loading, "load_plugin_factory", lambda _dir: lambda: plugin)
    monkeypatch.setenv("PLUGIN_DIR", "my_nparse_plugin")
    monkeypatch.setenv("GITHUB_REPOSITORY", "someone/my-nparse-plugin")
    monkeypatch.setenv("GITHUB_REF_NAME", "v1.2.0")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "my_nparse_plugin.zip").write_bytes(b"not really a zip")

    exec(compile(_compose_release_body_script(), "release.yml", "exec"), {"__name__": "__main__"})
    return (tmp_path / "release-body.md").read_text(encoding="utf-8")


def test_release_body_carries_every_value_a_publish_request_needs(tmp_path, monkeypatch) -> None:
    """The registry POST's inputs, so following the README is copying.

    The digest is the cross-check the registry compares its own against, not
    the value it publishes — but an author still has to send one.
    """
    import hashlib

    body = _run_compose(tmp_path, monkeypatch, requires_sdk=">=1.1,<2")
    assert "https://github.com/someone/my-nparse-plugin/releases/download/v1.2.0/" in body
    assert hashlib.sha256(b"not really a zip").hexdigest() in body
    assert ">=1.1,<2" in body


def test_release_body_keeps_an_authors_app_version_floor(tmp_path, monkeypatch) -> None:
    """min_app_version is optional, and dropping it is not a cosmetic loss.

    It is the mechanism that stops a release needing a newer nParse+ from
    being offered to a build that cannot load it, so a body that quietly
    leaves it out costs the author their compatibility floor.
    """
    body = _run_compose(tmp_path, monkeypatch, min_app_version="2.20.0")
    assert "min_app_version: `2.20.0`" in body


def test_release_body_omits_the_floor_when_there_is_none(tmp_path, monkeypatch) -> None:
    """The default: no floor declared, nothing to copy into the request."""
    body = _run_compose(tmp_path, monkeypatch)
    assert "min_app_version" not in body


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
