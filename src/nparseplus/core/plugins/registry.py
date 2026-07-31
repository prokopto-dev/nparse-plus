"""The plugin registry index — schema, fetching, and compatibility checks.

The registry is deliberately static: a curated ``index.json`` published on
GitHub Pages of the ``prokopto-dev/nparseplus-plugins`` repo, maintained by
pull-request review. See docs/plugins/registry.md for the
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
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nparseplus_sdk import PluginMeta, check_compat
from nparseplus_sdk.plugin import PLUGIN_ID_RE

if TYPE_CHECKING:
    from nparseplus.config.settings import PluginsSettings

REGISTRY_SCHEMA_VERSION = 1

# How many failing registries the status summary names before collapsing.
_MAX_REPORTED_FAILURES = 5

# GitHub Pages of the curated registry repo. Always offered, never stored:
# resolve_registries synthesizes it so changing this constant moves every
# user instead of stranding them on whatever a past release wrote to disk.
DEFAULT_REGISTRY_URL = "https://prokopto-dev.github.io/nparseplus-plugins/index.json"
DEFAULT_REGISTRY_NAME = "nParse+ registry (built-in)"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# A curated index of a few hundred plugins is tens of KB; anything past this
# is not an index we want to hold in memory while parsing.
MAX_INDEX_BYTES = 5 * 1024 * 1024


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

    The default fetch goes through the installer's ``fetch_https_bytes``:
    https is re-asserted on every redirect hop and the body is streamed
    under a byte budget. This matters more here than anywhere else — a
    forged index supplies both the download URL and the hash it will be
    checked against, so the index itself has to arrive over TLS end to end.

    Raises ValueError on any failure (transport or content) with a message
    fit to show the user; ``fetch_indexes`` turns it into a per-registry
    failure so one dead registry cannot blank the rest.
    """
    if not url.lower().startswith("https://"):
        raise ValueError("registry url must be https://")
    if fetch is None:

        def fetch(target_url: str) -> bytes:
            # Imported lazily: the index schema shouldn't depend on the
            # installer at module scope, only on its transport at call time.
            from nparseplus.core.plugins.install import fetch_https_bytes

            return fetch_https_bytes(target_url, timeout=15.0, max_bytes=MAX_INDEX_BYTES)

    try:
        raw = fetch(url)
    except Exception as exc:
        raise ValueError(f"could not reach the registry: {exc}") from exc
    return parse_index(raw)


@dataclass(frozen=True)
class ResolvedRegistry:
    """One index to fetch: the built-in default, a user-added registry, or a
    plugin's own declared update feed.

    ``kind`` exists because ``name`` renders straight into the Source column
    and the "update available … from <name>" line. A self-published feed
    dressed as a plain registry would read exactly like one the user chose to
    trust, so it says which it is and the renderers key off that rather than
    off the name. It is a trailing defaulted field: every existing
    construction site keeps working unchanged.
    """

    url: str
    name: str
    enabled: bool
    is_default: bool
    kind: Literal["default", "user", "self"] = "user"

    @property
    def is_self_published(self) -> bool:
        """True for a feed a plugin declared about itself — nobody vouched."""
        return self.kind == "self"


@dataclass(frozen=True)
class RegistryFetchResult:
    """What one registry returned. Exactly one of index/error is set."""

    registry: ResolvedRegistry
    index: RegistryIndex | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.index is not None


@dataclass(frozen=True)
class MergedListing:
    """A listing plus the registry that vouched for it.

    The provenance is a wrapper rather than a field on ``RegistryPlugin``
    because a registry document cannot truthfully say which registry served
    it — a mirror would copy the claim verbatim and the app would display a
    lie. Which URL we fetched from is knowledge only the client has.
    """

    registry: ResolvedRegistry
    plugin: RegistryPlugin


@dataclass(frozen=True)
class MultiFetchResult:
    """Every registry's outcome, in the order they were asked."""

    results: list[RegistryFetchResult]

    @property
    def listings(self) -> list[MergedListing]:
        return [
            MergedListing(registry=result.registry, plugin=plugin)
            for result in self.results
            if result.index is not None
            for plugin in result.index.plugins
        ]

    @property
    def failures(self) -> list[RegistryFetchResult]:
        return [result for result in self.results if result.error is not None]

    def summary_lines(self) -> list[str]:
        """Human-readable status, pure so the dialog stays a thin renderer."""
        if not self.results:
            return ["No plugin registries are enabled — tick one in Settings > Plugins."]
        failures = self.failures
        lines: list[str] = []
        if failures:
            shown = failures[:_MAX_REPORTED_FAILURES]
            lines.append(
                f"Could not reach {len(failures)} of {len(self.results)} "
                f"{'registry' if len(self.results) == 1 else 'registries'}:"
            )
            lines += [
                f"  {result.registry.name} ({result.registry.url}) — "
                f"{_truncate(result.error or '')}"
                for result in shown
            ]
            if len(failures) > len(shown):
                lines.append(f"  +{len(failures) - len(shown)} more")
        return lines


def _truncate(text: str, limit: int = 160) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def registry_display_name(url: str, name: str = "") -> str:
    """A label for a registry: its given name, else its host."""
    if name:
        return name
    host = url.partition("://")[2].partition("/")[0]
    return host or url


def resolve_registries(plugins: PluginsSettings) -> list[ResolvedRegistry]:
    """The built-in default first, then the user's, in settings order.

    The default is synthesized from the constant rather than read from
    settings, so an app update that changes DEFAULT_REGISTRY_URL moves every
    user instead of stranding them. A user entry pointing at the same URL
    collapses into the built-in row, which is what makes promoting a
    community registry to default a non-event.
    """
    default = ResolvedRegistry(
        url=DEFAULT_REGISTRY_URL,
        name=DEFAULT_REGISTRY_NAME,
        enabled=plugins.default_registry_enabled,
        is_default=True,
        kind="default",
    )
    resolved = [default]
    for source in plugins.registries:
        if source.url.lower() == DEFAULT_REGISTRY_URL.lower():
            continue
        resolved.append(
            ResolvedRegistry(
                url=source.url,
                name=registry_display_name(source.url, source.name),
                enabled=source.enabled,
                is_default=False,
                kind="user",
            )
        )
    return resolved


def fetch_indexes(
    registries: Sequence[ResolvedRegistry],
    fetch: Callable[[str], bytes] | None = None,
    max_workers: int = 4,
) -> MultiFetchResult:
    """Fetch every given registry, reporting each outcome separately.

    Takes exactly the list to fetch — it does NOT filter on ``enabled``, so
    ``summary_lines`` never has to explain a row it silently skipped. The
    caller passes the enabled ones.

    Concurrent, because the failure that matters is a dead registry sitting
    in front of a live one: serially that costs the full per-URL timeout
    before the working registry is even tried. Results are placed by index,
    not completion order, so the merged table is deterministic.
    """
    if not registries:
        return MultiFetchResult(results=[])

    def one(registry: ResolvedRegistry) -> RegistryFetchResult:
        try:
            return RegistryFetchResult(registry=registry, index=fetch_index(registry.url, fetch))
        except Exception as exc:
            return RegistryFetchResult(registry=registry, error=str(exc))

    if len(registries) == 1 or max_workers <= 1:
        return MultiFetchResult(results=[one(registry) for registry in registries])

    with ThreadPoolExecutor(max_workers=min(len(registries), max_workers)) as pool:
        return MultiFetchResult(results=list(pool.map(one, registries)))


def duplicate_listing_ids(listings: Sequence[MergedListing]) -> set[str]:
    """Ids offered by more than one registry — a conflict worth surfacing."""
    seen: dict[str, set[str]] = {}
    for listing in listings:
        seen.setdefault(listing.plugin.id, set()).add(listing.registry.url)
    return {plugin_id for plugin_id, urls in seen.items() if len(urls) > 1}


def best_update(
    listings: Sequence[MergedListing],
    *,
    plugin_id: str,
    installed_version: str,
    installed_registry_url: str,
    sdk_version: str,
    app_version: str,
) -> MergedListing | None:
    """The update to offer for an installed plugin, or None.

    Only compatible releases count — offering an update the app would then
    refuse to load is worse than offering none. Among those, the registry the
    plugin was installed from wins if it has anything to offer: silently
    promoting a different registry's build of the same id is a trust hop the
    user has not agreed to, and the caller says so out loud when it happens.
    """
    candidates = [
        listing
        for listing in listings
        if listing.plugin.id == plugin_id
        and update_available(installed_version, listing.plugin.latest)
        and release_compat(listing.plugin.latest, sdk_version=sdk_version, app_version=app_version)
        is None
    ]
    if not candidates:
        return None
    if installed_registry_url:
        same = [
            listing
            for listing in candidates
            if listing.registry.url.lower() == installed_registry_url.lower()
        ]
        if same:
            return _highest(same)
    return _highest(candidates)


def _highest(listings: Sequence[MergedListing]) -> MergedListing:
    """Highest version; ties keep registry order (the caller's ordering)."""
    from packaging.version import InvalidVersion, Version

    def key(item: tuple[int, MergedListing]) -> tuple[Version, int]:
        index, listing = item
        try:
            version = Version(listing.plugin.latest.version)
        except InvalidVersion:  # pragma: no cover - update_available filters these
            version = Version("0")
        return version, -index

    return max(enumerate(listings), key=key)[1]


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
