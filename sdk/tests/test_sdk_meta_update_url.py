"""``PluginMeta.update_url`` — the optional self-published update feed.

The field is deliberately strict: a malformed feed URL is a load error the
author sees in ``nparseplus-plugin validate``, not a feed that quietly never
produces an update.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nparseplus_sdk import PluginMeta


def meta(**overrides: object) -> PluginMeta:
    base: dict[str, object] = {"id": "demo", "name": "Demo"}
    base.update(overrides)
    return PluginMeta.model_validate(base)


def test_update_url_defaults_to_no_feed() -> None:
    assert meta().update_url == ""


@pytest.mark.parametrize(
    "good",
    [
        "https://example.test/plugins/index.json",
        "HTTPS://Example.Test/index.json",  # scheme compared case-insensitively
    ],
)
def test_https_feeds_accepted(good: str) -> None:
    assert meta(update_url=good).update_url == good


def test_surrounding_whitespace_is_stripped() -> None:
    assert meta(update_url="  https://example.test/i.json  ").update_url == (
        "https://example.test/i.json"
    )


@pytest.mark.parametrize(
    "bad",
    [
        "http://example.test/index.json",  # plaintext
        "ftp://example.test/index.json",
        "example.test/index.json",  # scheme-relative
        "file:///etc/passwd",
        "javascript:alert(1)",
    ],
)
def test_non_https_feeds_rejected(bad: str) -> None:
    with pytest.raises(ValidationError):
        meta(update_url=bad)


def test_whitespace_only_is_no_feed_not_an_error() -> None:
    assert meta(update_url="   ").update_url == ""
