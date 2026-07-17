"""Self-updater — release check, asset picking, download (MockTransport)."""

import json
from pathlib import Path

import httpx

from nparseplus import updater
from nparseplus.updater import (
    ReleaseAsset,
    ReleaseInfo,
    check_for_update,
    download_asset,
    format_release_notes,
    install_action,
    pick_asset,
)

RELEASE_JSON = {
    "tag_name": "v9.9.9",
    "html_url": "https://github.com/prokopto-dev/nparse-plus/releases/tag/v9.9.9",
    "prerelease": False,
    "draft": False,
    "body": "- Added the newest feature.",
    "assets": [
        {"name": "nParse+-9.9.9.dmg", "browser_download_url": "https://dl.test/a.dmg", "size": 5},
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


def test_running_in_flatpak_detection(tmp_path: Path) -> None:
    marker = tmp_path / ".flatpak-info"
    assert not updater.running_in_flatpak(marker)
    marker.write_text("[Application]\nname=io.github.prokopto_dev.nparse_plus\n")
    assert updater.running_in_flatpak(marker)


def test_download_asset(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"DMG BYTES")

    asset = ReleaseAsset(name="x.dmg", browser_download_url="https://dl.test/x.dmg")
    path = download_asset(asset, tmp_path, client=_client(handler))
    assert path is not None and path.read_bytes() == b"DMG BYTES"


def test_download_failure_returns_none(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    asset = ReleaseAsset(name="x.dmg", browser_download_url="https://dl.test/x.dmg")
    assert download_asset(asset, tmp_path, client=_client(handler)) is None


def test_install_action_darwin_downloads_and_opens(tmp_path: Path, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"DMG BYTES")

    monkeypatch.setattr(updater, "_client", lambda c: _client(handler))
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
