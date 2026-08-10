"""Shared test helpers.

The import-poison fixture below backs every "this layer must not import X"
guard in the suite: the Qt-free architecture rule (tests/test_architecture.py),
the plugins-off-means-nothing-imported rule (tests/core/plugins/
test_master_toggle.py), and the plugin template's standalone-mode check
(tests/core/plugins/test_template.py).

The session fixture at the bottom is the other kind of guard: no test may
open a real connection to a plugin registry.
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


@pytest.fixture(scope="session", autouse=True)
def no_live_registry_fetches():
    """Refuse any plugin fetch that would actually leave the machine.

    The suite mocks registry traffic at two levels — a ``fetch=`` callable
    passed into ``fetch_index``, or an ``httpx.MockTransport`` handed to
    ``fetch_https_bytes`` — so a call arriving here with neither is one
    nobody meant to make. Before this guard, one did: a test that built the
    plugin UI armed the 12 s post-launch update check, whose QTimer fired
    after that test had finished and started a live fetch inside an
    unrelated one. Concurrent sockets against another test's Qt teardown
    segfaulted the whole run in CI, which reads as "semantic release is
    broken" rather than "a test reached the internet".

    Session-scoped, because the point is to cover the gaps *between* tests
    too: a leaked timer fires wherever it likes, and a function-scoped patch
    would have been unwound by then.

    The transport seam stays open on purpose. ``patch_default_transport``
    and friends wrap whatever this attribute holds and re-call it with a
    MockTransport, so they keep working — they land on the `transport is
    not None` path and get the real implementation, offline.
    """
    from nparseplus.core.plugins import install

    real = install.fetch_https_bytes

    def guarded(url: str, *, transport=None, **kwargs):
        if transport is None:
            raise AssertionError(
                f"the test suite must not fetch {url!r} for real — pass a "
                "transport=httpx.MockTransport(...), inject fetch=, or turn "
                "the caller off (e.g. settings.plugins.update_check = False)"
            )
        return real(url, transport=transport, **kwargs)

    install.fetch_https_bytes = guarded
    try:
        yield
    finally:
        install.fetch_https_bytes = real
