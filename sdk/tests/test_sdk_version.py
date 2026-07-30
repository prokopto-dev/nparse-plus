"""The SDK version must have exactly one source.

``sdk/pyproject.toml`` declares ``dynamic = ["version"]`` and points hatchling
at ``nparseplus_sdk.__version__``, so the literal in ``__init__.py`` is what
ends up in the wheel, in the installed dist metadata, and in ``uv.lock``.

These tests catch the two ways that can rot: someone reintroducing a second
copy of the version (an ``importlib.metadata`` fallback, a literal back in
pyproject), and a hand-edited ``__version__`` in an environment that was never
re-synced — the installed metadata then still reports the old number, which is
what any third-party plugin calling ``importlib.metadata.version`` would see.
"""

from __future__ import annotations

import tomllib
from importlib.metadata import version as dist_version
from pathlib import Path

import nparseplus_sdk

SDK_PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_installed_metadata_matches_the_literal() -> None:
    assert dist_version("nparseplus-sdk") == nparseplus_sdk.__version__


def test_sdk_version_is_the_literal() -> None:
    # SDK_VERSION is what the host hands check_compat(); it must not be
    # derived through anything that can fail (and fall back) at runtime.
    assert nparseplus_sdk.__version__ == nparseplus_sdk.SDK_VERSION


def test_pyproject_declares_no_literal_version() -> None:
    data = tomllib.loads(SDK_PYPROJECT.read_text(encoding="utf-8"))
    assert "version" not in data["project"], (
        "sdk/pyproject.toml must keep the version dynamic; a literal here is a "
        "second source that drifts from nparseplus_sdk.__version__"
    )
    assert "version" in data["project"]["dynamic"]
    assert data["tool"]["hatch"]["version"]["path"] == "src/nparseplus_sdk/__init__.py"
