"""Self-updater — GitHub releases check + per-platform install flow.

No in-place binary swap in 1.0 (EQTool's two-phase ping/pong updater is not
ported): the check compares the latest GitHub release tag against
``nparseplus.__version__`` with ``packaging.version``; "install" downloads
the platform artifact to ~/Downloads and opens it (macOS DMG), or falls
back to opening the release page in a browser.

Qt-free; the tray layer marshals results to the GUI thread itself. Every
failure — including the repo not existing yet — degrades to "no update".
"""

from __future__ import annotations

import logging
import subprocess
import sys
import webbrowser
from collections.abc import Callable
from pathlib import Path

import httpx
from packaging.version import Version
from pydantic import BaseModel, ConfigDict

import nparseplus

logger = logging.getLogger(__name__)

GITHUB_OWNER = "prokopto-dev"
GITHUB_REPO = "nparse-plus"
TIMEOUT_S = 10.0


def releases_api_url() -> str:
    return f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"


def releases_page_url() -> str:
    return f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases"


class ReleaseAsset(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    name: str
    browser_download_url: str
    size: int = 0


class ReleaseInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    version: str  # normalized, no leading "v"
    html_url: str
    assets: tuple[ReleaseAsset, ...] = ()


def _client(client: httpx.Client | None) -> httpx.Client:
    return client or httpx.Client(
        timeout=TIMEOUT_S,
        follow_redirects=True,
        # GitHub's API requires a User-Agent.
        headers={"User-Agent": f"nparseplus/{nparseplus.__version__}"},
    )


def check_for_update(
    current: str | None = None, client: httpx.Client | None = None
) -> ReleaseInfo | None:
    """The latest release if it is newer than ``current``; else/on error None."""
    try:
        resp = _client(client).get(releases_api_url())
        resp.raise_for_status()
        payload = resp.json()
        tag = str(payload.get("tag_name", "")).lstrip("v")
        latest = Version(tag)
        installed = Version(current or nparseplus.__version__)
    except Exception:  # includes InvalidVersion on junk tags
        logger.debug("update check failed", exc_info=True)
        return None
    if latest <= installed:
        return None
    return ReleaseInfo(
        version=str(latest),
        html_url=str(payload.get("html_url", releases_page_url())),
        assets=tuple(
            ReleaseAsset.model_validate(a) for a in payload.get("assets", []) if isinstance(a, dict)
        ),
    )


def pick_asset(release: ReleaseInfo, platform: str = sys.platform) -> ReleaseAsset | None:
    """The artifact for this platform (macOS .dmg, Windows .zip, else None)."""
    suffix = {"darwin": ".dmg", "win32": ".zip"}.get(platform)
    if suffix is None:
        return None
    return next((a for a in release.assets if a.name.lower().endswith(suffix)), None)


def download_asset(
    asset: ReleaseAsset, dest_dir: Path, client: httpx.Client | None = None
) -> Path | None:
    """Stream the artifact into ``dest_dir``; None on any failure."""
    destination = Path(dest_dir) / asset.name
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with _client(client).stream("GET", asset.browser_download_url) as resp:
            resp.raise_for_status()
            with open(destination, "wb") as fh:
                for chunk in resp.iter_bytes():
                    fh.write(chunk)
    except Exception:
        logger.warning("update download failed for %s", asset.name, exc_info=True)
        return None
    return destination


def install_action(
    release: ReleaseInfo,
    platform: str = sys.platform,
    open_path: Callable[[Path], None] | None = None,
    open_url: Callable[[str], None] = webbrowser.open,
    downloads_dir: Path | None = None,
) -> None:
    """User-initiated 'install': download + open, or open the release page."""
    asset = pick_asset(release, platform)
    if asset is None:
        open_url(release.html_url)
        return
    downloaded = download_asset(asset, downloads_dir or (Path.home() / "Downloads"))
    if downloaded is None:
        open_url(release.html_url)
        return
    if open_path is not None:
        open_path(downloaded)
    elif platform == "darwin":
        subprocess.run(["open", str(downloaded)], check=False)
    elif platform == "win32":
        subprocess.run(["explorer", "/select,", str(downloaded)], check=False)
    else:
        open_url(release.html_url)
