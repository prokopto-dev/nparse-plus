"""nparseplus-sdk — the stable contract for building nParse+ plugins.

Everything importable from this package root is the public plugin API and
follows semantic versioning: breaking changes to these names only happen in
a new SDK major version, and the host app refuses plugins whose
``requires_sdk`` range does not admit the bundled SDK.

Submodules:

- ``nparseplus_sdk.events`` — the typed bus event classes (host re-export).
- ``nparseplus_sdk.ui`` — the ``PluginWindow`` Qt base (host re-export).
- ``nparseplus_sdk.testing`` — ``FakePluginContext`` for plugin unit tests.
- ``nparseplus_sdk.validate`` / the ``nparseplus-plugin`` CLI — developer
  validation.

This package deliberately has no install-time dependency on ``nparseplus``:
plugins execute inside the host app, which provides the runtime behind the
lazy re-export modules above.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version

from nparseplus_sdk.compat import check_compat
from nparseplus_sdk.context import (
    LineInfoLike,
    LineParser,
    PluginContext,
    PluginStorage,
    Speaker,
    Unsubscribe,
)
from nparseplus_sdk.plugin import (
    PLUGIN_ID_RE,
    NParsePlugin,
    PluginMeta,
    PluginSettingsPageSpec,
    PluginWindowContext,
    PluginWindowSpec,
)

try:
    SDK_VERSION = _dist_version("nparseplus-sdk")
except PackageNotFoundError:  # pragma: no cover - frozen app / odd installs
    SDK_VERSION = "1.0.0"

__version__ = SDK_VERSION

__all__ = [
    "PLUGIN_ID_RE",
    "SDK_VERSION",
    "LineInfoLike",
    "LineParser",
    "NParsePlugin",
    "PluginContext",
    "PluginMeta",
    "PluginSettingsPageSpec",
    "PluginStorage",
    "PluginWindowContext",
    "PluginWindowSpec",
    "Speaker",
    "Unsubscribe",
    "check_compat",
]
