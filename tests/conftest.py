"""Shared test helpers.

The import-poison fixture below backs every "this layer must not import X"
guard in the suite: the Qt-free architecture rule (tests/test_architecture.py),
the plugins-off-means-nothing-imported rule (tests/core/plugins/
test_master_toggle.py), and the plugin template's standalone-mode check
(tests/core/plugins/test_template.py).
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping, Sequence

import pytest

# A meta-path finder that refuses the named modules and their submodules.
#
# It MUST implement find_spec. The old find_module/load_module protocol was
# removed from CPython's import system in 3.12, so a finder providing only
# those is silently skipped and the "poison" lets everything through.
_PRELUDE = """
import sys
from importlib.abc import MetaPathFinder

_FORBIDDEN = {forbidden!r}


class _Poison(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        for name in _FORBIDDEN:
            if fullname == name or fullname.startswith(name + "."):
                raise ImportError(f"import of {{fullname!r}} is forbidden here")
        return None


sys.meta_path.insert(0, _Poison())
"""


def poison_prelude(forbidden: Sequence[str]) -> str:
    """Python source that makes importing ``forbidden`` (or submodules) raise."""
    return _PRELUDE.format(forbidden=list(forbidden))


def run_poisoned(
    forbidden: Sequence[str],
    body: str,
    *,
    env: Mapping[str, str] | None = None,
    timeout: float = 120,
) -> subprocess.CompletedProcess[str]:
    """Run ``body`` in a subprocess where ``forbidden`` cannot be imported."""
    return subprocess.run(
        [sys.executable, "-c", poison_prelude(forbidden) + body],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=dict(env) if env is not None else None,
    )


@pytest.fixture
def poisoned_import():
    """`run_poisoned` as a fixture — importlib import-mode hides tests/conftest."""
    return run_poisoned
