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
    ReplaceTarget,
    fetch_https_bytes,
    install_from_file,
    install_from_url,
    install_from_zip,
    trash_plugin_data,
    uninstall,
)
from nparseplus_sdk.loading import MODULE_NAMESPACE, import_plugin_module
from nparseplus_sdk.validate import validate_plugin

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
        # A real relative import, so the submodule ends up in sys.modules
        # under the plugin's namespace — that entry is what a careless
        # validation run would strand pointing into the deleted staging dir.
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

    def test_the_candidate_imports_its_own_modules_not_the_live_ones(self, tmp_path: Path) -> None:
        """The update case: a new module asking for a name the old copy lacks.

        Both versions' submodules answer to the same ``sys.modules`` keys, so
        a leftover ``demo.helper`` from the running version is what the
        candidate's ``__init__`` was handed — and validation failed with an
        ImportError naming the very file the user was replacing. Any plugin
        that grew a module or a public name between releases was un-updatable
        while it was loaded, which is every interesting update.
        """
        plugins_dir = tmp_path / "plugins"
        live = self._live_package(plugins_dir, "demo")
        archive = make_zip(
            tmp_path / "v2.zip",
            {
                "demo/__init__.py": (
                    source_at("2.0.0") + "\nfrom .helper import ADDED_LATER  # noqa: E402\n"
                ),
                "demo/helper.py": "VALUE = 'updated'\nADDED_LATER = 'new'\n",
            },
        )

        result = install_from_zip(
            archive, plugins_dir, replace=ReplaceTarget("zipped", plugins_dir / "demo")
        )

        assert result.ok, result.errors
        assert result.meta is not None and result.meta.version == "2.0.0"
        # ...and the running plugin is still the one the host is holding.
        assert sys.modules[f"{MODULE_NAMESPACE}.demo"] is live
        assert sys.modules[f"{MODULE_NAMESPACE}.demo.helper"].VALUE == "original"


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


def source_at(version: str, plugin_id: str = "zipped") -> str:
    return PLUGIN_TEMPLATE.format(
        plugin_id=plugin_id,
        name="Zipped",
        version=version,
        extra_meta="",
        activate_body="        pass",
        deactivate_body="        pass",
    )


class TestReplaceInPlace:
    """Updating an installed plugin without uninstalling it first.

    The identity trap these tests pin down: the install path comes from the
    archive root, the consent record and plugin-data come from ``meta.id``,
    and nothing in the installer reconciles the two — so both have to be
    checked, at different points in the flow.
    """

    @staticmethod
    def _installed(tmp_path: Path, version: str = "1.0.0") -> tuple[Path, Path]:
        """Install v1 and return (plugins_dir, installed_path)."""
        plugins_dir = tmp_path / "plugins"
        archive = make_zip(tmp_path / "v1.zip", {"solo.py": source_at(version)})
        result = install_from_zip(archive, plugins_dir)
        assert result.ok, result.errors
        assert result.installed_path is not None
        return plugins_dir, result.installed_path

    @staticmethod
    def _target(installed: Path, plugin_id: str = "zipped") -> ReplaceTarget:
        return ReplaceTarget(plugin_id=plugin_id, installed_path=installed)

    def test_replace_installs_the_new_version_over_the_old(self, tmp_path: Path) -> None:
        plugins_dir, installed = self._installed(tmp_path)
        archive = make_zip(tmp_path / "v2.zip", {"solo.py": source_at("2.0.0")})

        result = install_from_zip(archive, plugins_dir, replace=self._target(installed))

        assert result.ok, result.errors
        assert result.meta is not None and result.meta.version == "2.0.0"
        assert result.installed_path == installed
        assert "2.0.0" in installed.read_text(encoding="utf-8")

    def test_the_replaced_copy_goes_to_trash_and_is_reported(self, tmp_path: Path) -> None:
        plugins_dir, installed = self._installed(tmp_path)
        archive = make_zip(tmp_path / "v2.zip", {"solo.py": source_at("2.0.0")})

        result = install_from_zip(archive, plugins_dir, replace=self._target(installed))

        trashed = plugins_dir / "trash" / "solo.py"
        assert trashed.is_file()
        assert "1.0.0" in trashed.read_text(encoding="utf-8")
        assert result.replaced_path == trashed

    def test_no_backup_directory_survives_a_successful_update(self, tmp_path: Path) -> None:
        plugins_dir, installed = self._installed(tmp_path)
        archive = make_zip(tmp_path / "v2.zip", {"solo.py": source_at("2.0.0")})
        assert install_from_zip(archive, plugins_dir, replace=self._target(installed)).ok
        assert not (plugins_dir / ".install-backup").exists()
        assert not (plugins_dir / ".install-staging").exists()

    def test_a_different_archive_root_is_refused(self, tmp_path: Path) -> None:
        plugins_dir, installed = self._installed(tmp_path)
        # Same plugin id, renamed distribution root: this would install a
        # SECOND copy beside the old one, both claiming the same id.
        archive = make_zip(tmp_path / "v2.zip", {"renamed.py": source_at("2.0.0")})

        result = install_from_zip(archive, plugins_dir, replace=self._target(installed))

        assert not result.ok
        assert any("not an update" in e for e in result.errors)
        assert "1.0.0" in installed.read_text(encoding="utf-8")
        assert not (plugins_dir / "renamed.py").exists()

    def test_a_different_plugin_id_is_refused_after_validation(self, tmp_path: Path) -> None:
        plugins_dir, installed = self._installed(tmp_path)
        # Same file name, different identity — an id takeover that would
        # inherit the victim's consent record and plugin-data.
        archive = make_zip(
            tmp_path / "v2.zip", {"solo.py": source_at("2.0.0", plugin_id="stranger")}
        )

        result = install_from_zip(archive, plugins_dir, replace=self._target(installed))

        assert not result.ok
        assert any("'stranger'" in e and "'zipped'" in e for e in result.errors)
        assert "1.0.0" in installed.read_text(encoding="utf-8")

    def test_a_target_outside_the_plugins_dir_is_refused(self, tmp_path: Path) -> None:
        plugins_dir, _installed = self._installed(tmp_path)
        outside = tmp_path / "elsewhere" / "solo.py"
        outside.parent.mkdir()
        outside.write_text(source_at("1.0.0"), encoding="utf-8")
        archive = make_zip(tmp_path / "v2.zip", {"solo.py": source_at("2.0.0")})

        result = install_from_zip(archive, plugins_dir, replace=self._target(outside))

        assert not result.ok
        assert any("not inside the plugins directory" in e for e in result.errors)
        assert outside.exists()

    def test_a_missing_installed_copy_is_refused(self, tmp_path: Path) -> None:
        plugins_dir, installed = self._installed(tmp_path)
        installed.unlink()
        archive = make_zip(tmp_path / "v2.zip", {"solo.py": source_at("2.0.0")})

        result = install_from_zip(archive, plugins_dir, replace=self._target(installed))

        assert not result.ok
        assert any("no longer at" in e for e in result.errors)

    def test_a_shape_change_is_refused(self, tmp_path: Path) -> None:
        # `solo.py` -> `solo/` is a different filesystem node, so the archive
        # root no longer matches the installed path.
        plugins_dir, installed = self._installed(tmp_path)
        archive = make_zip(
            tmp_path / "v2.zip",
            {"solo/__init__.py": source_at("2.0.0"), "solo/helper.py": "VALUE = 1\n"},
        )

        result = install_from_zip(archive, plugins_dir, replace=self._target(installed))

        assert not result.ok
        assert any("not an update" in e for e in result.errors)
        assert installed.is_file()

    def test_a_checksum_mismatch_leaves_the_installed_copy_alone(self, tmp_path: Path) -> None:
        plugins_dir, installed = self._installed(tmp_path)
        archive = make_zip(tmp_path / "v2.zip", {"solo.py": source_at("2.0.0")})

        result = install_from_zip(
            archive, plugins_dir, replace=self._target(installed), expected_sha256="0" * 64
        )

        assert not result.ok
        assert any("checksum mismatch" in e for e in result.errors)
        assert "1.0.0" in installed.read_text(encoding="utf-8")

    def test_invalid_new_code_leaves_the_installed_copy_alone(self, tmp_path: Path) -> None:
        plugins_dir, installed = self._installed(tmp_path)
        archive = make_zip(tmp_path / "v2.zip", {"solo.py": "import nope_never\n"})

        result = install_from_zip(archive, plugins_dir, replace=self._target(installed))

        assert not result.ok
        assert "1.0.0" in installed.read_text(encoding="utf-8")
        assert not (plugins_dir / "trash").exists()

    def test_a_failed_swap_rolls_the_working_copy_back(self, tmp_path: Path, monkeypatch) -> None:
        plugins_dir, installed = self._installed(tmp_path)
        archive = make_zip(tmp_path / "v2.zip", {"solo.py": source_at("2.0.0")})

        # Fail the second move — the one that puts the new copy in place —
        # after the old copy has already been set aside.
        real_move = install_module.shutil.move
        calls = {"n": 0}

        def flaky_move(src, dst):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("disk went away")
            return real_move(src, dst)

        monkeypatch.setattr(install_module.shutil, "move", flaky_move)
        result = install_from_zip(archive, plugins_dir, replace=self._target(installed))

        assert not result.ok
        assert any("could not install the new version" in e for e in result.errors)
        assert installed.is_file()
        assert "1.0.0" in installed.read_text(encoding="utf-8")
        assert not (plugins_dir / ".install-backup").exists()

    def test_the_rolled_back_copy_still_loads(self, tmp_path: Path, monkeypatch) -> None:
        plugins_dir, installed = self._installed(tmp_path)
        archive = make_zip(tmp_path / "v2.zip", {"solo.py": source_at("2.0.0")})
        real_move = install_module.shutil.move
        calls = {"n": 0}

        def flaky_move(src, dst):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("disk went away")
            return real_move(src, dst)

        monkeypatch.setattr(install_module.shutil, "move", flaky_move)
        install_from_zip(archive, plugins_dir, replace=self._target(installed))

        report = validate_plugin(installed)
        assert report.ok, report.errors
        assert report.meta is not None and report.meta.version == "1.0.0"

    def test_replace_works_for_a_py_file_install(self, tmp_path: Path) -> None:
        plugins_dir = tmp_path / "plugins"
        first = tmp_path / "solo.py"
        first.write_text(source_at("1.0.0"), encoding="utf-8")
        installed = install_from_file(first, plugins_dir).installed_path
        assert installed is not None

        second = tmp_path / "newer" / "solo.py"
        second.parent.mkdir()
        second.write_text(source_at("2.0.0"), encoding="utf-8")
        result = install_from_file(second, plugins_dir, replace=self._target(installed))

        assert result.ok, result.errors
        assert "2.0.0" in installed.read_text(encoding="utf-8")
        assert (plugins_dir / "trash" / "solo.py").is_file()
        # The user's own file is copied, never consumed.
        assert second.is_file()

    def test_replace_works_through_install_from_url(self, tmp_path: Path) -> None:
        plugins_dir, installed = self._installed(tmp_path)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("solo.py", source_at("2.0.0"))

        result = install_from_url(
            "https://example.com/p.zip",
            plugins_dir,
            fetch=lambda url: buffer.getvalue(),
            replace=self._target(installed),
        )

        assert result.ok, result.errors
        assert result.source_url == "https://example.com/p.zip"
        assert "2.0.0" in installed.read_text(encoding="utf-8")

    def test_reinstalling_the_same_version_is_allowed(self, tmp_path: Path) -> None:
        # Repairing a corrupted copy is legitimate; "is this newer?" is the
        # offer layer's decision, not the installer's.
        plugins_dir, installed = self._installed(tmp_path)
        archive = make_zip(tmp_path / "again.zip", {"solo.py": source_at("1.0.0")})
        assert install_from_zip(archive, plugins_dir, replace=self._target(installed)).ok


def test_reserved_py_filename_rejected(tmp_path: Path) -> None:
    source = tmp_path / "trash.py"
    source.write_text(GOOD_SOURCE, encoding="utf-8")
    result = install_from_file(source, tmp_path / "plugins")
    assert not result.ok
    assert any("reserved" in e for e in result.errors)


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
