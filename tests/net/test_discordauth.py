"""discordauth — loopback login round-trip (DiscordAuthService port)."""

from __future__ import annotations

import threading
import urllib.request
from urllib.parse import urlparse

from nparseplus.net.discordauth import DiscordAuthResult, login


def _fake_browser_redirect(query: str):
    """A 'browser' that immediately performs the pigparse redirect."""

    def open_url(url: str) -> None:
        port = urlparse(url).query.split("desktop_port=")[1]

        def hit() -> None:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/?{query}", timeout=5) as resp:
                assert b"Login successful" in resp.read()

        threading.Thread(target=hit, daemon=True).start()

    return open_url


def test_login_round_trip_captures_credentials() -> None:
    result = login(
        open_browser=_fake_browser_redirect("username=Pig&discord_id=123456&api_token=tok-abc"),
        timeout_s=10.0,
    )
    assert result == DiscordAuthResult(username="Pig", discord_id="123456", api_token="tok-abc")
    assert result.ok


def test_login_missing_token_is_not_ok() -> None:
    result = login(open_browser=_fake_browser_redirect("username=Pig"), timeout_s=10.0)
    assert result is not None
    assert not result.ok


def test_login_timeout_returns_none() -> None:
    assert login(open_browser=lambda _url: None, timeout_s=0.2) is None
