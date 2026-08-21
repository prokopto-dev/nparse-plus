"""Self-updater — GitHub releases check + per-platform install flow.

No in-place binary swap before 1.4 (EQTool's two-phase ping/pong updater is
not ported): the check compares published GitHub release tags against
``nparseplus.__version__`` with ``packaging.version`` and collects every
intervening release body for the update-details window. "Install" downloads
the platform artifact to ~/Downloads and opens it (macOS DMG), or falls back
to opening the release page in a browser.

The download is verified before it is handed anywhere: bytes stream to a
staging file under a byte budget with a rolling sha256, https is re-asserted
on every redirect hop, and the result is pinned to the ``sha256:`` digest
GitHub publishes for the asset. That digest is a **channel** guarantee, not a
signature — see ``expected_sha256`` for what it does and does not cover.

Every download answers with a ``DownloadOutcome`` rather than a bare path, so
a refusal can say *why* it refused instead of looking like a flaky network to
the caller (#93).

Qt-free; the tray layer marshals results to the GUI thread itself. Every
failure — including the repo not existing yet — degrades to "no update".
"""

from __future__ import annotations

import hashlib
import logging
import platform as platform_mod
import subprocess
import sys
import webbrowser
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path

import httpx
from packaging.version import Version
from pydantic import BaseModel, ConfigDict

import nparseplus

logger = logging.getLogger(__name__)

GITHUB_OWNER = "prokopto-dev"
GITHUB_REPO = "nparse-plus"
TIMEOUT_S = 10.0

# Release artifacts run 195-255 MB. The plugin installer's fetch caps a
# download at 50 MiB held *in memory*, which is the right budget for a plugin
# zip and unusable here — hence the streaming sibling below with its own
# ceiling. Do NOT raise MAX_TOTAL_UNCOMPRESSED_BYTES to meet this one. Roughly
# 3x the largest artifact today, so a build that grows does not need a code
# change, while an endless response still stops.
MAX_ASSET_BYTES = 768 * 1024 * 1024
MAX_REDIRECTS = 5
# The download's name until its digest checks out; a sibling of the final path
# so the promotion is a same-directory rename.
_STAGING_SUFFIX = ".part"

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
    # GitHub serves "sha256:<hex>" per asset (present on every asset of
    # v2.3.2). Optional because releases published before the field existed
    # carry nothing to pin to.
    digest: str | None = None


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


# macOS ships one DMG per architecture (…-macos-arm64.dmg / …-macos-x86_64.dmg)
# and, since #75, a ditto zip of the same .app beside each one.
# platform.machine() reports the RUNNING interpreter's arch (arm64 native, or
# x86_64 under Rosetta), which is exactly the build the user needs.
_MACOS_ARCH = {"arm64": "arm64", "x86_64": "x86_64", "amd64": "x86_64"}


def _pick_macos_asset(
    release: ReleaseInfo, machine: str | None, self_update: bool
) -> ReleaseAsset | None:
    """macOS artifact: the DMG a person installs, or the zip an updater unpacks.

    Two shapes of the same build ship together. A DMG is right for a human —
    it mounts and shows the drag-to-Applications window — and wrong for code,
    which would have to ``hdiutil attach`` / copy / ``detach`` to reach the
    ``.app`` inside. The zip is the opposite, so the choice belongs to the
    caller: ``self_update`` picks the zip, everything else keeps the DMG. The
    swap helper (#76) is what flips it.

    The trailing fallback is deliberately ``.dmg`` and NOT the requested
    suffix: a release carries the Windows ``.zip`` too, so a bare
    ``endswith(".zip")`` sweep here would happily hand macOS a Windows build.
    Every macOS asset is arch-tagged, and an arch-agnostic zip has never
    existed — releases that predate #75 have DMGs only, which is exactly what
    this returns.
    """
    arch = _MACOS_ARCH.get((machine or platform_mod.machine()).lower())
    if arch is not None:
        suffixes = (".zip", ".dmg") if self_update else (".dmg",)
        for suffix in suffixes:
            match = next(
                (a for a in release.assets if a.name.lower().endswith(f"-macos-{arch}{suffix}")),
                None,
            )
            if match is not None:
                return match
    # Older releases shipped a single arm64 DMG with no arch in some names.
    return next((a for a in release.assets if a.name.lower().endswith(".dmg")), None)


def pick_asset(
    release: ReleaseInfo,
    platform: str = sys.platform,
    in_flatpak: bool | None = None,
    machine: str | None = None,
    *,
    self_update: bool = False,
) -> ReleaseAsset | None:
    """The artifact for this platform: macOS .dmg (arch-matched, or the .app
    zip when ``self_update``), Windows .zip, Linux .flatpak inside the sandbox
    / .tar.gz outside; None when unknown.

    **The match is on the platform TAG, never on the suffix alone** — the same
    guard ``_pick_macos_asset`` documents, now on every branch. A container
    format is not a platform: #75 put a ditto zip of the macOS ``.app`` beside
    the Windows ``.zip``, and those sort first, so the bare
    ``endswith(".zip")`` sweep this used to do handed every Windows user a
    macOS bundle (#160). The tag is explicit for the Linux artifacts too even
    though ``.tar.gz`` and ``.flatpak`` are unique today, so that a future
    artifact in an existing container cannot arm the same trap a third time.

    Windows carries its tag right before the suffix (``…-win64.zip``); Linux
    carries the architecture in between (``…-linux-x86_64.tar.gz``), so the tag
    is matched as a substring rather than hardcoding an arch this picker does
    not choose. Nothing matching is ``None``, which the caller degrades to the
    release page — a failure that shows, unlike the wrong platform's build.
    """
    if platform == "darwin":
        return _pick_macos_asset(release, machine, self_update)
    if platform.startswith("linux"):
        flatpak = running_in_flatpak() if in_flatpak is None else in_flatpak
        tag, suffix = "-linux", (".flatpak" if flatpak else ".tar.gz")
    elif platform == "win32":
        tag, suffix = "-win", ".zip"
    else:
        return None
    return next(
        (a for a in release.assets if (name := a.name.lower()).endswith(suffix) and tag in name),
        None,
    )


class DownloadStatus(StrEnum):
    """What became of one download attempt.

    A vocabulary rather than a bool, because the reasons are not
    interchangeable: a timeout says "try again", a digest mismatch says the
    bytes that arrived are not the bytes the release describes, and no
    artifact for this platform says the release page is the only route. The
    swap helper (#76) adds its pre-flight refusals here (unwritable install
    root, insufficient disk, a translocated bundle) — each one degrades to
    download-and-open with a message naming the reason, which is this shape.
    """

    OK = "ok"  # the artifact is on disk under its real name
    DIGEST_MISMATCH = "digest_mismatch"  # wrong bytes, and we can prove it
    SIZE_MISMATCH = "size_mismatch"  # wrong length (the only guard pre-digest)
    REFUSED = "refused"  # refused for a reason only `detail` can state
    FAILED = "failed"  # transport: timeout, 5xx, dropped connection
    UNAVAILABLE = "unavailable"  # the release has nothing for this platform


#: Statuses that mean "the bytes arrived and we threw them away", as opposed to
#: "they never arrived". A caller distinguishes these two, never the members.
REFUSALS = frozenset(
    {DownloadStatus.DIGEST_MISMATCH, DownloadStatus.SIZE_MISMATCH, DownloadStatus.REFUSED}
)


class DownloadOutcome(BaseModel):
    """The result of a download attempt — path on success, reason otherwise.

    ``download_asset`` used to answer ``None`` for everything that went wrong,
    which made the one case the verification exists to catch indistinguishable
    from a flaky network: the caller opened the release page and said nothing,
    pointing the user straight back at the artifact that had just been refused
    (#93). The status carries the distinction out to the UI, and ``message()``
    is the prose — kept here, not in the dialog, so the Qt layer stays a
    renderer and the wording is testable without a window.

    ``pinned`` is deliberately orthogonal to ``status``: "this release
    published no checksum to check against" and "the checksum did not match"
    are different facts about different releases, and collapsing them would
    tell a user on a pre-digest release that their download was corrupt.
    """

    model_config = ConfigDict(frozen=True)

    status: DownloadStatus
    asset_name: str = ""
    path: Path | None = None
    #: The technical line, both digests named — what a bug report quotes.
    detail: str = ""
    #: False when the release published no usable sha256 for this asset.
    pinned: bool = True
    #: Set when the caller has already sent the user to the release page.
    opened_release_page: bool = False

    @property
    def ok(self) -> bool:
        return self.status is DownloadStatus.OK

    @property
    def refused(self) -> bool:
        """The bytes arrived and were thrown away — not a transport failure."""
        return self.status in REFUSALS

    @property
    def needs_attention(self) -> bool:
        """Worth interrupting the user for: anything but a verified download."""
        return not self.ok or not self.pinned

    def title(self) -> str:
        """Short caption for the dialog."""
        if self.refused:
            return "Update download refused"
        if self.status is DownloadStatus.FAILED:
            return "Update download failed"
        if self.status is DownloadStatus.UNAVAILABLE:
            return "No download for this platform"
        if not self.pinned:
            return "Update downloaded, but not verified"
        return "Update downloaded"

    def message(self) -> str:
        """One paragraph of prose saying what happened and what to do."""
        name = self.asset_name or "the update"
        page = (
            " The release page is open in your browser."
            if self.opened_release_page
            else " You can download it by hand from the release page."
        )
        if self.status is DownloadStatus.DIGEST_MISMATCH:
            return (
                f"The download of {name} did not match the checksum published for it, "
                "so it was discarded and nothing was installed.\n\n"
                "That is almost always a corrupted or interrupted transfer — try again. "
                "If it keeps happening, download the release by hand and compare its "
                "checksum against the one on the release page before opening it."
            )
        if self.status is DownloadStatus.SIZE_MISMATCH:
            return (
                f"The download of {name} was not the size the release said it would be, "
                "so it was discarded and nothing was installed.\n\n"
                "This release publishes no checksum, so its length is the only check "
                "there is. Try again, or download it by hand from the release page."
            )
        if self.status is DownloadStatus.REFUSED:
            return (
                f"The download of {name} was refused: it is not the artifact the release "
                f"describes, so nothing was installed.{page}"
            )
        if self.status is DownloadStatus.FAILED:
            return f"{name} could not be downloaded. This is usually a network problem.{page}"
        if self.status is DownloadStatus.UNAVAILABLE:
            return f"This release publishes no download for your platform.{page}"
        if not self.pinned:
            return (
                f"{name} downloaded, but this release publishes no checksum for it, "
                "so nothing could verify that the file is the one the release describes. "
                "Releases published before GitHub served per-asset checksums carry none; "
                "its length was all there was to check."
            )
        return f"{name} downloaded and verified against the checksum published for it."


class VerificationRefused(ValueError):
    """The artifact arrived, and is not the one the release described.

    Separated from every other download failure on purpose: a 500, a timeout
    or a dropped connection is a flaky network and the honest answer is "try
    again", while this one means the bytes that arrived are not the bytes
    GitHub published a digest for. Refusals are logged at ERROR (transport
    failures stay WARNING) and reach the caller as a ``DownloadOutcome``
    carrying one of ``REFUSALS``; the type is what carries the reason from the
    check that made it up to ``download_asset``.
    """

    def __init__(self, message: str, status: DownloadStatus) -> None:
        super().__init__(message)
        self.status = status


_HEX_DIGITS = frozenset("0123456789abcdef")


def expected_sha256(asset: ReleaseAsset) -> str | None:
    """The sha256 hex GitHub published for this asset, or None if it published none.

    **This digest is a channel guarantee, not a signature, and the difference
    is worth keeping sharp.** It arrives over the same TLS session as the
    release metadata that describes it, from the same API, so pinning to it
    proves only that the object the CDN served is the object the API named. It
    defends against a corrupted, truncated or substituted artifact — a
    mirror, a proxy, a half-written CDN object, a swapped upload. It defends
    against *nothing* that can publish a release: whatever can change the
    artifact there can change the digest beside it in the same breath.

    A signature would move the trust root off the GitHub account and onto a
    key we hold — a per-release ``SHA256SUMS`` signed with minisign, the
    public key compiled into the app. That is issue #73 item 3 and is
    deliberately not what this function is.

    Anything unparseable (no digest, an algorithm we do not compute, a
    malformed hex string) reads as "unpinnable" rather than as a failure: a
    release predating the field must still be installable, and a caller that
    treats None as verified would be the actual bug.
    """
    algorithm, _, value = (asset.digest or "").strip().lower().partition(":")
    if algorithm != "sha256" or len(value) != 64 or not set(value) <= _HEX_DIGITS:
        return None
    return value


def digest_error(actual: str, expected: str | None) -> str | None:
    """Message for a failed digest check; None when it passed or could not run.

    Both digests are named, matching ``core.plugins.install._checksum_error``:
    a user reporting this can compare what arrived against the release page by
    hand, and the two paths say it the same way.
    """
    if expected is None or actual == expected:
        return None
    return f"checksum mismatch: expected sha256 {expected}, got {actual} — refusing to install"


def _size_error(written: int, declared: int) -> str | None:
    """Refuse a body that disagrees with the size GitHub published.

    Redundant whenever a digest is present, and the only truncation guard on a
    release published before GitHub served one.

    Only the *short* half of that comparison reaches here: a body running long
    is stopped mid-stream by the byte ceiling instead, which is what
    ``ByteBudgetExceeded`` exists to keep classifiable.
    """
    if declared > 0 and written != declared:
        return f"size mismatch: expected {declared} bytes, got {written} — refusing to install"
    return None


class ByteBudgetExceeded(ValueError):
    """The body ran past the ceiling this download was given.

    Carries the ceiling because only the caller knows where it came from, and
    that decides what the overflow *means*: stopped at the size GitHub
    published, the body is provably not that artifact and the answer is the
    same refusal ``_size_error`` would have given had the stream been allowed
    to finish; stopped at the global backstop, all that is known is that the
    response would not end.
    """

    def __init__(self, message: str, limit: int) -> None:
        super().__init__(message)
        self.limit = limit


def _download_client(client: httpx.Client | None) -> httpx.Client:
    """Client for artifact downloads — redirects are NOT delegated to httpx."""
    return client or httpx.Client(
        timeout=TIMEOUT_S,
        follow_redirects=False,
        headers={"User-Agent": f"nparseplus/{nparseplus.__version__}"},
    )


def stream_https_to_file(
    url: str,
    destination: Path,
    *,
    max_bytes: int = MAX_ASSET_BYTES,
    client: httpx.Client | None = None,
) -> tuple[str, int]:
    """GET an https URL onto disk; return ``(sha256 hex, bytes written)``.

    The streaming sibling of ``core.plugins.install.fetch_https_bytes``, and
    deliberately a second function rather than a widened one: that fetch
    buffers the whole body under a 50 MiB in-memory cap, which is right for a
    plugin zip and wrong for a quarter-gigabyte release artifact in both
    dimensions. What carries across is the part that matters — redirects are
    followed **by hand** so https is re-asserted on every hop (an https
    release URL that 302s to http is refused, not silently downloaded in
    plaintext, which is what ``follow_redirects=True`` used to do), and the
    body is cut off the moment it passes ``max_bytes``.

    The hash rolls as the bytes land, so nothing is read twice and the caller
    can refuse before anything opens the file. Raises on any refusal; callers
    turn that into a user message.
    """
    digest = hashlib.sha256()
    written = 0
    session = _download_client(client)
    try:
        for _ in range(MAX_REDIRECTS + 1):
            if not url.lower().startswith("https://"):
                raise ValueError(f"refusing non-https URL {url!r}")
            # follow_redirects is passed per request rather than left to the
            # client: an injected client may default to following them, and a
            # hop httpx takes on its own is precisely the one we need to see.
            with session.stream("GET", url, follow_redirects=False) as response:
                if response.is_redirect:
                    location = response.headers.get("location", "")
                    if not location:
                        raise ValueError("redirect without a Location header")
                    url = str(response.url.join(location))
                    continue
                response.raise_for_status()
                with open(destination, "wb") as fh:
                    for chunk in response.iter_bytes():
                        written += len(chunk)
                        if written > max_bytes:
                            raise ByteBudgetExceeded(
                                f"response exceeds the {max_bytes} byte limit", max_bytes
                            )
                        digest.update(chunk)
                        fh.write(chunk)
                return digest.hexdigest(), written
        raise ValueError(f"too many redirects (limit {MAX_REDIRECTS})")
    finally:
        if client is None:
            session.close()


def download_asset(
    asset: ReleaseAsset,
    dest_dir: Path,
    client: httpx.Client | None = None,
    *,
    max_bytes: int = MAX_ASSET_BYTES,
) -> DownloadOutcome:
    """Stream the artifact into ``dest_dir``, verified; a reason on any failure.

    The bytes land on a ``.part`` sibling of the destination and are renamed
    into place only once the published digest matches what arrived — so the
    artifact never exists under its real name unverified, where the user (or,
    later, a swap step) could open it. A refusal deletes the staging file
    instead of leaving a plausible-looking partial download in ~/Downloads,
    and happens before anything opens the archive.

    ``dest_dir`` is a parameter and not a constant because the swap helper
    (#76) must stage beside the install root — ``os.rename`` is atomic only
    within one filesystem, and ~/Downloads is a different one on many setups.
    """
    # The asset name comes off the wire and is joined to a directory path; a
    # name carrying a separator would write outside dest_dir. Real GitHub asset
    # names are plain filenames, so this only ever rejects a hostile one.
    if Path(asset.name).name != asset.name:
        detail = f"refusing release asset with a path-bearing name {asset.name!r}"
        logger.error(detail)
        return DownloadOutcome(status=DownloadStatus.REFUSED, asset_name=asset.name, detail=detail)
    destination = Path(dest_dir) / asset.name
    staging = destination.with_name(destination.name + _STAGING_SUFFIX)
    expected = expected_sha256(asset)
    if expected is None:
        logger.warning(
            "release asset %s publishes no usable sha256 digest (%r) — "
            "the download cannot be pinned",
            asset.name,
            asset.digest,
        )

    def refusal(status: DownloadStatus, detail: str) -> DownloadOutcome:
        # Not a flaky network: the bytes arrived and are the wrong bytes.
        logger.error("REFUSED the downloaded %s — %s", asset.name, detail)
        staging.unlink(missing_ok=True)
        return DownloadOutcome(
            status=status,
            asset_name=asset.name,
            detail=detail,
            pinned=expected is not None,
        )

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        actual, written = stream_https_to_file(
            asset.browser_download_url,
            staging,
            # The published size is a tighter ceiling than the global budget
            # whenever GitHub gives us one.
            max_bytes=min(max_bytes, asset.size) if asset.size > 0 else max_bytes,
            client=client,
        )
        digest_refusal = digest_error(actual, expected)
        if digest_refusal is not None:
            raise VerificationRefused(digest_refusal, DownloadStatus.DIGEST_MISMATCH)
        size_refusal = _size_error(written, asset.size)
        if size_refusal is not None:
            raise VerificationRefused(size_refusal, DownloadStatus.SIZE_MISMATCH)
        staging.replace(destination)
    except VerificationRefused as exc:
        return refusal(exc.status, str(exc))
    except ByteBudgetExceeded as exc:
        # An over-long body never reaches _size_error — the stream is cut the
        # moment it passes the ceiling. When that ceiling WAS the size the
        # release published, the comparison has effectively already happened
        # and this is the same refusal, not a transport failure; letting it
        # fall through to `except Exception` would report a network problem
        # and open the release page for an artifact we just rejected.
        if asset.size > 0 and exc.limit == asset.size:
            return refusal(
                DownloadStatus.SIZE_MISMATCH,
                f"size mismatch: expected {asset.size} bytes, the response ran past that "
                "— refusing to install",
            )
        # No published size to compare against: all that is known is that the
        # response would not stop. Still a refusal — nothing arrived that we
        # would install, and retrying is not the advice.
        return refusal(DownloadStatus.REFUSED, str(exc))
    except Exception as exc:
        logger.warning("update download failed for %s: %s", asset.name, exc, exc_info=True)
        staging.unlink(missing_ok=True)
        return DownloadOutcome(
            status=DownloadStatus.FAILED,
            asset_name=asset.name,
            detail=str(exc),
            pinned=expected is not None,
        )
    return DownloadOutcome(
        status=DownloadStatus.OK,
        asset_name=asset.name,
        path=destination,
        pinned=expected is not None,
    )


def install_action(
    release: ReleaseInfo,
    platform: str = sys.platform,
    open_path: Callable[[Path], None] | None = None,
    open_url: Callable[[str], None] = webbrowser.open,
    downloads_dir: Path | None = None,
) -> DownloadOutcome:
    """User-initiated 'install': download + open, or open the release page.

    Returns what happened so the caller can say so. **A refusal does not open
    the release page**, which is the whole of #93: that page points at the
    same artifact that was just refused, so opening it silently hands the user
    back the bad download and tells them nothing. Nothing arriving at all is a
    different matter — the page is then the only route left, so a transport
    failure keeps opening it and the message says it did.
    """
    asset = pick_asset(release, platform)
    if asset is None:
        open_url(release.html_url)
        return DownloadOutcome(status=DownloadStatus.UNAVAILABLE, opened_release_page=True)
    outcome = download_asset(asset, downloads_dir or (Path.home() / "Downloads"))
    if outcome.status is DownloadStatus.FAILED:
        open_url(release.html_url)
        return outcome.model_copy(update={"opened_release_page": True})
    if not outcome.ok or outcome.path is None:
        return outcome
    downloaded = outcome.path
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
        return outcome.model_copy(update={"opened_release_page": True})
    return outcome
