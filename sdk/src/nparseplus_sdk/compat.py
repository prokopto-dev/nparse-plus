"""Version-compatibility handshake between a plugin, the SDK, and the app.

The host bundles exactly one SDK version; a plugin declares the SDK range it
was built against (``PluginMeta.requires_sdk``, PEP 440 specifier) and
optionally a minimum app version. ``check_compat`` returns ``None`` when the
plugin may load, else a human-readable refusal reason — malformed version
strings are a reason, never an exception.
"""

from __future__ import annotations

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from nparseplus_sdk.plugin import PluginMeta


def check_compat(
    meta: PluginMeta,
    *,
    sdk_version: str,
    app_version: str | None = None,
) -> str | None:
    """Return None if the plugin is compatible, else the reason it is not."""
    try:
        spec = SpecifierSet(meta.requires_sdk)
    except InvalidSpecifier:
        return f"invalid requires_sdk specifier: {meta.requires_sdk!r}"
    try:
        sdk = Version(sdk_version)
    except InvalidVersion:
        return f"host SDK version is unparseable: {sdk_version!r}"
    if not spec.contains(sdk, prereleases=True):
        return f"plugin requires SDK {meta.requires_sdk}, but this app bundles SDK {sdk_version}"
    if meta.min_app_version is not None and app_version is not None:
        try:
            needed = Version(meta.min_app_version)
        except InvalidVersion:
            return f"invalid min_app_version: {meta.min_app_version!r}"
        try:
            have = Version(app_version)
        except InvalidVersion:
            return f"app version is unparseable: {app_version!r}"
        if have < needed:
            return (
                f"plugin requires nParse+ >= {meta.min_app_version}, "
                f"but this is nParse+ {app_version}"
            )
    return None
