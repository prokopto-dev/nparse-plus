"""Self-updater — GitHub releases check + per-platform install flow.

No in-place binary swap before 1.4 (EQTool's two-phase ping/pong updater is
not ported): the check compares published GitHub release tags against
``nparseplus.__version__`` with ``packaging.version`` and collects every
intervening release body for the update-details window. "Install" downloads
the platform artifact to ~/Downloads and opens it (macOS DMG), or falls back
to opening the release page in a browser.

Qt-free; the tray layer marshals results to the GUI thread itself. Every
failure — including the repo not existing yet — degrades to "no update".
"""

from __future__ import annotations

import logging
import platform as platform_mod
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

# Flatpak mounts this file into every sandboxed app instance.
FLATPAK_INFO = Path("/.flatpak-info")


def running_in_flatpak(info_path: Path = FLATPAK_INFO) -> bool:
    """True when running inside a Flatpak sandbox."""
    return info_path.exists()


def releases_api_url() -> str:
    return f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases?per_page=100"


def releases_page_url() -> str:
    return f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases"


class ReleaseAsset(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    name: str
    browser_download_url: str
    size: int = 0


class ReleaseNote(BaseModel):
    """One published release between the installed and target versions."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    version: str
    body: str = ""
    html_url: str = ""


class ReleaseInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    version: str  # normalized, no leading "v"
    html_url: str
    assets: tuple[ReleaseAsset, ...] = ()
    notes: tuple[ReleaseNote, ...] = ()


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
        installed = Version(current or nparseplus.__version__)
        if not isinstance(payload, list):
            return None
        releases: list[tuple[Version, dict]] = []
        for item in payload:
            if not isinstance(item, dict) or item.get("draft") or item.get("prerelease"):
                continue
            try:
                version = Version(str(item.get("tag_name", "")).lstrip("v"))
            except Exception:
                continue
            releases.append((version, item))
    except Exception:  # includes InvalidVersion on junk tags
        logger.debug("update check failed", exc_info=True)
        return None
    releases.sort(key=lambda release: release[0], reverse=True)
    if not releases:
        return None
    latest, latest_payload = releases[0]
    if latest <= installed:
        return None
    return ReleaseInfo(
        version=str(latest),
        html_url=str(latest_payload.get("html_url", releases_page_url())),
        assets=tuple(
            ReleaseAsset.model_validate(a)
            for a in latest_payload.get("assets", [])
            if isinstance(a, dict)
        ),
        notes=tuple(
            ReleaseNote(
                version=str(version),
                body=str(item.get("body") or "").strip(),
                html_url=str(item.get("html_url") or ""),
            )
            for version, item in releases
            if installed < version <= latest
        ),
    )


def format_release_notes(release: ReleaseInfo) -> str:
    """Markdown for every published version crossed by this update."""
    sections: list[str] = []
    for note in release.notes:
        body = note.body or "No changelog entry was published for this version."
        sections.append(f"## Version {note.version}\n\n{body}")
    if not sections:
        return f"## Version {release.version}\n\nNo changelog entry was published for this version."
    return "\n\n---\n\n".join(sections)


# macOS ships one DMG per architecture (…-macos-arm64.dmg / …-macos-x86_64.dmg).
# platform.machine() reports the RUNNING interpreter's arch (arm64 native, or
# x86_64 under Rosetta), which is exactly the build the user needs.
_MACOS_ARCH = {"arm64": "arm64", "x86_64": "x86_64", "amd64": "x86_64"}


def pick_asset(
    release: ReleaseInfo,
    platform: str = sys.platform,
    in_flatpak: bool | None = None,
    machine: str | None = None,
) -> ReleaseAsset | None:
    """The artifact for this platform: macOS .dmg (arch-matched), Windows .zip,
    Linux .flatpak inside the sandbox / .tar.gz outside; None when unknown."""
    if platform.startswith("linux"):
        flatpak = running_in_flatpak() if in_flatpak is None else in_flatpak
        suffix = ".flatpak" if flatpak else ".tar.gz"
    else:
        suffix = {"darwin": ".dmg", "win32": ".zip"}.get(platform)
    if suffix is None:
        return None
    if platform == "darwin":
        arch = _MACOS_ARCH.get((machine or platform_mod.machine()).lower())
        if arch is not None:
            match = next(
                (a for a in release.assets if a.name.lower().endswith(f"-macos-{arch}.dmg")),
                None,
            )
            if match is not None:
                return match
        # Fall back to any .dmg — older releases shipped a single arm64 DMG.
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
    elif platform.startswith("linux"):
        # Inside Flatpak this routes through the OpenURI portal, so the host
        # offers its software installer for the downloaded .flatpak.
        subprocess.run(["xdg-open", str(downloaded)], check=False)
    else:
        open_url(release.html_url)
