"""nparseplus-sdk — the stable contract for building nParse+ plugins.

Everything importable from this package root is the public plugin API and
follows semantic versioning: breaking changes to these names only happen in
a new SDK major version, and the host app refuses plugins whose
``requires_sdk`` range does not admit the bundled SDK.

Submodules:

- ``nparseplus_sdk.events`` — the typed bus event classes (host re-export).
- ``nparseplus_sdk.ui`` — the ``PluginWindow`` Qt base (host re-export).
- ``nparseplus_sdk.skin`` — what the app currently looks like (host
  re-export): the colour/type snapshot an add-on dresses itself from.
- ``nparseplus_sdk.eqfiles`` — EQ install-file helpers (host re-export):
  preflight, backup-first, splice one ini section.
- ``nparseplus_sdk.testing`` — ``FakePluginContext`` for plugin unit tests.
- ``nparseplus_sdk.validate`` / the ``nparseplus-plugin`` CLI — developer
  validation.

This package deliberately has no install-time dependency on ``nparseplus``:
plugins execute inside the host app, which provides the runtime behind the
lazy re-export modules above.
"""

from __future__ import annotations

from nparseplus_sdk.compat import check_compat
from nparseplus_sdk.context import (
    LineInfoLike,
    LineParser,
    PluginContext,
    PluginStorage,
    Speaker,
    Unsubscribe,
    WindowTimerLike,
)
from nparseplus_sdk.plugin import (
    PLUGIN_ID_RE,
    NParsePlugin,
    PluginMeta,
    PluginSettingsPageSpec,
    PluginWindowContext,
    PluginWindowSpec,
)

# THE single source of the SDK version: pyproject declares dynamic = ["version"]
# and hatchling reads this literal (see [tool.hatch.version] in sdk/pyproject.toml),
# so the wheel, uv.lock and this module cannot disagree.
#
# Deliberately NOT importlib.metadata.version("nparseplus-sdk"): a frozen
# PyInstaller build has no dist metadata, so that call raises and whatever
# fallback sits next to it always wins in shipped DMG/zip/flatpak builds. That
# made every release report the fallback version to check_compat(), silently
# refusing plugins that declared a newer requires_sdk range — a bug invisible
# in CI and only reproducible from a release artifact. A plain literal has no
# such second code path. Bump this, tag `sdk-v<X.Y.Z>` (see sdk/README.md).
__version__ = "1.4.1"

SDK_VERSION = __version__

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
    "WindowTimerLike",
    "check_compat",
]
