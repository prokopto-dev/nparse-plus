"""Installer engine: zip safety, single-root rule, validation gate, trash."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from nparseplus.core.plugins import install as install_module
from nparseplus.core.plugins.install import (
    install_from_file,
    install_from_url,
    install_from_zip,
    uninstall,
)

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
