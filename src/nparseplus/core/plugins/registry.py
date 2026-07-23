"""The plugin registry index — schema, fetching, and compatibility checks.

The registry is deliberately static: a curated ``index.json`` published on
GitHub Pages of the (planned) ``prokopto-dev/nparseplus-plugins`` repo,
maintained by pull-request review. See docs/plugins/registry.md for the
full specification. Trust comes from sha256 pinning: the index records the
hash of each reviewed release artifact, and the installer refuses a
download whose bytes don't match — the URL is transport, the hash is the
security boundary.

Everything here is Qt-free and network-injectable; the manager UI runs
``fetch_index`` on a worker thread.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nparseplus_sdk import PluginMeta, check_compat
from nparseplus_sdk.plugin import PLUGIN_ID_RE

REGISTRY_SCHEMA_VERSION = 1

# GitHub Pages of the curated registry repo. Not live yet — the UI treats
# fetch failures as "registry unavailable", nothing else depends on it.
DEFAULT_REGISTRY_URL = "https://prokopto-dev.github.io/nparseplus-plugins/index.json"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class RegistryRelease(BaseModel):
    """One reviewed, downloadable release of a plugin."""

    model_config = ConfigDict(frozen=True)

    version: str
    url: str
    sha256: str
    requires_sdk: str = ">=1.0,<2"
    min_app_version: str | None = None

    @field_validator("url")
    @classmethod
    def _https_only(cls, value: str) -> str:
        if not value.lower().startswith("https://"):
            raise ValueError("release url must be https://")
        return value

    @field_validator("sha256")
    @classmethod
    def _hex_digest(cls, value: str) -> str:
        value = value.lower()
        if not _SHA256_RE.match(value):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return value


class RegistryPlugin(BaseModel):
    """A plugin listing in the index."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    description: str = ""
    author: str = ""
    homepage: str = ""
    latest: RegistryRelease

    @field_validator("id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        if not PLUGIN_ID_RE.match(value):
            raise ValueError(f"plugin id must match {PLUGIN_ID_RE.pattern}")
        return value


class RegistryIndex(BaseModel):
    schema_version: int = REGISTRY_SCHEMA_VERSION
    plugins: list[RegistryPlugin] = Field(default_factory=list)


def parse_index(raw: bytes | str) -> RegistryIndex:
    """Parse and validate an index document; raises ValueError on garbage.

    A newer ``schema_version`` is rejected (this app doesn't know how to
    read it — the message tells the user to update nParse+).
    """
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"registry index is not valid JSON: {exc}") from exc
    try:
        index = RegistryIndex.model_validate(payload)
    except Exception as exc:
        raise ValueError(f"registry index is malformed: {exc}") from exc
    if index.schema_version > REGISTRY_SCHEMA_VERSION:
        raise ValueError(
            f"registry schema {index.schema_version} is newer than this app "
            f"understands ({REGISTRY_SCHEMA_VERSION}) — update nParse+"
        )
    return index


def fetch_index(url: str, fetch: Callable[[str], bytes] | None = None) -> RegistryIndex:
    """Download and parse the index. https-only; ``fetch`` injectable.

    Raises ValueError on any failure (transport or content) with a message
    fit for the "registry unavailable" UI state.
    """
    if not url.lower().startswith("https://"):
        raise ValueError("registry url must be https://")
    if fetch is None:

        def fetch(target_url: str) -> bytes:
            import httpx

            response = httpx.get(target_url, timeout=15.0, follow_redirects=True)
            response.raise_for_status()
            return response.content

    try:
        raw = fetch(url)
    except Exception as exc:
        raise ValueError(f"could not reach the registry: {exc}") from exc
    return parse_index(raw)


def release_compat(
    release: RegistryRelease,
    *,
    sdk_version: str,
    app_version: str,
) -> str | None:
    """None if this release can load here, else the human-readable reason.

    Reuses the SDK's load-time handshake so the Browse pre-filter can never
    disagree with what the host would decide after download.
    """
    meta = PluginMeta(
        id="registry-check",  # placeholder; only the version fields matter
        name="registry-check",
        version=release.version,
        requires_sdk=release.requires_sdk,
        min_app_version=release.min_app_version,
    )
    return check_compat(meta, sdk_version=sdk_version, app_version=app_version)


def update_available(installed_version: str, release: RegistryRelease) -> bool:
    """True if the index release is strictly newer; garbage versions -> False."""
    from packaging.version import InvalidVersion, Version

    try:
        return Version(release.version) > Version(installed_version)
    except InvalidVersion:
        return False
