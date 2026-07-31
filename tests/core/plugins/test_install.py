"""Installer engine: zip safety, single-root rule, validation gate, trash."""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import httpx
import pytest

from nparseplus.core.plugins import install as install_module
from nparseplus.core.plugins.install import (
    fetch_https_bytes,
    install_from_file,
    install_from_url,
    install_from_zip,
    trash_plugin_data,
    uninstall,
)
from nparseplus_sdk.loading import MODULE_NAMESPACE, import_plugin_module

from .conftest import PLUGIN_TEMPLATE

GOOD_SOURCE = PLUGIN_TEMPLATE.format(
    plugin_id="zipped",
    name="Zipped",
    version="1.0.0",
    extra_meta="",
    activate_body="        pass",
    deactivate_body="        pass",
)


def make_zip(path: Path, members: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return path


def make_lying_zip(path: Path, name: str, content: bytes, declared: int) -> Path:
    """A stored-member zip whose headers understate the member's real size."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr(name, content)
    raw = bytearray(path.read_bytes())
    lie = declared.to_bytes(4, "little")
    local = raw.index(b"PK\x03\x04")  # uncompressed size sits at +22
    raw[local + 22 : local + 26] = lie
    central = raw.index(b"PK\x01\x02")  # ...and at +24 in the directory entry
    raw[central + 24 : central + 28] = lie
    path.write_bytes(bytes(raw))
    return path


def patch_default_transport(monkeypatch, transport: httpx.MockTransport) -> None:
    """Route the module's own default https fetch through a mock transport."""
    real = install_module.fetch_https_bytes
    monkeypatch.setattr(
        install_module,
        "fetch_https_bytes",
        lambda url, **kwargs: real(url, **{**kwargs, "transport": transport}),
    )


def test_install_package_zip(tmp_path: Path) -> None:
    archive = make_zip(
        tmp_path / "plug.zip",
        {"zipped/__init__.py": GOOD_SOURCE, "zipped/README.md": "hi"},
    )
    plugins_dir = tmp_path / "plugins"
    result = install_from_zip(archive, plugins_dir)
    assert result.ok, result.errors
    assert result.meta is not None and result.meta.id == "zipped"
    assert (plugins_dir / "zipped" / "__init__.py").is_file()
    assert not (plugins_dir / ".install-staging").exists()


def test_install_single_file_zip(tmp_path: Path) -> None:
    archive = make_zip(tmp_path / "plug.zip", {"solo.py": GOOD_SOURCE})
    result = install_from_zip(archive, tmp_path / "plugins")
    assert result.ok, result.errors
    assert (tmp_path / "plugins" / "solo.py").is_file()


def test_zip_slip_member_rejected(tmp_path: Path) -> None:
    archive = make_zip(
        tmp_path / "evil.zip",
        {"pkg/__init__.py": GOOD_SOURCE, "../escape.py": "print('pwned')"},
    )
    result = install_from_zip(archive, tmp_path / "plugins")
    assert not result.ok
    assert any("unsafe member" in e for e in result.errors)
    assert not (tmp_path / "escape.py").exists()


def test_absolute_member_rejected(tmp_path: Path) -> None:
    archive = make_zip(tmp_path / "abs.zip", {"/tmp/abs.py": GOOD_SOURCE})
    result = install_from_zip(archive, tmp_path / "plugins")
    assert not result.ok


def test_multiple_roots_rejected(tmp_path: Path) -> None:
    archive = make_zip(tmp_path / "two.zip", {"one.py": GOOD_SOURCE, "two.py": GOOD_SOURCE})
    result = install_from_zip(archive, tmp_path / "plugins")
    assert not result.ok
    assert any("exactly one plugin" in e for e in result.errors)


def test_package_without_init_rejected(tmp_path: Path) -> None:
    archive = make_zip(tmp_path / "noinit.zip", {"pkg/mod.py": GOOD_SOURCE})
    result = install_from_zip(archive, tmp_path / "plugins")
    assert not result.ok
    assert any("__init__.py" in e for e in result.errors)


def test_size_cap_enforced(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(install_module, "MAX_TOTAL_UNCOMPRESSED_BYTES", 64)
    archive = make_zip(tmp_path / "big.zip", {"big.py": "x = 1\n" + "#" * 1000})
    result = install_from_zip(archive, tmp_path / "plugins")
    assert not result.ok
    assert any("expands to" in e for e in result.errors)


def test_invalid_plugin_zip_rejected_and_not_installed(tmp_path: Path) -> None:
    archive = make_zip(tmp_path / "bad.zip", {"bad.py": "import nope_never\n"})
    plugins_dir = tmp_path / "plugins"
    result = install_from_zip(archive, plugins_dir)
    assert not result.ok
    assert not (plugins_dir / "bad.py").exists()
    assert not (plugins_dir / ".install-staging").exists()


def test_already_installed_rejected(tmp_path: Path) -> None:
    archive = make_zip(tmp_path / "plug.zip", {"solo.py": GOOD_SOURCE})
    plugins_dir = tmp_path / "plugins"
    assert install_from_zip(archive, plugins_dir).ok
    result = install_from_zip(archive, plugins_dir)
    assert not result.ok
    assert any("already installed" in e for e in result.errors)


class TestLiveModuleNamespace:
    """Validating a candidate must not repoint an already-loaded plugin.

    ``validate_plugin`` imports the candidate, and ``import_plugin_module``
    keys ``sys.modules`` by the path *stem* — so without care, validating a
    same-stem copy leaves the running plugin's import entry pointing into the
    staging directory the installer then deletes.
    """

    @pytest.fixture(autouse=True)
    def _restore_sys_modules(self):
        """These tests import plugins for real; don't leak them to other tests."""
        before = dict(sys.modules)
        yield
        for name in [n for n in sys.modules if n.startswith(MODULE_NAMESPACE)]:
            if name not in before:
                del sys.modules[name]
        sys.modules.update(
            {n: m for n, m in before.items() if n.startswith(MODULE_NAMESPACE)},
        )

    @staticmethod
    def _live_package(plugins_dir: Path, name: str) -> ModuleType:
        """Install a package plugin with a submodule and import it for real."""
        plugins_dir.mkdir(parents=True, exist_ok=True)
        package = plugins_dir / name
        package.mkdir()
        # `from .helper import ...`, not `from . import helper` — the latter
        # needs the grandparent namespace package, which loading.py never
        # creates (see tests/core/plugins/test_discovery.py for the scheme).
        (package / "__init__.py").write_text(
            GOOD_SOURCE + "\nfrom .helper import VALUE  # noqa: E402\n", encoding="utf-8"
        )
        (package / "helper.py").write_text("VALUE = 'original'\n", encoding="utf-8")
        return import_plugin_module(package)

    def test_a_fresh_install_does_not_clobber_a_live_module(self, tmp_path: Path) -> None:
        # The latent bug this guard also covers: a `demo.py` archive installs
        # cleanly beside a live `demo/` package (the exists-check compares
        # paths, which differ), but both import under the same stem.
        plugins_dir = tmp_path / "plugins"
        live = self._live_package(plugins_dir, "demo")
        key = f"{MODULE_NAMESPACE}.demo"
        assert sys.modules[key] is live

        archive = make_zip(tmp_path / "other.zip", {"demo.py": GOOD_SOURCE})
        assert install_from_zip(archive, plugins_dir).ok

        assert sys.modules[key] is live
        assert sys.modules[f"{key}.helper"].VALUE == "original"

    def test_validation_leaves_no_staging_paths_behind(self, tmp_path: Path) -> None:
        plugins_dir = tmp_path / "plugins"
        self._live_package(plugins_dir, "demo")
        archive = make_zip(tmp_path / "other.zip", {"demo.py": GOOD_SOURCE})
        install_from_zip(archive, plugins_dir)

        staging = str(plugins_dir / ".install-staging")
        for name, module in list(sys.modules.items()):
            if not name.startswith(MODULE_NAMESPACE):
                continue
            assert staging not in (getattr(module, "__file__", None) or "")

    def test_a_failed_validation_also_restores_the_namespace(self, tmp_path: Path) -> None:
        plugins_dir = tmp_path / "plugins"
        live = self._live_package(plugins_dir, "demo")
        archive = make_zip(tmp_path / "bad.zip", {"demo.py": "import nope_never\n"})
        assert not install_from_zip(archive, plugins_dir).ok
        assert sys.modules[f"{MODULE_NAMESPACE}.demo"] is live

    def test_an_unrelated_stem_is_left_alone(self, tmp_path: Path) -> None:
        plugins_dir = tmp_path / "plugins"
        live = self._live_package(plugins_dir, "demo")
        archive = make_zip(tmp_path / "other.zip", {"unrelated.py": GOOD_SOURCE})
        assert install_from_zip(archive, plugins_dir).ok
        assert sys.modules[f"{MODULE_NAMESPACE}.demo"] is live


def test_install_from_local_py_file(tmp_path: Path) -> None:
    source = tmp_path / "local.py"
    source.write_text(GOOD_SOURCE, encoding="utf-8")
    result = install_from_file(source, tmp_path / "plugins")
    assert result.ok, result.errors
    assert (tmp_path / "plugins" / "local.py").is_file()


def test_install_from_url_requires_https(tmp_path: Path) -> None:
    result = install_from_url("http://example.com/p.zip", tmp_path / "plugins")
    assert not result.ok
    assert any("https" in e for e in result.errors)


def test_install_from_url_with_injected_fetch(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("fetched.py", GOOD_SOURCE)
    result = install_from_url(
        "https://example.com/p.zip",
        tmp_path / "plugins",
        fetch=lambda url: buffer.getvalue(),
    )
    assert result.ok, result.errors
    assert (tmp_path / "plugins" / "fetched.py").is_file()


def test_install_from_url_download_failure_isolated(tmp_path: Path) -> None:
    def failing_fetch(url: str) -> bytes:
        raise OSError("network down")

    result = install_from_url(
        "https://example.com/p.zip", tmp_path / "plugins", fetch=failing_fetch
    )
    assert not result.ok
    assert any("download failed" in e for e in result.errors)


def test_uninstall_moves_to_trash(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    victim = plugins_dir / "gone.py"
    victim.write_text(GOOD_SOURCE, encoding="utf-8")
    assert uninstall(victim, plugins_dir) is None
    assert not victim.exists()
    assert (plugins_dir / "trash" / "gone.py").is_file()
    # A second install+uninstall of the same name gets a numbered slot.
    victim.write_text(GOOD_SOURCE, encoding="utf-8")
    assert uninstall(victim, plugins_dir) is None
    assert (plugins_dir / "trash" / "gone.py.1").is_file()


def test_uninstall_outside_plugins_dir_refused(tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere.py"
    outside.write_text("x = 1", encoding="utf-8")
    error = uninstall(outside, tmp_path / "plugins")
    assert error is not None and "not inside" in error
    assert outside.exists()


def test_sha256_match_installs_and_reported(tmp_path: Path) -> None:
    import hashlib

    archive = make_zip(tmp_path / "plug.zip", {"solo.py": GOOD_SOURCE})
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    result = install_from_zip(archive, tmp_path / "plugins", expected_sha256=digest.upper())
    assert result.ok, result.errors
    assert result.sha256 == digest


def test_sha256_mismatch_rejected_without_extraction(tmp_path: Path) -> None:
    archive = make_zip(tmp_path / "plug.zip", {"solo.py": GOOD_SOURCE})
    plugins_dir = tmp_path / "plugins"
    result = install_from_zip(archive, plugins_dir, expected_sha256="b" * 64)
    assert not result.ok
    assert any("checksum mismatch" in e for e in result.errors)
    assert not plugins_dir.exists() or not any(plugins_dir.iterdir())


def test_py_file_sha256_and_mismatch(tmp_path: Path) -> None:
    import hashlib

    source = tmp_path / "local.py"
    source.write_text(GOOD_SOURCE, encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    ok = install_from_file(source, tmp_path / "plugins", expected_sha256=digest)
    assert ok.ok and ok.sha256 == digest
    bad = install_from_file(source, tmp_path / "plugins2", expected_sha256="c" * 64)
    assert not bad.ok
    assert any("checksum mismatch" in e for e in bad.errors)


def test_url_install_records_source_and_hash(tmp_path: Path) -> None:
    import hashlib
    import io as _io

    buffer = _io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("hashed.py", GOOD_SOURCE)
    payload = buffer.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    result = install_from_url(
        "https://example.com/hashed.zip",
        tmp_path / "plugins",
        fetch=lambda url: payload,
        expected_sha256=digest,
    )
    assert result.ok, result.errors
    assert result.source_url == "https://example.com/hashed.zip"
    assert result.sha256 == digest


def test_reserved_root_refused_in_either_casing(tmp_path: Path) -> None:
    """Discovery skips ``trash``/``Trash`` case-insensitively; so must this."""
    for root in ("trash", "Trash"):
        archive = make_zip(
            tmp_path / f"{root}.zip",
            {f"{root}/__init__.py": GOOD_SOURCE},
        )
        result = install_from_zip(archive, tmp_path / "plugins")
        assert not result.ok
        assert any("reserved" in e for e in result.errors), root


class TestFetchHttpsBytes:
    def test_redirect_to_http_refused(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "example.com":
                return httpx.Response(302, headers={"location": "http://evil.example/p.zip"})
            return httpx.Response(200, content=b"plaintext payload")

        with pytest.raises(ValueError, match="non-https"):
            fetch_https_bytes(
                "https://example.com/p.zip",
                timeout=1.0,
                max_bytes=4096,
                transport=httpx.MockTransport(handler),
            )

    def test_https_redirect_followed(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/start":
                return httpx.Response(302, headers={"location": "https://cdn.example.com/final"})
            return httpx.Response(200, content=b"artifact")

        payload = fetch_https_bytes(
            "https://example.com/start",
            timeout=1.0,
            max_bytes=4096,
            transport=httpx.MockTransport(handler),
        )
        assert payload == b"artifact"

    def test_redirect_loop_bounded(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "https://example.com/again"})

        with pytest.raises(ValueError, match="too many redirects"):
            fetch_https_bytes(
                "https://example.com/start",
                timeout=1.0,
                max_bytes=4096,
                transport=httpx.MockTransport(handler),
            )

    def test_oversize_body_aborted_mid_stream(self) -> None:
        emitted: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            def chunks():
                for _ in range(1000):
                    emitted.append(1)
                    yield b"x" * 1024

            return httpx.Response(200, content=chunks())

        with pytest.raises(ValueError, match="byte limit"):
            fetch_https_bytes(
                "https://example.com/huge.zip",
                timeout=1.0,
                max_bytes=4096,
                transport=httpx.MockTransport(handler),
            )
        assert len(emitted) < 50, "whole body was buffered instead of aborted"


def test_install_from_url_default_fetch_refuses_downgrade(tmp_path: Path, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://evil.example/p.zip"})

    patch_default_transport(monkeypatch, httpx.MockTransport(handler))
    result = install_from_url("https://example.com/p.zip", tmp_path / "plugins")
    assert not result.ok
    assert any("non-https" in e for e in result.errors)


def test_install_from_url_default_fetch_caps_size(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(install_module, "MAX_TOTAL_UNCOMPRESSED_BYTES", 1024)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 8192)

    patch_default_transport(monkeypatch, httpx.MockTransport(handler))
    result = install_from_url("https://example.com/p.zip", tmp_path / "plugins")
    assert not result.ok
    assert any("byte limit" in e for e in result.errors)


def test_extraction_stops_at_the_byte_budget(tmp_path: Path) -> None:
    archive = make_zip(tmp_path / "wide.zip", {"pkg/__init__.py": "#" * 4096})
    staging = tmp_path / "staging"
    staging.mkdir()
    with zipfile.ZipFile(archive) as zf:
        error = install_module._extract_limited(zf, staging, 512)
    assert error is not None and "during extraction" in error


def test_member_larger_than_it_declares_is_refused(tmp_path: Path) -> None:
    """The declared size passes the pre-check; the real bytes must not."""
    archive = make_lying_zip(
        tmp_path / "liar.zip",
        "liar.py",
        (GOOD_SOURCE + "#" * 4096).encode(),
        declared=8,
    )
    plugins_dir = tmp_path / "plugins"
    result = install_from_zip(archive, plugins_dir)
    assert not result.ok
    assert any("could not be extracted" in e for e in result.errors), result.errors
    assert not (plugins_dir / "liar.py").exists()
    assert not (plugins_dir / ".install-staging").exists()


def test_trash_plugin_data_moves_dir_aside(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    data_dir = tmp_path / "plugin-data" / "ghost"
    data_dir.mkdir(parents=True)
    (data_dir / "storage.json").write_text('{"secret": 1}', encoding="utf-8")
    assert trash_plugin_data(data_dir, plugins_dir) is None
    assert not data_dir.exists()
    assert (plugins_dir / "trash" / "plugin-data" / "ghost" / "storage.json").is_file()
    # Missing data dir is not an error, and a repeat gets a numbered slot.
    assert trash_plugin_data(data_dir, plugins_dir) is None
    data_dir.mkdir(parents=True)
    assert trash_plugin_data(data_dir, plugins_dir) is None
    assert (plugins_dir / "trash" / "plugin-data" / "ghost.1").is_dir()


def test_url_install_wrong_hash_refused(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("swapped.py", GOOD_SOURCE)
    result = install_from_url(
        "https://example.com/swapped.zip",
        tmp_path / "plugins",
        fetch=lambda url: buffer.getvalue(),
        expected_sha256="d" * 64,
    )
    assert not result.ok
    assert any("checksum mismatch" in e for e in result.errors)
    assert not (tmp_path / "plugins" / "swapped.py").exists()
