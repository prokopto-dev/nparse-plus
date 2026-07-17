"""pigparse.org Discord login via a loopback redirect (Qt-free).

Port of EQTool's Services/DiscordAuthService.cs: bind an ephemeral loopback
port, open the pigparse login page (which sends the user through Discord
OAuth in their own browser), and catch the single redirect back to
``http://127.0.0.1:<port>/?username=…&discord_id=…&api_token=…``.

The user authenticates entirely in their browser; this process only
receives the resulting token. The token is a credential — callers must
never log it.
"""

from __future__ import annotations

import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

LOGIN_URL = "https://pigparse.azurewebsites.net/Account/Login?desktop_port={port}"

_SUCCESS_HTML = (
    "<html><body style='font-family:sans-serif;text-align:center;padding-top:60px'>"
    "<h2>Login successful!</h2><p>You can close this window and return to nParse+.</p>"
    "</body></html>"
)


@dataclass(frozen=True)
class DiscordAuthResult:
    username: str
    discord_id: str
    api_token: str

    @property
    def ok(self) -> bool:
        return bool(self.discord_id and self.api_token)


def login(
    open_browser: Callable[[str], object] = webbrowser.open,
    timeout_s: float = 300.0,
) -> DiscordAuthResult | None:
    """Run one loopback login round-trip; None on timeout.

    Blocks the calling thread (run it on the net worker, never the GUI)."""
    captured: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            query = parse_qs(urlparse(self.path).query)
            for key in ("username", "discord_id", "api_token"):
                captured[key] = (query.get(key) or [""])[0]
            body = _SUCCESS_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            """Silence request logging — the query string carries the token."""

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        server.timeout = timeout_s
        port = server.server_address[1]
        open_browser(LOGIN_URL.format(port=port))
        server.handle_request()  # exactly one request, or timeout

    if not captured:
        return None
    return DiscordAuthResult(
        username=captured.get("username", ""),
        discord_id=captured.get("discord_id", ""),
        api_token=captured.get("api_token", ""),
    )
