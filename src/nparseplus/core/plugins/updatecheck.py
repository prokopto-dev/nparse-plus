"""Deciding which installed plugins have an update waiting (Qt-free).

Everything here is pure except :func:`check_for_updates`, which fetches and
takes an injectable ``fetch`` for tests. No new version or compatibility
logic lives in this module: :func:`~nparseplus.core.plugins.registry.best_update`
remains the single function that decides what to offer, and this is the
fan-out across installed plugins plus the provenance classification around
it. Two renderers — the plugins table and the registry browser — read the
results, which is exactly why the decisions are made here and not in either.

**Provenance is the whole game.** An update is only silent when it comes
from the source that vouched for the copy you already have. Anything else
is a publisher change wearing an update's clothes, so it carries
``needs_confirmation`` and the UI has to name both ends before installing.

Feeds come in two shapes and are deliberately not interchangeable:

- the registries the user has ticked, which list many plugins; and
- a plugin's own ``PluginMeta.update_url``, which may only ever speak about
  itself. Anything else in that document is discarded — otherwise one
  installed add-on could offer "updates" for every other plugin on the
  machine.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from nparseplus.core.plugins.registry import (
    MergedListing,
    MultiFetchResult,
    RegistryFetchResult,
    ResolvedRegistry,
    best_update,
    fetch_index,
    fetch_indexes,
    registry_display_name,
    release_compat,
    update_available,
)

logger = logging.getLogger(__name__)

# What a self-published feed is called in the Source column and in tooltips.
# Spelled out rather than signalled with an icon: it has to survive a
# screenshot and a colour-blind user, same as the "(third-party)" marker.
SELF_FEED_SUFFIX = "(self-published)"

SELF_FEED_WARNING = (
    "This update is offered by a feed the plugin itself declares. No registry "
    "reviewed it, and the checksum in that feed is the author's own claim — it "
    "proves the download matches what the author published, not that anyone "
    "else looked at it."
)

ActionKind = Literal[
    "install",
    "update",
    "update_other_source",
    "installed",
    "installed_other_source",
    "incompatible",
]


@dataclass(frozen=True)
class InstalledPlugin:
    """One installed plugin, as the update checker needs to see it.

    Deliberately not ``LoadedPlugin`` or ``PluginEntry``: the checker has to
    cover plugins that never loaded (consent declined, disabled,
    incompatible) as well as ones installed this session, and neither of
    those types spans both.
    """

    plugin_id: str
    version: str
    registry_url: str = ""  # "" = nothing vouched for this copy
    installed_path: Path | None = None
    update_url: str = ""  # the plugin's own declared feed, if any


@dataclass(frozen=True)
class PluginUpdate:
    """A newer, compatible release on offer for an installed plugin."""

    plugin_id: str
    installed_version: str
    listing: MergedListing
    installed_path: Path | None
    same_source: bool
    unknown_provenance: bool

    @property
    def offered_version(self) -> str:
        return self.listing.plugin.latest.version

    @property
    def source_name(self) -> str:
        return self.listing.registry.name

    @property
    def needs_confirmation(self) -> bool:
        """True when taking this update also changes who publishes it.

        ``unknown_provenance`` is tracked separately from ``same_source``
        because the two need different words: one names two registries, the
        other has to admit there is no record of where the installed copy
        came from.
        """
        return not self.same_source


@dataclass(frozen=True)
class ListingAction:
    """What the browser's action button should be for one listing row.

    Decided per *row*, not per plugin id: the browser renders one row per
    (registry, listing) pair, so a v1.0 row from one registry must not
    advertise a v2.0 another registry happens to list further down the table.
    """

    kind: ActionKind
    label: str
    enabled: bool
    tooltip: str = ""


@dataclass(frozen=True)
class UpdateCheckResult:
    """Everything one check produced, ready for a renderer to read."""

    fetched: MultiFetchResult
    updates: list[PluginUpdate] = field(default_factory=list)
    self_feeds: list[RegistryFetchResult] = field(default_factory=list)

    @property
    def listings(self) -> list[MergedListing]:
        """Registry listings only — self-feeds are never browsable."""
        return self.fetched.listings

    def summary_lines(self) -> list[str]:
        """Registry status plus any feed that could not be reached."""
        lines = list(self.fetched.summary_lines())
        broken = [result for result in self.self_feeds if result.error is not None]
        if broken:
            lines.append(
                f"Could not reach {len(broken)} plugin update "
                f"{'feed' if len(broken) == 1 else 'feeds'}:"
            )
            lines += [f"  {result.registry.name} ({result.registry.url})" for result in broken]
        return lines


def self_feed_registry(plugin_id: str, url: str) -> ResolvedRegistry:
    """The provenance stamp for a plugin's own update feed.

    Named after the host it lives on rather than the plugin, because the
    question a user is answering when they see it is "who is serving this",
    and a plugin naming its own feed after itself would beg that question.
    """
    return ResolvedRegistry(
        url=url,
        name=f"{registry_display_name(url)} {SELF_FEED_SUFFIX}",
        enabled=True,
        is_default=False,
        kind="self",
    )


def _classify(installed: InstalledPlugin, listing: MergedListing) -> tuple[bool, bool]:
    """Return ``(same_source, unknown_provenance)`` for an offer.

    A plugin with no recorded registry is the case ``update_url`` exists to
    serve — sideloaded, or self-hosted from the start. Its own feed is the
    only source it ever had, so treating that as a source *change* would make
    the feature permanently two-click for its primary use. Any other offer
    for such a plugin is still flagged: a registry adopting an id nobody
    vouched for is precisely the ambiguity worth stopping on.
    """
    recorded = installed.registry_url.strip().lower()
    offered = listing.registry.url.strip().lower()
    if not recorded:
        is_own_feed = bool(installed.update_url) and offered == installed.update_url.strip().lower()
        return is_own_feed, True
    return offered == recorded, False


def pending_updates(
    installed: Sequence[InstalledPlugin],
    listings: Sequence[MergedListing],
    *,
    sdk_version: str,
    app_version: str,
) -> list[PluginUpdate]:
    """The update to offer for each installed plugin that has one.

    ``best_update`` picks the winner per plugin (compatible releases only,
    preferring the registry that vouched for the install); this adds the
    provenance classification the UI needs to decide whether taking it is a
    one-click action.
    """
    updates: list[PluginUpdate] = []
    for plugin in installed:
        if not plugin.plugin_id or not plugin.version:
            continue
        # With no vouching registry, the plugin's own feed takes the place of
        # one: it is the only source this copy ever had, so it should win the
        # same way an installing registry does. Otherwise a registry that
        # happens to list a higher version under this id would outrank the
        # author's own release and turn every update into a confirmation.
        preferred = plugin.registry_url or plugin.update_url
        listing = best_update(
            listings,
            plugin_id=plugin.plugin_id,
            installed_version=plugin.version,
            installed_registry_url=preferred,
            sdk_version=sdk_version,
            app_version=app_version,
        )
        if listing is None:
            continue
        same_source, unknown = _classify(plugin, listing)
        updates.append(
            PluginUpdate(
                plugin_id=plugin.plugin_id,
                installed_version=plugin.version,
                listing=listing,
                installed_path=plugin.installed_path,
                same_source=same_source,
                unknown_provenance=unknown,
            )
        )
    return updates


def same_source_updates(updates: Sequence[PluginUpdate]) -> list[PluginUpdate]:
    """The updates a bulk action may take without asking anything."""
    return [update for update in updates if not update.needs_confirmation]


def updates_by_id(updates: Sequence[PluginUpdate]) -> dict[str, PluginUpdate]:
    """Index for the row renderer; later entries never displace earlier ones."""
    indexed: dict[str, PluginUpdate] = {}
    for update in updates:
        indexed.setdefault(update.plugin_id, update)
    return indexed


def listing_action(
    merged: MergedListing,
    *,
    installed_version: str,
    installed_registry_url: str,
    is_installed: bool,
    sdk_version: str,
    app_version: str,
) -> ListingAction:
    """The action button for one browser row.

    Order matters. Compatibility is checked first for anything not installed,
    because offering an install the loader would then refuse is worse than
    offering none. For an installed plugin the source question comes first:
    an offer from elsewhere is not an update at all, whatever its version.
    """
    release = merged.plugin.latest
    reason = release_compat(release, sdk_version=sdk_version, app_version=app_version)

    if not is_installed:
        if reason is not None:
            return ListingAction("incompatible", "Incompatible", False, reason)
        return ListingAction("install", "Install", True)

    other_source = bool(installed_registry_url) and (
        merged.registry.url.lower() != installed_registry_url.lower()
    )
    newer = bool(installed_version) and update_available(installed_version, release)

    if other_source:
        # Same id, different publisher. Never a plain "Update" — but if it IS
        # newer, say so, so the user can find it deliberately rather than
        # concluding the app has nothing for them.
        if newer and reason is None:
            return ListingAction(
                "update_other_source",
                f"Update to v{release.version}…",
                True,
                f"This listing comes from {merged.registry.name} ({merged.registry.url}), "
                f"which is not where your copy came from "
                f"({registry_display_name(installed_registry_url)}).\n"
                "The same id from another source may be entirely different code — "
                "nParse+ will ask you to confirm.",
            )
        return ListingAction(
            "installed_other_source",
            "Installed (other source)",
            False,
            f"Installed from {registry_display_name(installed_registry_url)} "
            f"({installed_registry_url}).\nThis listing comes from "
            f"{merged.registry.name} ({merged.registry.url}) — the same id from "
            "another registry may be entirely different code.\n"
            "Uninstall the current copy first if you want this one.",
        )

    if newer and reason is None:
        return ListingAction(
            "update",
            f"Update to v{release.version}",
            True,
            f"You have v{installed_version}. The previous version is kept in the "
            "plugins trash folder.",
        )
    if newer and reason is not None:
        return ListingAction("incompatible", "Incompatible", False, reason)
    return ListingAction("installed", "Installed", False)


def _feed_targets(installed: Sequence[InstalledPlugin]) -> dict[str, str]:
    """``{url: plugin_id}`` for the feeds worth fetching, deduplicated.

    Two plugins declaring the same feed URL is a conflict with no honest
    answer — the document can only be scoped to one id — so the first
    declaration wins and the second is dropped rather than silently letting
    one plugin's feed speak for another's.
    """
    targets: dict[str, str] = {}
    for plugin in installed:
        url = plugin.update_url.strip()
        if not url or not url.lower().startswith("https://"):
            continue
        targets.setdefault(url, plugin.plugin_id)
    return targets


def _fetch_self_feeds(
    targets: dict[str, str],
    fetch: Callable[[str], bytes] | None,
    max_workers: int,
) -> list[RegistryFetchResult]:
    """Fetch each declared feed concurrently; failures stay per-feed."""

    def one(item: tuple[str, str]) -> RegistryFetchResult:
        url, plugin_id = item
        registry = self_feed_registry(plugin_id, url)
        try:
            return RegistryFetchResult(registry=registry, index=fetch_index(url, fetch))
        except Exception as exc:
            return RegistryFetchResult(registry=registry, error=str(exc))

    items = list(targets.items())
    if len(items) <= 1 or max_workers <= 1:
        return [one(item) for item in items]
    with ThreadPoolExecutor(max_workers=min(len(items), max_workers)) as pool:
        return list(pool.map(one, items))


def _self_feed_listings(
    results: Sequence[RegistryFetchResult],
    targets: dict[str, str],
) -> list[MergedListing]:
    """Listings from self-feeds, scoped to the id that declared each feed.

    The impersonation guard: without it, any installed plugin could publish a
    feed listing every other plugin id at version 99 and the app would offer
    them. A feed speaks about its declarer or it says nothing.
    """
    listings: list[MergedListing] = []
    for result in results:
        if result.index is None:
            continue
        owner = targets.get(result.registry.url, "")
        for plugin in result.index.plugins:
            if plugin.id != owner:
                logger.warning(
                    "plugin update feed %s listed %r but may only offer %r — ignored",
                    result.registry.url,
                    plugin.id,
                    owner,
                )
                continue
            listings.append(MergedListing(registry=result.registry, plugin=plugin))
    return listings


def check_for_updates(
    installed: Sequence[InstalledPlugin],
    registries: Sequence[ResolvedRegistry],
    *,
    sdk_version: str,
    app_version: str,
    fetch: Callable[[str], bytes] | None = None,
    max_workers: int = 4,
) -> UpdateCheckResult:
    """Fetch every feed and work out what has an update waiting.

    Safe to call from a worker thread — it touches nothing shared. With
    nothing installed it makes no request at all: a user who turned add-ons
    on and never installed anything should not generate traffic on every
    launch.
    """
    installed = list(installed)
    if not installed:
        return UpdateCheckResult(fetched=MultiFetchResult(results=[]))

    fetched = fetch_indexes(registries, fetch, max_workers=max_workers)
    targets = _feed_targets(installed)
    self_feeds = _fetch_self_feeds(targets, fetch, max_workers)
    listings = [*fetched.listings, *_self_feed_listings(self_feeds, targets)]
    return UpdateCheckResult(
        fetched=fetched,
        updates=pending_updates(
            installed, listings, sdk_version=sdk_version, app_version=app_version
        ),
        self_feeds=self_feeds,
    )
