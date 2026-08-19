"""Which installed plugins have an update, and whether taking it is silent."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nparseplus.config.settings import _LEGACY_DEFAULT_REGISTRY_URL, load_settings
from nparseplus.core.plugins.registry import (
    DEFAULT_REGISTRY_URL,
    MergedListing,
    RegistryPlugin,
    RegistryRelease,
    ResolvedRegistry,
)
from nparseplus.core.plugins.updatecheck import (
    InstalledPlugin,
    check_for_updates,
    listing_action,
    pending_updates,
    same_source_updates,
    self_feed_registry,
    updates_by_id,
)

SHA = "a" * 64
SDK = "1.0.0"
APP = "1.20.0"

DEFAULT_URL = "https://registry.example/index.json"
GUILD_URL = "https://guild.example/index.json"
FEED_URL = "https://you.example/demo/index.json"


def registry(url: str, name: str = "", *, kind: str = "user") -> ResolvedRegistry:
    return ResolvedRegistry(
        url=url,
        name=name or url,
        enabled=True,
        is_default=kind == "default",
        kind=kind,  # type: ignore[arg-type]
    )


def listing(
    url: str,
    version: str,
    *,
    plugin_id: str = "demo",
    requires_sdk: str = ">=1.0,<2",
    min_app_version: str | None = None,
    registry_kind: str = "user",
) -> MergedListing:
    return MergedListing(
        registry=registry(url, kind=registry_kind),
        plugin=RegistryPlugin(
            id=plugin_id,
            name=plugin_id,
            latest=RegistryRelease(
                version=version,
                url=f"https://example.com/{plugin_id}-{version}.zip",
                sha256=SHA,
                requires_sdk=requires_sdk,
                min_app_version=min_app_version,
            ),
        ),
    )


def installed(**overrides) -> InstalledPlugin:
    base: dict = {"plugin_id": "demo", "version": "1.0.0"}
    base.update(overrides)
    return InstalledPlugin(**base)


def updates_for(installed_plugins, listings) -> list:
    return pending_updates(installed_plugins, listings, sdk_version=SDK, app_version=APP)


def index_bytes(*specs: tuple[str, str], **release_overrides) -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "plugins": [
                {
                    "id": plugin_id,
                    "name": plugin_id,
                    "latest": {
                        "version": version,
                        "url": f"https://example.com/{plugin_id}.zip",
                        "sha256": SHA,
                        **release_overrides,
                    },
                }
                for plugin_id, version in specs
            ],
        }
    ).encode()


class TestPendingUpdates:
    def test_offers_a_newer_compatible_release(self) -> None:
        updates = updates_for(
            [installed(registry_url=DEFAULT_URL)], [listing(DEFAULT_URL, "2.0.0")]
        )
        assert len(updates) == 1
        assert updates[0].offered_version == "2.0.0"
        assert updates[0].installed_version == "1.0.0"
        assert updates[0].same_source is True
        assert updates[0].needs_confirmation is False

    def test_nothing_when_the_listing_is_not_newer(self) -> None:
        assert (
            updates_for([installed(registry_url=DEFAULT_URL)], [listing(DEFAULT_URL, "1.0.0")])
            == []
        )

    def test_skips_a_release_the_app_would_refuse(self) -> None:
        # Offering an update the loader then rejects is worse than offering
        # none — best_update filters it, and nothing here re-adds it.
        assert (
            updates_for(
                [installed(registry_url=DEFAULT_URL)],
                [listing(DEFAULT_URL, "2.0.0", min_app_version="99.0.0")],
            )
            == []
        )

    def test_prefers_the_registry_the_plugin_came_from(self) -> None:
        updates = updates_for(
            [installed(registry_url=DEFAULT_URL)],
            [listing(DEFAULT_URL, "2.0.0"), listing(GUILD_URL, "3.0.0")],
        )
        assert updates[0].offered_version == "2.0.0"
        assert updates[0].same_source is True

    def test_an_offer_from_elsewhere_needs_confirmation(self) -> None:
        updates = updates_for([installed(registry_url=DEFAULT_URL)], [listing(GUILD_URL, "2.0.0")])
        assert updates[0].needs_confirmation is True
        assert updates[0].unknown_provenance is False

    def test_an_offer_for_an_unvouched_copy_needs_confirmation(self) -> None:
        # Sideloaded, and a registry now claims the id. That is exactly the
        # ambiguity worth stopping on.
        updates = updates_for([installed()], [listing(DEFAULT_URL, "2.0.0")])
        assert updates[0].needs_confirmation is True
        assert updates[0].unknown_provenance is True

    def test_a_plugin_with_no_version_is_skipped(self) -> None:
        assert updates_for([installed(version="")], [listing(DEFAULT_URL, "2.0.0")]) == []

    def test_carries_the_installed_path_through(self, tmp_path: Path) -> None:
        where = tmp_path / "demo.py"
        updates = updates_for(
            [installed(registry_url=DEFAULT_URL, installed_path=where)],
            [listing(DEFAULT_URL, "2.0.0")],
        )
        assert updates[0].installed_path == where


class TestSelfFeeds:
    def test_a_feed_updates_a_sideloaded_plugin_silently(self) -> None:
        # The whole point of update_url: no registry ever vouched, so the
        # author's own feed is the only source this copy has ever had.
        updates = updates_for(
            [installed(update_url=FEED_URL)],
            [listing(FEED_URL, "2.0.0", registry_kind="self")],
        )
        assert updates[0].same_source is True
        assert updates[0].needs_confirmation is False
        assert updates[0].unknown_provenance is True

    def test_a_feed_loses_to_the_registry_that_vouched(self) -> None:
        updates = updates_for(
            [installed(registry_url=DEFAULT_URL, update_url=FEED_URL)],
            [listing(DEFAULT_URL, "2.0.0"), listing(FEED_URL, "3.0.0", registry_kind="self")],
        )
        assert updates[0].offered_version == "2.0.0"
        assert updates[0].same_source is True

    def test_a_feed_offer_for_a_registry_install_needs_confirmation(self) -> None:
        updates = updates_for(
            [installed(registry_url=DEFAULT_URL, update_url=FEED_URL)],
            [listing(FEED_URL, "3.0.0", registry_kind="self")],
        )
        assert updates[0].needs_confirmation is True

    def test_a_feed_beats_a_registry_for_an_unvouched_copy(self) -> None:
        # Without the preference, a registry listing a higher version under
        # this id would outrank the author's own release and make every
        # self-hosted update a confirmation.
        updates = updates_for(
            [installed(update_url=FEED_URL)],
            [listing(DEFAULT_URL, "9.0.0"), listing(FEED_URL, "2.0.0", registry_kind="self")],
        )
        assert updates[0].offered_version == "2.0.0"
        assert updates[0].needs_confirmation is False

    def test_the_feed_registry_is_marked_self_published(self) -> None:
        resolved = self_feed_registry("demo", FEED_URL)
        assert resolved.is_self_published is True
        assert resolved.is_default is False
        assert "self-published" in resolved.name
        assert "you.example" in resolved.name


class TestCheckForUpdates:
    def test_makes_no_request_with_nothing_installed(self) -> None:
        calls: list[str] = []

        def fetch(url: str) -> bytes:
            calls.append(url)
            return index_bytes(("demo", "2.0.0"))

        result = check_for_updates(
            [], [registry(DEFAULT_URL)], sdk_version=SDK, app_version=APP, fetch=fetch
        )
        assert calls == []
        assert result.updates == []

    def test_fetches_registries_and_reports_updates(self) -> None:
        payloads = {DEFAULT_URL: index_bytes(("demo", "2.0.0"))}
        result = check_for_updates(
            [installed(registry_url=DEFAULT_URL)],
            [registry(DEFAULT_URL)],
            sdk_version=SDK,
            app_version=APP,
            fetch=payloads.__getitem__,
        )
        assert [u.offered_version for u in result.updates] == ["2.0.0"]
        assert result.summary_lines() == []

    def test_a_feed_cannot_offer_another_plugins_id(self) -> None:
        # The impersonation guard. Without it, one installed add-on could
        # publish "updates" for every other plugin on the machine.
        payloads = {FEED_URL: index_bytes(("demo", "2.0.0"), ("victim", "99.0.0"))}
        result = check_for_updates(
            [
                installed(update_url=FEED_URL),
                installed(plugin_id="victim", version="1.0.0"),
            ],
            [],
            sdk_version=SDK,
            app_version=APP,
            fetch=payloads.__getitem__,
        )
        assert {u.plugin_id for u in result.updates} == {"demo"}

    def test_a_non_https_feed_is_never_fetched(self) -> None:
        calls: list[str] = []

        def fetch(url: str) -> bytes:
            calls.append(url)
            raise AssertionError("should not be reached")

        result = check_for_updates(
            [installed(update_url="http://you.example/i.json")],
            [],
            sdk_version=SDK,
            app_version=APP,
            fetch=fetch,
        )
        assert calls == []
        assert result.updates == []

    def test_a_dead_feed_does_not_lose_the_registries(self) -> None:
        def fetch(url: str) -> bytes:
            if url == FEED_URL:
                raise OSError("feed is down")
            return index_bytes(("demo", "2.0.0"))

        result = check_for_updates(
            [installed(registry_url=DEFAULT_URL, update_url=FEED_URL)],
            [registry(DEFAULT_URL)],
            sdk_version=SDK,
            app_version=APP,
            fetch=fetch,
        )
        assert [u.offered_version for u in result.updates] == ["2.0.0"]
        assert any("plugin update feed" in line for line in result.summary_lines())

    def test_a_dead_registry_does_not_lose_the_feed(self) -> None:
        def fetch(url: str) -> bytes:
            if url == DEFAULT_URL:
                raise OSError("registry is down")
            return index_bytes(("demo", "2.0.0"))

        result = check_for_updates(
            [installed(update_url=FEED_URL)],
            [registry(DEFAULT_URL)],
            sdk_version=SDK,
            app_version=APP,
            fetch=fetch,
        )
        assert [u.offered_version for u in result.updates] == ["2.0.0"]
        assert any("Could not reach" in line for line in result.summary_lines())

    def test_one_feed_url_is_fetched_once(self) -> None:
        calls: list[str] = []

        def fetch(url: str) -> bytes:
            calls.append(url)
            return index_bytes(("demo", "2.0.0"))

        check_for_updates(
            [
                installed(update_url=FEED_URL),
                installed(plugin_id="other", version="1.0.0", update_url=FEED_URL),
            ],
            [],
            sdk_version=SDK,
            app_version=APP,
            fetch=fetch,
        )
        assert calls == [FEED_URL]

    def test_self_feed_listings_never_enter_the_browsable_set(self) -> None:
        payloads = {FEED_URL: index_bytes(("demo", "2.0.0"))}
        result = check_for_updates(
            [installed(update_url=FEED_URL)],
            [],
            sdk_version=SDK,
            app_version=APP,
            fetch=payloads.__getitem__,
        )
        assert result.updates  # the offer exists...
        assert result.listings == []  # ...but Browse must not show it


class TestSameSourceAndIndex:
    def test_same_source_updates_excludes_confirmations(self) -> None:
        updates = updates_for(
            [
                installed(registry_url=DEFAULT_URL),
                installed(plugin_id="other", version="1.0.0", registry_url=DEFAULT_URL),
            ],
            [listing(DEFAULT_URL, "2.0.0"), listing(GUILD_URL, "2.0.0", plugin_id="other")],
        )
        assert {u.plugin_id for u in same_source_updates(updates)} == {"demo"}

    def test_updates_by_id_keeps_the_first(self) -> None:
        updates = updates_for(
            [installed(registry_url=DEFAULT_URL)], [listing(DEFAULT_URL, "2.0.0")]
        )
        assert set(updates_by_id(updates)) == {"demo"}


class TestListingAction:
    def _action(self, merged, **overrides):
        base = {
            "installed_version": "",
            "installed_registry_url": "",
            "is_installed": False,
            "sdk_version": SDK,
            "app_version": APP,
        }
        base.update(overrides)
        return listing_action(merged, **base)

    def test_not_installed_and_compatible_offers_install(self) -> None:
        action = self._action(listing(DEFAULT_URL, "1.0.0"))
        assert (action.kind, action.label, action.enabled) == ("install", "Install", True)

    def test_not_installed_and_incompatible_is_disabled(self) -> None:
        action = self._action(listing(DEFAULT_URL, "1.0.0", min_app_version="99.0.0"))
        assert action.kind == "incompatible"
        assert action.enabled is False
        assert action.tooltip

    def test_installed_at_the_same_version_says_installed(self) -> None:
        action = self._action(
            listing(DEFAULT_URL, "1.0.0"),
            is_installed=True,
            installed_version="1.0.0",
            installed_registry_url=DEFAULT_URL,
        )
        assert (action.kind, action.label, action.enabled) == ("installed", "Installed", False)

    def test_installed_and_newer_from_the_same_source_offers_update(self) -> None:
        action = self._action(
            listing(DEFAULT_URL, "2.0.0"),
            is_installed=True,
            installed_version="1.0.0",
            installed_registry_url=DEFAULT_URL,
        )
        assert action.kind == "update"
        assert action.label == "Update to v2.0.0"
        assert action.enabled is True

    def test_installed_and_newer_from_elsewhere_is_flagged_not_plain(self) -> None:
        action = self._action(
            listing(GUILD_URL, "2.0.0"),
            is_installed=True,
            installed_version="1.0.0",
            installed_registry_url=DEFAULT_URL,
        )
        assert action.kind == "update_other_source"
        assert action.label.endswith("…")  # the ellipsis promises a dialog
        assert action.enabled is True
        assert "not where your copy came from" in action.tooltip

    def test_installed_from_elsewhere_at_the_same_version_stays_disabled(self) -> None:
        action = self._action(
            listing(GUILD_URL, "1.0.0"),
            is_installed=True,
            installed_version="1.0.0",
            installed_registry_url=DEFAULT_URL,
        )
        assert action.kind == "installed_other_source"
        assert action.enabled is False

    def test_installed_and_newer_but_incompatible_is_disabled(self) -> None:
        action = self._action(
            listing(DEFAULT_URL, "2.0.0", min_app_version="99.0.0"),
            is_installed=True,
            installed_version="1.0.0",
            installed_registry_url=DEFAULT_URL,
        )
        assert action.kind == "incompatible"
        assert action.enabled is False

    @pytest.mark.parametrize("version", ["2.0.0", "1.0.0"])
    def test_an_unvouched_install_never_reports_another_source(self, version: str) -> None:
        # No recorded registry means there is no "other" to compare against;
        # the row must not invent a source conflict.
        action = self._action(
            listing(GUILD_URL, version),
            is_installed=True,
            installed_version="1.0.0",
            installed_registry_url="",
        )
        assert action.kind in {"update", "installed"}


class TestTheBuiltInRegistryMoved:
    """#130 end to end: an install made before the move is not a source hop.

    The provenance rewrite lives in ``PluginsSettings`` and everything that
    reads it lives here, so the acceptance criterion — "an existing install
    shows the built-in registry as its Source and its next update is a plain
    Update" — is only really asserted by running both halves together.
    """

    @staticmethod
    def _installed_from(settings_file: Path, recorded_url: str) -> InstalledPlugin:
        settings_file.write_text(
            json.dumps(
                {
                    "plugins": {
                        "entries": {
                            "demo": {
                                "approved": True,
                                "last_version": "1.0.0",
                                "registry_url": recorded_url,
                                "sha256": SHA,
                            }
                        }
                    }
                }
            )
        )
        entry = load_settings(settings_file).plugins.entries["demo"]
        return InstalledPlugin(plugin_id="demo", version="1.0.0", registry_url=entry.registry_url)

    def test_an_install_from_the_old_default_takes_a_plain_update(self, tmp_path: Path) -> None:
        installed = self._installed_from(tmp_path / "settings.json", _LEGACY_DEFAULT_REGISTRY_URL)
        offer = listing(DEFAULT_REGISTRY_URL, "2.0.0", registry_kind="default")

        updates = pending_updates([installed], [offer], sdk_version=SDK, app_version=APP)
        assert len(updates) == 1
        assert updates[0].same_source is True
        assert updates[0].needs_confirmation is False
        assert same_source_updates(updates) == updates  # "Update all" takes it

        action = listing_action(
            offer,
            installed_version="1.0.0",
            installed_registry_url=installed.registry_url,
            is_installed=True,
            sdk_version=SDK,
            app_version=APP,
        )
        assert action.kind == "update"
        assert action.enabled is True

    def test_a_listed_old_url_still_asks(self, tmp_path: Path) -> None:
        """Listing that URL is what separates this from the case above.

        A registry in the list is a trust decision the user made and the
        record naming it is true, so nothing is repointed and the built-in
        registry's offer is a genuine source change.
        """
        path = tmp_path / "settings.json"
        path.write_text(
            json.dumps(
                {
                    "plugins": {
                        "registries": [{"url": _LEGACY_DEFAULT_REGISTRY_URL}],
                        "entries": {
                            "demo": {"registry_url": _LEGACY_DEFAULT_REGISTRY_URL},
                        },
                    }
                }
            )
        )
        entry = load_settings(path).plugins.entries["demo"]
        assert entry.registry_url == _LEGACY_DEFAULT_REGISTRY_URL

        action = listing_action(
            listing(DEFAULT_REGISTRY_URL, "2.0.0", registry_kind="default"),
            installed_version="1.0.0",
            installed_registry_url=entry.registry_url,
            is_installed=True,
            sdk_version=SDK,
            app_version=APP,
        )
        assert action.kind == "update_other_source"
