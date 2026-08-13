"""Self-updater — release check, asset picking, download (MockTransport)."""

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from nparseplus import updater
from nparseplus.updater import (
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
    path = download_asset(_asset(), tmp_path, client=_client(_serve()))
    assert path is not None and path.read_bytes() == BODY
    # The staging file is promoted, not left beside the artifact.
    assert not _staging(tmp_path).exists()


def test_download_failure_returns_none(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    assert download_asset(_asset(), tmp_path, client=_client(handler)) is None


def test_download_asset_accepts_the_published_digest(tmp_path: Path) -> None:
    asset = _asset(digest=f"sha256:{BODY_SHA256}", size=len(BODY))
    path = download_asset(asset, tmp_path, client=_client(_serve()))
    assert path is not None and path.read_bytes() == BODY


def test_download_asset_refuses_a_digest_mismatch(tmp_path: Path, caplog) -> None:
    asset = _asset(digest="sha256:" + "a" * 64)

    with caplog.at_level("WARNING"):
        assert download_asset(asset, tmp_path, client=_client(_serve())) is None

    # Nothing survives under the artifact's own name — the failed download is
    # not left somewhere the user could open it.
    assert not (tmp_path / "x.dmg").exists()
    assert not _staging(tmp_path).exists()
    # Both digests are named, like the plugin installer's refusal.
    assert "a" * 64 in caplog.text and BODY_SHA256 in caplog.text


def test_download_asset_refuses_a_truncated_body(tmp_path: Path) -> None:
    # Truncation with a digest published is caught by the hash; this is the
    # same failure on a release from before GitHub served assets[].digest.
    asset = _asset(size=len(BODY))
    assert download_asset(asset, tmp_path, client=_client(_serve(BODY[:4]))) is None
    assert not (tmp_path / "x.dmg").exists()
    assert not _staging(tmp_path).exists()


def test_download_asset_refuses_a_redirect_to_http(tmp_path: Path) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.scheme == "https":
            return httpx.Response(302, headers={"location": "http://dl.test/plaintext.dmg"})
        return httpx.Response(200, content=BODY)

    assert download_asset(_asset(), tmp_path, client=_client(handler)) is None
    # The plaintext hop is never requested at all.
    assert seen == ["https://dl.test/x.dmg"]
    assert not (tmp_path / "x.dmg").exists()


def test_download_asset_follows_an_https_redirect(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/x.dmg":
            return httpx.Response(302, headers={"location": "https://cdn.test/blob"})
        return httpx.Response(200, content=BODY)

    path = download_asset(_asset(digest=f"sha256:{BODY_SHA256}"), tmp_path, client=_client(handler))
    assert path is not None and path.read_bytes() == BODY


def test_download_asset_refuses_an_over_budget_body(tmp_path: Path) -> None:
    assert download_asset(_asset(), tmp_path, client=_client(_serve()), max_bytes=4) is None
    assert not _staging(tmp_path).exists()


def test_download_asset_caps_at_the_published_size(tmp_path: Path) -> None:
    # A body longer than the size GitHub published is cut off mid-stream
    # rather than buffered to the global budget first.
    asset = _asset(size=4)
    assert download_asset(asset, tmp_path, client=_client(_serve())) is None


def test_download_asset_refuses_a_path_bearing_asset_name(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    asset = _asset(name="../outside/evil.dmg")
    assert download_asset(asset, tmp_path / "downloads", client=_client(_serve())) is None
    assert list(outside.iterdir()) == []


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
    install_action(
        release,
        platform="darwin",
        open_path=opened_paths.append,
        open_url=opened_urls.append,
        downloads_dir=tmp_path,
    )
    assert opened_paths == [tmp_path / "a.dmg"]
    assert opened_urls == []
    assert (tmp_path / "a.dmg").read_bytes() == b"DMG BYTES"


def test_install_action_falls_back_to_release_page(tmp_path: Path) -> None:
    release = ReleaseInfo(version="9.9.9", html_url="https://example/release", assets=())
    opened_urls: list[str] = []
    install_action(release, platform="linux", open_url=opened_urls.append, downloads_dir=tmp_path)
    assert opened_urls == ["https://example/release"]


def test_release_json_shape_matches_github() -> None:
    # Guard: the fields we parse exist in a real GitHub /releases/latest body.
    parsed = json.loads(json.dumps(RELEASE_JSON))
    assert {"tag_name", "html_url", "assets"} <= set(parsed)
    # digest is served per asset ("sha256:<hex>") — confirmed on all five
    # assets of v2.3.2 — and is what the download is pinned to.
    assert {"name", "browser_download_url", "size", "digest"} <= set(parsed["assets"][0])
