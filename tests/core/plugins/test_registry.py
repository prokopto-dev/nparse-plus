"""Registry index parsing, fetching, compatibility, and update detection."""

from __future__ import annotations

import json
import threading

import httpx
import pytest

from nparseplus.config.settings import PluginsSettings, RegistrySource
from nparseplus.core.plugins import install as install_module
from nparseplus.core.plugins import registry as registry_module
from nparseplus.core.plugins.registry import (
    DEFAULT_REGISTRY_URL,
    MAX_INDEX_BYTES,
    REGISTRY_SCHEMA_VERSION,
    MergedListing,
    RegistryPlugin,
    RegistryRelease,
    ResolvedRegistry,
    best_update,
    duplicate_listing_ids,
    fetch_index,
    fetch_indexes,
    parse_index,
    release_compat,
    resolve_registries,
    update_available,
)

GOOD_SHA = "a" * 64

GOOD_INDEX = {
    "schema_version": 1,
    "plugins": [
        {
            "id": "merchant-prices",
            "name": "Merchant Prices",
            "author": "someone",
            "description": "WTS price tracking",
            "homepage": "https://github.com/someone/plug",
            "latest": {
                "version": "1.2.0",
                "url": "https://github.com/someone/plug/releases/download/v1.2.0/p.zip",
                "sha256": GOOD_SHA,
                "requires_sdk": ">=1.0,<2",
                "min_app_version": "1.15.0",
            },
        }
    ],
}


def release(**overrides: object) -> RegistryRelease:
    base: dict[str, object] = {
        "version": "1.0.0",
        "url": "https://example.com/p.zip",
        "sha256": GOOD_SHA,
    }
    base.update(overrides)
    return RegistryRelease.model_validate(base)


class TestParseIndex:
    def test_good_index(self) -> None:
        index = parse_index(json.dumps(GOOD_INDEX))
        assert len(index.plugins) == 1
        plugin = index.plugins[0]
        assert plugin.id == "merchant-prices"
        assert plugin.latest.version == "1.2.0"
        assert plugin.latest.sha256 == GOOD_SHA

    def test_not_json(self) -> None:
        with pytest.raises(ValueError, match="not valid JSON"):
            parse_index(b"<html>404</html>")

    def test_malformed_shape(self) -> None:
        with pytest.raises(ValueError, match="malformed"):
            parse_index(json.dumps({"schema_version": 1, "plugins": [{"id": "x"}]}))

    def test_newer_schema_rejected(self) -> None:
        payload = {"schema_version": REGISTRY_SCHEMA_VERSION + 1, "plugins": []}
        with pytest.raises(ValueError, match="update nParse"):
            parse_index(json.dumps(payload))

    def test_http_release_url_rejected(self) -> None:
        bad = json.loads(json.dumps(GOOD_INDEX))
        bad["plugins"][0]["latest"]["url"] = "http://example.com/p.zip"
        with pytest.raises(ValueError, match="malformed"):
            parse_index(json.dumps(bad))

    def test_bad_sha256_rejected(self) -> None:
        bad = json.loads(json.dumps(GOOD_INDEX))
        bad["plugins"][0]["latest"]["sha256"] = "nothex"
        with pytest.raises(ValueError, match="malformed"):
            parse_index(json.dumps(bad))

    def test_uppercase_sha256_normalized(self) -> None:
        upper = json.loads(json.dumps(GOOD_INDEX))
        upper["plugins"][0]["latest"]["sha256"] = GOOD_SHA.upper()
        index = parse_index(json.dumps(upper))
        assert index.plugins[0].latest.sha256 == GOOD_SHA


class TestReleaseNotes:
    """The additive plain-text notes field (#147, regserve ADR-0013).

    Shipped ahead of the server surfacing it on ``latest``, which is why
    both spellings load: the index document calls it ``release_notes`` and
    the publish request calls it ``notes``.
    """

    def test_absent_notes_are_empty_not_missing(self) -> None:
        """Every listing published so far has none, and none of them break."""
        index = parse_index(json.dumps(GOOD_INDEX))
        assert index.plugins[0].latest.notes == ""

    @pytest.mark.parametrize("key", ["release_notes", "notes"])
    def test_either_spelling_populates_the_field(self, key: str) -> None:
        payload = json.loads(json.dumps(GOOD_INDEX))
        payload["plugins"][0]["latest"][key] = "Fixed the price cache."
        assert parse_index(json.dumps(payload)).plugins[0].latest.notes == "Fixed the price cache."

    def test_notes_are_carried_verbatim(self) -> None:
        """Not markup, and not sanitised either: the client is not a renderer.

        The registry's promise is that the field is *not* markup, so nothing
        here strips or escapes anything — that is the callers' problem, and
        they solve it by showing text in a plain-text widget.
        """
        raw = "Fixed **everything**\n<b>really</b>"
        assert release(notes=raw).notes == raw

    def test_the_field_can_still_be_set_by_its_python_name(self) -> None:
        """populate_by_name: an alias must not break construction in code."""
        assert (
            RegistryRelease(
                version="1.0.0", url="https://example.com/p.zip", sha256=GOOD_SHA, notes="hi"
            ).notes
            == "hi"
        )


class TestFetchIndex:
    def test_https_only(self) -> None:
        with pytest.raises(ValueError, match="https"):
            fetch_index("http://example.com/index.json", fetch=lambda url: b"{}")

    def test_injected_fetch(self) -> None:
        index = fetch_index(
            "https://example.com/index.json",
            fetch=lambda url: json.dumps(GOOD_INDEX).encode(),
        )
        assert index.plugins[0].id == "merchant-prices"

    def test_transport_failure_wrapped(self) -> None:
        def boom(url: str) -> bytes:
            raise OSError("connection refused")

        with pytest.raises(ValueError, match="could not reach"):
            fetch_index("https://example.com/index.json", fetch=boom)


class TestDefaultIndexTransport:
    """The built-in fetch: https on every hop, streamed under a byte budget.

    The index is the security boundary (it supplies both the artifact URL
    and the hash it is checked against), so a downgrade here is worse than
    anywhere else in the installer.
    """

    @staticmethod
    def _patch(monkeypatch, handler) -> None:
        real = install_module.fetch_https_bytes
        monkeypatch.setattr(
            install_module,
            "fetch_https_bytes",
            lambda url, **kwargs: real(
                url, **{**kwargs, "transport": httpx.MockTransport(handler)}
            ),
        )

    def test_redirect_to_http_refused(self, monkeypatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.scheme == "https":
                return httpx.Response(302, headers={"location": "http://evil.example/index.json"})
            return httpx.Response(200, content=json.dumps(GOOD_INDEX).encode())

        self._patch(monkeypatch, handler)
        with pytest.raises(ValueError, match="non-https"):
            fetch_index("https://example.com/index.json")

    def test_https_redirect_followed(self, monkeypatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/index.json":
                return httpx.Response(302, headers={"location": "https://cdn.example.com/i.json"})
            return httpx.Response(200, content=json.dumps(GOOD_INDEX).encode())

        self._patch(monkeypatch, handler)
        assert fetch_index("https://example.com/index.json").plugins[0].id == "merchant-prices"

    def test_oversize_index_refused(self, monkeypatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"{" + b" " * (MAX_INDEX_BYTES + 1))

        self._patch(monkeypatch, handler)
        with pytest.raises(ValueError, match="byte limit"):
            fetch_index("https://example.com/index.json")


class TestReleaseCompat:
    def test_compatible(self) -> None:
        assert release_compat(release(), sdk_version="1.0.0", app_version="1.15.0") is None

    def test_sdk_range_refused(self) -> None:
        reason = release_compat(
            release(requires_sdk=">=2.0"), sdk_version="1.0.0", app_version="1.15.0"
        )
        assert reason is not None and ">=2.0" in reason

    def test_min_app_refused(self) -> None:
        reason = release_compat(
            release(min_app_version="99.0.0"), sdk_version="1.0.0", app_version="1.15.0"
        )
        assert reason is not None and "99.0.0" in reason

    def test_bad_specifier_is_reason_not_crash(self) -> None:
        reason = release_compat(
            release(requires_sdk="!!bad!!"), sdk_version="1.0.0", app_version="1.15.0"
        )
        assert reason is not None


class TestUpdateAvailable:
    def test_newer(self) -> None:
        assert update_available("1.0.0", release(version="1.1.0")) is True

    def test_equal_and_older(self) -> None:
        assert update_available("1.1.0", release(version="1.1.0")) is False
        assert update_available("1.2.0", release(version="1.1.0")) is False

    def test_garbage_versions_false(self) -> None:
        assert update_available("not-a-version", release(version="1.1.0")) is False
        assert update_available("1.0.0", release(version="???")) is False


def _resolved(url: str, *, name: str = "", enabled: bool = True, default: bool = False):
    return ResolvedRegistry(url=url, name=name or url, enabled=enabled, is_default=default)


def _index_bytes(*ids_and_versions: tuple[str, str]) -> bytes:
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
                        "sha256": GOOD_SHA,
                    },
                }
                for plugin_id, version in ids_and_versions
            ],
        }
    ).encode()


class TestResolveRegistries:
    def test_default_comes_first_and_is_not_persisted(self) -> None:
        plugins = PluginsSettings(registries=[RegistrySource(url="https://a.example/i.json")])
        resolved = resolve_registries(plugins)
        assert [r.url for r in resolved] == [DEFAULT_REGISTRY_URL, "https://a.example/i.json"]
        assert resolved[0].is_default is True
        assert plugins.registries[0].url == "https://a.example/i.json"  # default absent

    def test_default_row_follows_the_constant(self, monkeypatch) -> None:
        # The anti-stranding guard: because the default is synthesized rather
        # than stored, changing the constant must move every existing user.
        monkeypatch.setattr(registry_module, "DEFAULT_REGISTRY_URL", "https://moved.example/i.json")
        assert resolve_registries(PluginsSettings())[0].url == "https://moved.example/i.json"

    def test_disabled_default_is_reported_not_removed(self) -> None:
        resolved = resolve_registries(PluginsSettings(default_registry_enabled=False))
        assert resolved[0].is_default is True
        assert resolved[0].enabled is False

    def test_a_user_copy_of_the_default_collapses(self) -> None:
        plugins = PluginsSettings(registries=[RegistrySource(url=DEFAULT_REGISTRY_URL)])
        assert [r.url for r in resolve_registries(plugins)] == [DEFAULT_REGISTRY_URL]

    def test_name_falls_back_to_the_host(self) -> None:
        plugins = PluginsSettings(registries=[RegistrySource(url="https://a.example/deep/i.json")])
        assert resolve_registries(plugins)[1].name == "a.example"


class TestFetchIndexes:
    def test_merges_in_registry_order(self) -> None:
        payloads = {
            "https://a.example/i.json": _index_bytes(("alpha", "1.0.0")),
            "https://b.example/i.json": _index_bytes(("beta", "2.0.0")),
        }
        result = fetch_indexes(
            [_resolved("https://a.example/i.json"), _resolved("https://b.example/i.json")],
            fetch=payloads.__getitem__,
        )
        assert [listing.plugin.id for listing in result.listings] == ["alpha", "beta"]
        assert result.listings[0].registry.url == "https://a.example/i.json"
        assert result.failures == []

    def test_one_failure_does_not_sink_the_others(self) -> None:
        def fetch(url: str) -> bytes:
            if "bad" in url:
                raise OSError("connection refused")
            return _index_bytes(("alpha", "1.0.0"))

        result = fetch_indexes(
            [
                _resolved("https://bad.example/i.json", name="Bad"),
                _resolved("https://a.example/i.json"),
            ],
            fetch=fetch,
        )
        assert [listing.plugin.id for listing in result.listings] == ["alpha"]
        assert len(result.failures) == 1
        summary = "\n".join(result.summary_lines())
        assert "Could not reach 1 of 2" in summary and "Bad" in summary

    def test_a_newer_schema_version_fails_alone(self) -> None:
        def fetch(url: str) -> bytes:
            if "future" in url:
                return json.dumps({"schema_version": 99, "plugins": []}).encode()
            return _index_bytes(("alpha", "1.0.0"))

        result = fetch_indexes(
            [_resolved("https://future.example/i.json"), _resolved("https://a.example/i.json")],
            fetch=fetch,
        )
        assert "update nParse+" in (result.failures[0].error or "")
        assert [listing.plugin.id for listing in result.listings] == ["alpha"]

    def test_all_failed(self) -> None:
        def boom(url: str) -> bytes:
            raise OSError("nope")

        result = fetch_indexes([_resolved("https://a.example/i.json")], fetch=boom)
        assert result.listings == []
        assert len(result.failures) == 1

    def test_no_registries_says_so(self) -> None:
        result = fetch_indexes([])
        assert result.results == []
        assert "No plugin registries are enabled" in result.summary_lines()[0]

    def test_a_worker_raising_anything_still_lands_as_an_error(self) -> None:
        def boom(url: str) -> bytes:
            raise RuntimeError("not a ValueError")

        result = fetch_indexes([_resolved("https://a.example/i.json")], fetch=boom)
        assert result.failures and "not a ValueError" in (result.failures[0].error or "")

    def test_fetches_run_concurrently(self) -> None:
        # A barrier, not a sleep: a serial implementation deadlocks it out.
        barrier = threading.Barrier(2, timeout=5)

        def fetch(url: str) -> bytes:
            barrier.wait()
            return _index_bytes(("alpha", "1.0.0"))

        result = fetch_indexes(
            [_resolved("https://a.example/i.json"), _resolved("https://b.example/i.json")],
            fetch=fetch,
        )
        assert result.failures == []

    def test_max_workers_one_runs_inline(self) -> None:
        threads: list[str] = []

        def fetch(url: str) -> bytes:
            threads.append(threading.current_thread().name)
            return _index_bytes(("alpha", "1.0.0"))

        fetch_indexes(
            [_resolved("https://a.example/i.json"), _resolved("https://b.example/i.json")],
            fetch=fetch,
            max_workers=1,
        )
        assert set(threads) == {threading.current_thread().name}

    def test_still_routes_through_fetch_https_bytes(self, monkeypatch) -> None:
        # Guards the seam that covers redirect/size hardening: if anyone
        # reimplements transport inside fetch_indexes, this goes red instead
        # of the coverage silently lapsing.
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "redirects.example":
                return httpx.Response(302, headers={"Location": "http://insecure.example/i.json"})
            return httpx.Response(200, content=_index_bytes(("alpha", "1.0.0")))

        TestDefaultIndexTransport._patch(monkeypatch, handler)
        result = fetch_indexes(
            [_resolved("https://redirects.example/i.json"), _resolved("https://a.example/i.json")]
        )
        assert "non-https" in (result.failures[0].error or "")
        assert [listing.plugin.id for listing in result.listings] == ["alpha"]


class TestDuplicateListingIds:
    def test_only_ids_from_two_registries_count(self) -> None:
        payloads = {
            "https://a.example/i.json": _index_bytes(("shared", "1.0.0"), ("only-a", "1.0.0")),
            "https://b.example/i.json": _index_bytes(("shared", "2.0.0")),
        }
        result = fetch_indexes(
            [_resolved("https://a.example/i.json"), _resolved("https://b.example/i.json")],
            fetch=payloads.__getitem__,
        )
        assert duplicate_listing_ids(result.listings) == {"shared"}


class TestBestUpdate:
    @staticmethod
    def _listings(*specs: tuple[str, str, str]):
        return [
            MergedListing(
                registry=_resolved(url),
                plugin=RegistryPlugin(
                    id=plugin_id,
                    name=plugin_id,
                    latest=RegistryRelease(
                        version=version, url="https://example.com/p.zip", sha256=GOOD_SHA
                    ),
                ),
            )
            for url, plugin_id, version in specs
        ]

    def _best(self, listings, *, installed="1.0.0", from_url=""):
        return best_update(
            listings,
            plugin_id="demo",
            installed_version=installed,
            installed_registry_url=from_url,
            sdk_version="1.0.0",
            app_version="1.18.0",
        )

    def test_none_when_nothing_is_newer(self) -> None:
        assert self._best(self._listings(("https://a/i", "demo", "1.0.0"))) is None

    def test_picks_the_highest_across_registries(self) -> None:
        listings = self._listings(
            ("https://a/i", "demo", "1.1.0"), ("https://b/i", "demo", "1.4.0")
        )
        best = self._best(listings)
        assert best is not None and best.plugin.latest.version == "1.4.0"

    def test_prefers_the_registry_it_was_installed_from(self) -> None:
        # Even though B is newer: promoting another registry's build of the
        # same id is a trust hop the user never agreed to.
        listings = self._listings(
            ("https://a/i", "demo", "1.3.0"), ("https://b/i", "demo", "1.4.0")
        )
        best = self._best(listings, from_url="https://a/i")
        assert best is not None and best.registry.url == "https://a/i"
        assert best.plugin.latest.version == "1.3.0"

    def test_skips_incompatible_releases(self) -> None:
        listings = [
            MergedListing(
                registry=_resolved("https://a/i"),
                plugin=RegistryPlugin(
                    id="demo",
                    name="demo",
                    latest=RegistryRelease(
                        version="9.9.9",
                        url="https://example.com/p.zip",
                        sha256=GOOD_SHA,
                        requires_sdk=">=99",
                    ),
                ),
            )
        ]
        assert self._best(listings) is None
