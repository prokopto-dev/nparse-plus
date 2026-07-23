"""Registry index parsing, fetching, compatibility, and update detection."""

from __future__ import annotations

import json

import pytest

from nparseplus.core.plugins.registry import (
    REGISTRY_SCHEMA_VERSION,
    RegistryRelease,
    fetch_index,
    parse_index,
    release_compat,
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
