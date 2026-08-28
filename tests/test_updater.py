"""Self-updater — release check, asset picking, download (MockTransport)."""

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from nparseplus import updater
from nparseplus.config.settings import Settings
from nparseplus.updater import (
    DownloadStatus,
    ReleaseAsset,
    ReleaseInfo,
    check_for_update,
    digest_error,
    download_asset,
    expected_sha256,
    format_release_notes,
    install_action,
    pick_asset,
    stream_https_to_file,
)

RELEASE_JSON = {
    "tag_name": "v9.9.9",
    "html_url": "https://github.com/prokopto-dev/nparse-plus/releases/tag/v9.9.9",
    "prerelease": False,
    "draft": False,
    "body": "- Added the newest feature.",
    "assets": [
        {
            "name": "nParse+-9.9.9.dmg",
            "browser_download_url": "https://dl.test/a.dmg",
            "size": 5,
            "digest": "sha256:" + "0" * 64,
        },
        {
            "name": "nparseplus-win64.zip",
            "browser_download_url": "https://dl.test/a.zip",
            "size": 5,
        },
        {"name": "nparseplus-linux.tar.gz", "browser_download_url": "https://dl.test/a.tgz"},
        {"name": "nparseplus-linux.flatpak", "browser_download_url": "https://dl.test/a.flatpak"},
    ],
}


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _release_handler(request: httpx.Request) -> httpx.Response:
    assert "api.github.com" in request.url.host
    older = {
        **RELEASE_JSON,
        "tag_name": "v5.0.0",
        "html_url": "https://github.com/prokopto-dev/nparse-plus/releases/tag/v5.0.0",
        "body": "- Fixed an older bug.",
        "assets": [],
    }
    return httpx.Response(200, json=[older, RELEASE_JSON])


def test_newer_release_found() -> None:
    release = check_for_update(current="1.0.0", client=_client(_release_handler))
    assert release is not None
    assert release.version == "9.9.9"
    assert len(release.assets) == 4
    assert [note.version for note in release.notes] == ["9.9.9", "5.0.0"]


def test_equal_or_older_release_is_no_update() -> None:
    assert check_for_update(current="9.9.9", client=_client(_release_handler)) is None
    assert check_for_update(current="10.0", client=_client(_release_handler)) is None


def test_v_prefix_and_junk_tags() -> None:
    def junk(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"tag_name": "not-a-version"}])

    assert check_for_update(current="1.0.0", client=_client(junk)) is None


def test_missing_repo_fails_soft() -> None:
    def gone(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    assert check_for_update(current="1.0.0", client=_client(gone)) is None


def test_release_history_is_semver_sorted_and_filters_unpublished() -> None:
    payload = [
        {"tag_name": "v1.5.0", "body": "five", "assets": []},
        {"tag_name": "v2.0.0-rc.1", "prerelease": True, "assets": []},
        {"tag_name": "v1.10.0", "body": "ten", "assets": []},
        {"tag_name": "v1.6.0", "draft": True, "assets": []},
    ]

    release = check_for_update(
        current="1.4.0", client=_client(lambda request: httpx.Response(200, json=payload))
    )

    assert release is not None and release.version == "1.10.0"
    assert [note.version for note in release.notes] == ["1.10.0", "1.5.0"]


def test_format_release_notes_includes_every_crossed_version() -> None:
    release = check_for_update(current="1.0.0", client=_client(_release_handler))
    markdown = format_release_notes(release)
    assert markdown.index("Version 9.9.9") < markdown.index("Version 5.0.0")
    assert "newest feature" in markdown
    assert "older bug" in markdown


def test_pick_asset_per_platform() -> None:
    release = check_for_update(current="1.0.0", client=_client(_release_handler))
    assert pick_asset(release, "darwin").name.endswith(".dmg")
    assert pick_asset(release, "win32").name.endswith(".zip")
    assert pick_asset(release, "linux", in_flatpak=False).name.endswith(".tar.gz")
    assert pick_asset(release, "linux", in_flatpak=True).name.endswith(".flatpak")
    assert pick_asset(release, "sunos") is None


def _dmg(name: str) -> ReleaseAsset:
    return ReleaseAsset(name=name, browser_download_url=f"https://dl.test/{name}")


def test_pick_asset_macos_matches_architecture() -> None:
    release = ReleaseInfo(
        version="1.0.0",
        html_url="https://example/release",
        assets=(
            _dmg("nParse+-1.0.0-macos-arm64.dmg"),
            _dmg("nParse+-1.0.0-macos-x86_64.dmg"),
        ),
    )
    assert pick_asset(release, "darwin", machine="arm64").name.endswith("-macos-arm64.dmg")
    assert pick_asset(release, "darwin", machine="x86_64").name.endswith("-macos-x86_64.dmg")
    # amd64 (rarely reported on macOS) normalizes to the x86_64 build.
    assert pick_asset(release, "darwin", machine="amd64").name.endswith("-macos-x86_64.dmg")


def test_pick_asset_macos_falls_back_to_single_dmg() -> None:
    # Older releases shipped a single arm64-only DMG; an Intel client still gets
    # *a* DMG (the old behavior) rather than nothing.
    release = ReleaseInfo(
        version="1.0.0",
        html_url="https://example/release",
        assets=(_dmg("nParse+-1.0.0-macos-arm64.dmg"),),
    )
    assert pick_asset(release, "darwin", machine="x86_64").name.endswith("-macos-arm64.dmg")


def _macos_release(*names: str) -> ReleaseInfo:
    return ReleaseInfo(
        version="1.0.0",
        html_url="https://example/release",
        # Every release carries the Windows zip too — the trap the macOS
        # branch must not fall into.
        assets=tuple(_dmg(name) for name in (*names, "nparseplus-1.0.0-win64.zip")),
    )


def test_pick_asset_macos_self_update_prefers_the_app_zip() -> None:
    # A DMG needs hdiutil to open; the zip is what a swap helper can unpack.
    release = _macos_release(
        "nParse+-1.0.0-macos-arm64.dmg",
        "nParse+-1.0.0-macos-arm64.zip",
        "nParse+-1.0.0-macos-x86_64.dmg",
        "nParse+-1.0.0-macos-x86_64.zip",
    )
    for arch in ("arm64", "x86_64"):
        picked = pick_asset(release, "darwin", machine=arch, self_update=True)
        assert picked.name == f"nParse+-1.0.0-macos-{arch}.zip"
        # The human path is unchanged: the DMG mounts and shows Applications.
        assert pick_asset(release, "darwin", machine=arch).name.endswith(f"-macos-{arch}.dmg")


def test_pick_asset_macos_self_update_falls_back_to_the_dmg() -> None:
    # A release from before #75 has no zip at all; the updater takes the DMG
    # rather than nothing — and never the Windows zip sitting beside it.
    release = _macos_release("nParse+-1.0.0-macos-arm64.dmg", "nParse+-1.0.0-macos-x86_64.dmg")
    picked = pick_asset(release, "darwin", machine="arm64", self_update=True)
    assert picked.name == "nParse+-1.0.0-macos-arm64.dmg"
    # Even with no macOS asset for this arch at all, the fallback stays a DMG.
    intel = pick_asset(
        _macos_release("nParse+-1.0.0-macos-arm64.dmg"),
        "darwin",
        machine="x86_64",
        self_update=True,
    )
    assert intel.name.endswith(".dmg")


def test_pick_asset_self_update_changes_nothing_off_darwin() -> None:
    release = check_for_update(current="1.0.0", client=_client(_release_handler))
    for platform, kwargs in (
        ("win32", {}),
        ("linux", {"in_flatpak": False}),
        ("linux", {"in_flatpak": True}),
        ("sunos", {}),
    ):
        plain = pick_asset(release, platform, **kwargs)
        assert pick_asset(release, platform, **kwargs, self_update=True) == plain


# The v2.21.0 asset list, verbatim and in API order — the shape that armed
# #160. Since #75 every release ships a ditto zip of the macOS .app beside each
# DMG, and those sort BEFORE the Windows zip, so a bare ``.zip`` sweep picked a
# macOS bundle for Windows. The per-platform tests above pass without this list
# only because their fixtures predate the macOS zips.
FULL_RELEASE_ASSETS = (
    "nParse+-2.21.0-macos-arm64.dmg",
    "nParse+-2.21.0-macos-arm64.zip",
    "nParse+-2.21.0-macos-x86_64.dmg",
    "nParse+-2.21.0-macos-x86_64.zip",
    "nparseplus-2.21.0-linux-x86_64.flatpak",
    "nparseplus-2.21.0-linux-x86_64.tar.gz",
    "nparseplus-2.21.0-win64.zip",
    # The Debian 12 package. It is deliberately shaped to be INERT here: no
    # "-linux" substring, and no suffix any branch of pick_asset looks for.
    "nparseplus_2.21.0_amd64.deb",
)


def _full_release() -> ReleaseInfo:
    return ReleaseInfo(
        version="2.21.0",
        html_url="https://example/release",
        assets=tuple(_dmg(name) for name in FULL_RELEASE_ASSETS),
    )


def test_pick_asset_over_a_full_release_is_distinct_and_correctly_tagged() -> None:
    release = _full_release()
    picks = {
        "win32": pick_asset(release, "win32"),
        "linux-tarball": pick_asset(release, "linux", in_flatpak=False),
        "linux-flatpak": pick_asset(release, "linux", in_flatpak=True),
        "darwin-arm64": pick_asset(release, "darwin", machine="arm64"),
        "darwin-x86_64": pick_asset(release, "darwin", machine="x86_64"),
    }
    # #160: this was nParse+-2.21.0-macos-arm64.zip — the first .zip in the list.
    assert picks["win32"].name == "nparseplus-2.21.0-win64.zip"
    assert picks["linux-tarball"].name == "nparseplus-2.21.0-linux-x86_64.tar.gz"
    assert picks["linux-flatpak"].name == "nparseplus-2.21.0-linux-x86_64.flatpak"
    assert picks["darwin-arm64"].name == "nParse+-2.21.0-macos-arm64.dmg"
    assert picks["darwin-x86_64"].name == "nParse+-2.21.0-macos-x86_64.dmg"
    # No two platforms may resolve to one artifact: sharing a container format
    # is exactly how the wrong build reaches a user.
    names = [asset.name for asset in picks.values()]
    assert len(set(names)) == len(names)


# Every way pick_asset can be called. ``updater`` is the ONLY consumer of
# release assets in the codebase (nothing else reads ``release.assets``), and
# every selection funnels through pick_asset / _pick_macos_asset, so a matrix
# over this is a matrix over the whole update path.
EVERY_CALL = tuple(
    (platform, {**machine, **flatpak, "self_update": self_update})
    for platform, machine in (
        ("darwin", {"machine": "arm64"}),
        ("darwin", {"machine": "x86_64"}),
        ("darwin", {"machine": None}),
        ("win32", {}),
        ("linux", {}),
        ("sunos", {}),
    )
    for flatpak in (({"in_flatpak": True},) if platform == "linux" else ({},))
    + (({"in_flatpak": False},) if platform == "linux" else ())
    for self_update in (False, True)
)


def test_the_debian_package_is_invisible_to_every_platform() -> None:
    """The .deb is a separate artifact; nobody may be handed it by accident.

    It is built in a debian:12 container so it runs where the generic tarball
    (built on ubuntu-latest, glibc 2.39) cannot. Teaching pick_asset to PREFER
    it for Debian users is follow-up work (#163); what must hold today is that
    adding it to a release changes nobody's pick, on any call.
    """
    for assets in (FULL_RELEASE_ASSETS, tuple(reversed(FULL_RELEASE_ASSETS))):
        release = ReleaseInfo(
            version="2.21.0",
            html_url="https://example/release",
            assets=tuple(_dmg(name) for name in assets),
        )
        for platform, kwargs in EVERY_CALL:
            pick = pick_asset(release, platform, **kwargs)
            assert pick is None or not pick.name.endswith(".deb"), (
                f"{platform} {kwargs} was handed the Debian package"
            )


def test_a_deb_only_release_offers_nobody_anything() -> None:
    """The fallbacks must not reach for it either.

    ``_pick_macos_asset``'s last resort is a bare ``.dmg`` sweep, and each
    branch degrades to None when nothing matches — which the caller turns into
    "open the release page". A new artifact must land in that None, never in
    somebody's fallback. Stronger than the test above: with no other asset
    present there is nothing else a buggy selector could return.
    """
    release = ReleaseInfo(
        version="2.21.0",
        html_url="https://example/release",
        assets=(_dmg("nparseplus_2.21.0_amd64.deb"),),
    )
    for platform, kwargs in EVERY_CALL:
        assert pick_asset(release, platform, **kwargs) is None


def test_pick_asset_does_not_depend_on_asset_order() -> None:
    """``next()`` takes the FIRST match, so ordering must not decide anything.

    GitHub's asset order is not a contract. Any predicate two artifacts can
    both satisfy turns that into a coin flip — which is what #160 was.
    """
    forward = _full_release()
    backward = ReleaseInfo(
        version="2.21.0",
        html_url="https://example/release",
        assets=tuple(reversed(forward.assets)),
    )
    for platform, kwargs in (
        ("win32", {}),
        ("linux", {"in_flatpak": False}),
        ("linux", {"in_flatpak": True}),
        ("darwin", {"machine": "arm64"}),
        ("darwin", {"machine": "x86_64"}),
    ):
        first = pick_asset(forward, platform, **kwargs)
        second = pick_asset(backward, platform, **kwargs)
        assert first is not None and second is not None
        assert first.name == second.name


def test_the_deployed_picker_still_resolves_to_the_generic_tarball() -> None:
    """A literal copy of the predicate every ALREADY-RELEASED binary runs.

    This repo can change ``pick_asset``; it cannot change the copy compiled
    into the build sitting on a user's disk. That copy sweeps for
    ``"-linux" in name and name.endswith(".tar.gz")`` and takes the first
    match, so the real constraint on any new Linux release asset is that this
    predicate keeps finding the generic tarball. Do not "fix" this test to
    call pick_asset — reimplementing it here is the whole point.
    """
    for assets in (FULL_RELEASE_ASSETS, tuple(reversed(FULL_RELEASE_ASSETS))):
        legacy = next(
            (n for n in assets if (low := n.lower()).endswith(".tar.gz") and "-linux" in low),
            None,
        )
        assert legacy == "nparseplus-2.21.0-linux-x86_64.tar.gz"


def test_pick_asset_full_release_macos_self_update_takes_the_macos_zip() -> None:
    # The zip a swap helper unpacks is legitimately what macOS wants here — and
    # it is the arch-tagged macOS one, never the Windows zip beside it.
    release = _full_release()
    for arch in ("arm64", "x86_64"):
        picked = pick_asset(release, "darwin", machine=arch, self_update=True)
        assert picked.name == f"nParse+-2.21.0-macos-{arch}.zip"
    # The flag moves nothing on Windows: still the win64 build, both ways.
    assert pick_asset(release, "win32", self_update=True).name == "nparseplus-2.21.0-win64.zip"


def test_pick_asset_refuses_an_untagged_artifact_rather_than_guessing() -> None:
    # A release carrying only a foreign zip resolves to None — the caller opens
    # the release page, which shows, unlike silently installing a macOS bundle.
    release = ReleaseInfo(
        version="2.21.0",
        html_url="https://example/release",
        assets=(_dmg("nParse+-2.21.0-macos-arm64.zip"),),
    )
    assert pick_asset(release, "win32") is None


def test_running_in_flatpak_detection(tmp_path: Path) -> None:
    marker = tmp_path / ".flatpak-info"
    assert not updater.running_in_flatpak(marker)
    marker.write_text("[Application]\nname=io.github.prokopto_dev.nparse_plus\n")
    assert updater.running_in_flatpak(marker)


BODY = b"DMG BYTES"
BODY_SHA256 = hashlib.sha256(BODY).hexdigest()


def _asset(**kwargs) -> ReleaseAsset:
    return ReleaseAsset(
        name=kwargs.pop("name", "x.dmg"),
        browser_download_url=kwargs.pop("url", "https://dl.test/x.dmg"),
        **kwargs,
    )


def _serve(body: bytes = BODY):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    return handler


def _staging(tmp_path: Path, name: str = "x.dmg") -> Path:
    return tmp_path / f"{name}.part"


def test_download_asset(tmp_path: Path) -> None:
    outcome = download_asset(_asset(), tmp_path, client=_client(_serve()))
    assert outcome.ok and outcome.path.read_bytes() == BODY
    # The staging file is promoted, not left beside the artifact.
    assert not _staging(tmp_path).exists()


def test_download_failure_reports_a_transport_failure(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    outcome = download_asset(_asset(), tmp_path, client=_client(handler))
    assert outcome.status is DownloadStatus.FAILED
    assert not outcome.ok and not outcome.refused and outcome.path is None
    assert outcome.needs_attention


def test_download_asset_accepts_the_published_digest(tmp_path: Path) -> None:
    asset = _asset(digest=f"sha256:{BODY_SHA256}", size=len(BODY))
    outcome = download_asset(asset, tmp_path, client=_client(_serve()))
    assert outcome.ok and outcome.path.read_bytes() == BODY
    # Verified against a published digest: nothing to tell the user.
    assert outcome.pinned and not outcome.needs_attention


def test_download_asset_refuses_a_digest_mismatch(tmp_path: Path, caplog) -> None:
    asset = _asset(digest="sha256:" + "a" * 64)

    with caplog.at_level("WARNING"):
        outcome = download_asset(asset, tmp_path, client=_client(_serve()))

    assert outcome.status is DownloadStatus.DIGEST_MISMATCH
    assert outcome.refused and outcome.path is None
    # Nothing survives under the artifact's own name — the failed download is
    # not left somewhere the user could open it.
    assert not (tmp_path / "x.dmg").exists()
    assert not _staging(tmp_path).exists()
    # Both digests are named, like the plugin installer's refusal.
    assert "a" * 64 in caplog.text and BODY_SHA256 in caplog.text
    assert "a" * 64 in outcome.detail and BODY_SHA256 in outcome.detail


def test_a_refusal_and_a_flaky_network_log_differently(tmp_path: Path, caplog) -> None:
    # A 500 means "try again"; a digest mismatch means the bytes are wrong.
    # The log level is one half of the distinction; the status is the other.
    def dead(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with caplog.at_level("WARNING"):
        assert download_asset(_asset(), tmp_path, client=_client(dead)).status is (
            DownloadStatus.FAILED
        )
    assert "ERROR" not in [r.levelname for r in caplog.records]

    caplog.clear()
    with caplog.at_level("WARNING"):
        refused = download_asset(
            _asset(digest="sha256:" + "a" * 64), tmp_path, client=_client(_serve())
        )
    assert refused.refused
    assert "ERROR" in [r.levelname for r in caplog.records]


def test_download_asset_refuses_a_truncated_body(tmp_path: Path) -> None:
    # Truncation with a digest published is caught by the hash; this is the
    # same failure on a release from before GitHub served assets[].digest.
    asset = _asset(size=len(BODY))
    outcome = download_asset(asset, tmp_path, client=_client(_serve(BODY[:4])))
    assert outcome.status is DownloadStatus.SIZE_MISMATCH
    # Unpinnable AND wrong: the size is the only check such a release has, and
    # the message says that rather than blaming a checksum that never existed.
    assert not outcome.pinned
    message = outcome.message()
    assert "not the size" in message and "did not match the checksum" not in message
    assert not (tmp_path / "x.dmg").exists()
    assert not _staging(tmp_path).exists()


def test_download_asset_that_cannot_be_pinned_says_so(tmp_path: Path) -> None:
    # A release published before GitHub served assets[].digest: the download
    # succeeds, and "nothing could check it" is NOT the same message as
    # "the check failed".
    outcome = download_asset(_asset(size=len(BODY)), tmp_path, client=_client(_serve()))
    assert outcome.ok and not outcome.pinned
    assert outcome.needs_attention  # worth one sentence, unlike a verified one
    assert not outcome.refused
    message = outcome.message()
    assert "no checksum" in message and "did not match" not in message


def test_download_asset_refuses_a_redirect_to_http(tmp_path: Path) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.scheme == "https":
            return httpx.Response(302, headers={"location": "http://dl.test/plaintext.dmg"})
        return httpx.Response(200, content=BODY)

    assert not download_asset(_asset(), tmp_path, client=_client(handler)).ok
    # The plaintext hop is never requested at all.
    assert seen == ["https://dl.test/x.dmg"]
    assert not (tmp_path / "x.dmg").exists()


def test_download_asset_follows_an_https_redirect(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/x.dmg":
            return httpx.Response(302, headers={"location": "https://cdn.test/blob"})
        return httpx.Response(200, content=BODY)

    outcome = download_asset(
        _asset(digest=f"sha256:{BODY_SHA256}"), tmp_path, client=_client(handler)
    )
    assert outcome.ok and outcome.path.read_bytes() == BODY


def test_download_asset_refuses_an_over_budget_body(tmp_path: Path) -> None:
    # No published size to compare against, so the only fact is that the
    # response would not stop — a refusal, not "try again in a moment".
    outcome = download_asset(_asset(), tmp_path, client=_client(_serve()), max_bytes=4)
    assert outcome.status is DownloadStatus.REFUSED and outcome.refused
    assert not _staging(tmp_path).exists()


def test_download_asset_caps_at_the_published_size(tmp_path: Path) -> None:
    # A body longer than the size GitHub published is cut off mid-stream
    # rather than buffered to the global budget first — and the cut is a
    # SIZE refusal, not a transport failure: the ceiling that stopped it was
    # the release's own number, so this is the same disagreement _size_error
    # reports for a short body.
    asset = _asset(size=4)
    outcome = download_asset(asset, tmp_path, client=_client(_serve()))
    assert outcome.status is DownloadStatus.SIZE_MISMATCH and outcome.refused
    assert "expected 4 bytes" in outcome.detail


def test_an_oversized_artifact_does_not_open_the_release_page(tmp_path: Path, monkeypatch) -> None:
    # The regression this pairs with: a substituted artifact that is merely
    # LONGER than the release says used to read as a network failure, which
    # sent the user straight back to the page serving it.
    monkeypatch.setattr(updater, "_download_client", lambda c: _client(_serve()))
    release = ReleaseInfo(
        version="9.9.9",
        html_url="https://example/release",
        assets=(ReleaseAsset(name="a.dmg", browser_download_url="https://dl.test/a.dmg", size=4),),
    )
    opened_urls: list[str] = []

    outcome = install_action(
        release,
        platform="darwin",
        open_path=lambda path: None,
        open_url=opened_urls.append,
        downloads_dir=tmp_path,
    )

    assert outcome.status is DownloadStatus.SIZE_MISMATCH
    assert opened_urls == [] and not outcome.opened_release_page


def test_download_asset_refuses_a_path_bearing_asset_name(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    asset = _asset(name="../outside/evil.dmg")
    outcome = download_asset(asset, tmp_path / "downloads", client=_client(_serve()))
    assert outcome.refused and outcome.status is DownloadStatus.REFUSED
    assert list(outside.iterdir()) == []


def test_every_refusal_message_names_the_artifact_and_the_reason() -> None:
    # The acceptance criterion of #93, stated once over the whole vocabulary.
    for status in updater.REFUSALS:
        outcome = updater.DownloadOutcome(
            status=status, asset_name="nParse+-9.9.9-macos-arm64.dmg", detail="the technical line"
        )
        assert "nParse+-9.9.9-macos-arm64.dmg" in outcome.message()
        assert "refused" in outcome.title().lower()
        assert outcome.needs_attention and not outcome.ok
    # ...and a transport failure still reads as a network problem, not as a
    # verification result.
    failed = updater.DownloadOutcome(status=DownloadStatus.FAILED, asset_name="x.dmg")
    assert "network" in failed.message() and not failed.refused
    assert "checksum" not in failed.message()


def test_stream_https_to_file_returns_hash_and_length(tmp_path: Path) -> None:
    digest, written = stream_https_to_file(
        "https://dl.test/x.dmg", tmp_path / "out.bin", client=_client(_serve())
    )
    assert digest == BODY_SHA256
    assert written == len(BODY)


def test_stream_https_to_file_refuses_a_plain_http_url(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="non-https"):
        stream_https_to_file("http://dl.test/x.dmg", tmp_path / "out.bin", client=_client(_serve()))


def test_stream_https_to_file_stops_at_the_redirect_limit(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://dl.test/again"})

    with pytest.raises(ValueError, match="too many redirects"):
        stream_https_to_file("https://dl.test/x.dmg", tmp_path / "out.bin", client=_client(handler))


def test_expected_sha256_parses_only_a_usable_digest() -> None:
    assert expected_sha256(_asset(digest=f"sha256:{BODY_SHA256.upper()}")) == BODY_SHA256
    assert expected_sha256(_asset()) is None  # a release from before the field
    assert expected_sha256(_asset(digest="sha512:" + "0" * 128)) is None
    assert expected_sha256(_asset(digest="sha256:nothex")) is None
    assert expected_sha256(_asset(digest=BODY_SHA256)) is None  # unprefixed


def test_digest_error_names_both_digests_and_passes_when_unpinnable() -> None:
    assert digest_error(BODY_SHA256, None) is None
    assert digest_error(BODY_SHA256, BODY_SHA256) is None
    message = digest_error(BODY_SHA256, "b" * 64)
    assert message is not None
    assert "b" * 64 in message and BODY_SHA256 in message


def test_release_asset_digest_survives_the_release_check() -> None:
    release = check_for_update(current="1.0.0", client=_client(_release_handler))
    assert expected_sha256(release.assets[0]) == "0" * 64


def test_install_action_darwin_downloads_and_opens(tmp_path: Path, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"DMG BYTES")

    monkeypatch.setattr(updater, "_download_client", lambda c: _client(handler))
    release = ReleaseInfo(
        version="9.9.9",
        html_url="https://example/release",
        assets=(ReleaseAsset(name="a.dmg", browser_download_url="https://dl.test/a.dmg"),),
    )
    opened_paths: list[Path] = []
    opened_urls: list[str] = []
    outcome = install_action(
        release,
        platform="darwin",
        open_path=opened_paths.append,
        open_url=opened_urls.append,
        downloads_dir=tmp_path,
    )
    assert opened_paths == [tmp_path / "a.dmg"]
    assert opened_urls == []
    assert (tmp_path / "a.dmg").read_bytes() == b"DMG BYTES"
    assert outcome.ok and outcome.path == tmp_path / "a.dmg"


def test_install_action_falls_back_to_release_page(tmp_path: Path) -> None:
    release = ReleaseInfo(version="9.9.9", html_url="https://example/release", assets=())
    opened_urls: list[str] = []
    outcome = install_action(
        release, platform="linux", open_url=opened_urls.append, downloads_dir=tmp_path
    )
    assert opened_urls == ["https://example/release"]
    assert outcome.status is DownloadStatus.UNAVAILABLE and outcome.opened_release_page
    assert "open in your browser" in outcome.message()


def _one_asset_release(digest: str | None = None) -> ReleaseInfo:
    return ReleaseInfo(
        version="9.9.9",
        html_url="https://example/release",
        assets=(
            ReleaseAsset(
                name="a.dmg", browser_download_url="https://dl.test/a.dmg", digest=digest or ""
            ),
        ),
    )


def test_install_action_does_not_open_the_release_page_on_a_refusal(
    tmp_path: Path, monkeypatch
) -> None:
    # THE point of #93: the release page serves the very artifact that was
    # just refused, so opening it silently hands the user the bad download
    # back and tells them nothing.
    monkeypatch.setattr(updater, "_download_client", lambda c: _client(_serve()))
    opened_paths: list[Path] = []
    opened_urls: list[str] = []

    outcome = install_action(
        _one_asset_release(digest="sha256:" + "a" * 64),
        platform="darwin",
        open_path=opened_paths.append,
        open_url=opened_urls.append,
        downloads_dir=tmp_path,
    )

    assert outcome.status is DownloadStatus.DIGEST_MISMATCH
    assert opened_urls == [] and opened_paths == []
    assert not outcome.opened_release_page
    assert "checksum" in outcome.message() and "a.dmg" in outcome.message()


def test_install_action_still_opens_the_release_page_on_a_network_failure(
    tmp_path: Path, monkeypatch
) -> None:
    def dead(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    monkeypatch.setattr(updater, "_download_client", lambda c: _client(dead))
    opened_urls: list[str] = []

    outcome = install_action(
        _one_asset_release(),
        platform="darwin",
        open_path=lambda path: None,
        open_url=opened_urls.append,
        downloads_dir=tmp_path,
    )

    assert outcome.status is DownloadStatus.FAILED and outcome.opened_release_page
    assert opened_urls == ["https://example/release"]
    assert "release page is open in your browser" in outcome.message()


def test_release_json_shape_matches_github() -> None:
    # Guard: the fields we parse exist in a real GitHub /releases/latest body.
    parsed = json.loads(json.dumps(RELEASE_JSON))
    assert {"tag_name", "html_url", "assets"} <= set(parsed)
    # digest is served per asset ("sha256:<hex>") — confirmed on all five
    # assets of v2.3.2 — and is what the download is pinned to.
    assert {"name", "browser_download_url", "size", "digest"} <= set(parsed["assets"][0])


# --- the beta channel (#186) ------------------------------------------------

#: A release list shaped like the one master now produces: a shipped stable,
#: two betas of the version after it, and a draft nobody is ever offered.
CHANNEL_RELEASES = [
    {
        "tag_name": "v2.30.0-beta.2",
        "html_url": "https://example/b2",
        "prerelease": True,
        "draft": False,
        "body": "beta two",
        "assets": [],
    },
    {
        "tag_name": "v2.30.0-beta.1",
        "html_url": "https://example/b1",
        "prerelease": True,
        "draft": False,
        "body": "beta one",
        "assets": [],
    },
    {
        "tag_name": "v2.29.0",
        "html_url": "https://example/stable",
        "prerelease": False,
        "draft": False,
        "body": "stable",
        "assets": [],
    },
    {
        "tag_name": "v2.31.0-beta.1",
        "html_url": "https://example/draft",
        "prerelease": True,
        "draft": True,
        "body": "unpublished",
        "assets": [],
    },
]


def _channel_client(payload=None) -> httpx.Client:
    body = CHANNEL_RELEASES if payload is None else payload

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    return _client(handler)


def test_a_stable_client_is_offered_exactly_what_it_is_offered_today() -> None:
    """The acceptance criterion that protects everybody already installed.

    Given a release list that now contains prereleases, a stable client must
    resolve the same release it would have resolved before the beta tier
    existed. This is not merely the default — it is what every nParse+ binary
    ever published does unconditionally, so any other answer here would change
    what an existing user is offered without them asking.
    """
    release = check_for_update("2.28.0", client=_channel_client())
    assert release is not None
    assert release.version == "2.29.0"
    # And no beta leaked into the notes it will render.
    assert [note.version for note in release.notes] == ["2.29.0"]


def test_the_default_channel_is_stable() -> None:
    """Callers that pass nothing get the conservative tier."""
    assert updater.DEFAULT_CHANNEL is updater.UpdateChannel.STABLE
    explicit = check_for_update(
        "2.28.0", client=_channel_client(), channel=updater.UpdateChannel.STABLE
    )
    implicit = check_for_update("2.28.0", client=_channel_client())
    assert explicit == implicit


def test_a_beta_client_is_offered_the_newest_prerelease() -> None:
    release = check_for_update(
        "2.28.0", client=_channel_client(), channel=updater.UpdateChannel.BETA
    )
    assert release is not None
    # Normalized PEP 440: the wire tag is v2.30.0-beta.2.
    assert release.version == "2.30.0b2"
    assert [note.version for note in release.notes] == ["2.30.0b2", "2.30.0b1", "2.29.0"]


def test_a_draft_is_offered_on_no_channel() -> None:
    """``draft`` is unpublished, not merely unfinished — nobody sees it."""
    for channel in updater.UpdateChannel:
        release = check_for_update("2.30.0b2", client=_channel_client(), channel=channel)
        assert release is None or "2.31.0" not in release.version


def test_a_promoted_stable_outranks_the_beta_it_came_from() -> None:
    """The other half of acceptance: a beta user rolls onto the stable.

    ``packaging.Version`` orders 2.30.0b3 < 2.30.0, so the promotion is newer
    than the beta line it finalizes and needs no special handling to be
    offered. A user is stranded only if a beta line is abandoned outright,
    which is documented rather than designed around.
    """
    published = [
        {
            "tag_name": "v2.30.0",
            "html_url": "https://example/final",
            "prerelease": False,
            "draft": False,
            "body": "promoted",
            "assets": [],
        },
        *CHANNEL_RELEASES[:3],
    ]
    for channel in updater.UpdateChannel:
        release = check_for_update("2.30.0b2", client=_channel_client(published), channel=channel)
        assert release is not None, f"{channel} was offered nothing"
        assert release.version == "2.30.0"


def test_a_beta_client_is_not_offered_an_older_beta() -> None:
    """The comparison is unchanged on the beta channel — only the filter moved."""
    assert (
        check_for_update("2.30.0b2", client=_channel_client(), channel=updater.UpdateChannel.BETA)
        is None
    )


# --- Flatpak is stable-only, structurally (#186 review) ---------------------


def test_effective_channel_clamps_beta_to_stable_inside_flatpak() -> None:
    """release.yml publishes no beta .flatpak and no beta OSTree commit.

    So a sandboxed build honouring a beta preference would announce an update
    the portal can only answer with "nothing to install", and whose download
    fallback finds no asset either — ``pick_asset`` looks for a ``.flatpak``.
    An update the app insists exists and cannot deliver is worse than not
    offering the channel.
    """
    assert updater.effective_channel("beta", in_flatpak=True) is updater.UpdateChannel.STABLE
    assert updater.effective_channel("beta", in_flatpak=False) is updater.UpdateChannel.BETA


def test_effective_channel_leaves_stable_alone_everywhere() -> None:
    for sandboxed in (True, False):
        assert (
            updater.effective_channel("stable", in_flatpak=sandboxed)
            is updater.UpdateChannel.STABLE
        )


def test_effective_channel_reads_an_unusable_value_as_stable() -> None:
    """``update_channel`` is a Literal, so this is a hand-edited file only."""
    for configured in (None, "", "nightly", "BETA"):
        assert (
            updater.effective_channel(configured, in_flatpak=False) is updater.UpdateChannel.STABLE
        ), configured


def test_effective_channel_probes_the_sandbox_when_not_told(monkeypatch) -> None:
    """The default path is the real probe, not a silent assumption of 'no'."""
    monkeypatch.setattr(updater, "running_in_flatpak", lambda: True)
    assert updater.effective_channel("beta") is updater.UpdateChannel.STABLE
    monkeypatch.setattr(updater, "running_in_flatpak", lambda: False)
    assert updater.effective_channel("beta") is updater.UpdateChannel.BETA


def test_a_flatpak_client_is_never_offered_a_prerelease() -> None:
    """End to end: the clamp in front of the check that would have offered it."""
    channel = updater.effective_channel("beta", in_flatpak=True)
    release = check_for_update("2.28.0", client=_channel_client(), channel=channel)
    assert release is not None
    assert release.version == "2.29.0", "a Flatpak client was offered a prerelease"


def test_a_stored_beta_preference_is_not_rewritten_by_the_clamp() -> None:
    """The clamp is a read, not a migration.

    Settings outlive the install that wrote them in both directions: a beta
    preference carried into a Flatpak install must not take effect, and must
    still be there if the same settings directory is used by a tarball install
    again.
    """
    settings = Settings()
    settings.general.update_channel = "beta"
    assert (
        updater.effective_channel(settings.general.update_channel, in_flatpak=True)
        is updater.UpdateChannel.STABLE
    )
    assert settings.general.update_channel == "beta"


# --- the tray's own path (#186 review: cover BOTH checks) -------------------


def test_the_tray_update_check_uses_the_effective_channel() -> None:
    """The tray reads the same clamp the settings window does.

    Called unbound against a stub rather than through a real ``NomnsParse``:
    the tray is a QApplication subclass, and what is under test is which
    channel it resolves, not Qt. Two consumers of one rule is exactly the
    shape that rots when only one of them is covered.
    """
    from types import SimpleNamespace

    from nparseplus.helpers.application import NomnsParse

    def resolve(configured: str, *, sandboxed: bool) -> updater.UpdateChannel:
        stub = SimpleNamespace(
            _backend=SimpleNamespace(
                settings=SimpleNamespace(general=SimpleNamespace(update_channel=configured))
            )
        )
        original = updater.running_in_flatpak
        updater.running_in_flatpak = lambda: sandboxed
        try:
            return NomnsParse._update_channel(stub)
        finally:
            updater.running_in_flatpak = original

    assert resolve("beta", sandboxed=False) is updater.UpdateChannel.BETA
    assert resolve("beta", sandboxed=True) is updater.UpdateChannel.STABLE
    assert resolve("stable", sandboxed=False) is updater.UpdateChannel.STABLE
    assert resolve("nonsense", sandboxed=False) is updater.UpdateChannel.STABLE
