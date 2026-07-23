"""Installer engine behind the in-app plugin manager (Qt-free).

Installs are deliberately conservative:

- Zip archives are validated member-by-member before extraction: absolute
  paths, ``..`` traversal, symlinks, oversize archives, and member floods
  are all rejected (zip-slip protection).
- The archive must contain exactly one plugin: a single top-level package
  directory (with ``__init__.py``) or a single top-level ``.py`` file.
- The candidate is extracted to a hidden staging dir and validated with the
  SDK's ``validate_plugin`` (the same load-correctness check as the
  ``nparseplus-plugin`` CLI) before it is moved into the plugins directory;
  advisory static-scan warnings are surfaced for the UI to show.
- URL installs are https-only; the byte fetch is injectable so the UI can
  route it through a worker thread.
- Uninstall moves the plugin into ``plugins/trash/`` rather than deleting.

Note: validation imports the plugin, so its module-level code runs at
install time — the same trust boundary as running the plugin. The manager
UI states this next to the install buttons.
"""

from __future__ import annotations

import hashlib
import shutil
import stat
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from nparseplus.core.plugins.discovery import RESERVED_DIR_NAMES
from nparseplus_sdk import PluginMeta
from nparseplus_sdk.validate import validate_plugin

MAX_ARCHIVE_MEMBERS = 2000
MAX_TOTAL_UNCOMPRESSED_BYTES = 50 * 1024 * 1024  # 50 MiB
TRASH_DIR_NAME = "trash"
_STAGING_DIR_NAME = ".install-staging"


@dataclass
class InstallResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    meta: PluginMeta | None = None
    installed_path: Path | None = None
    # Provenance: hash of the installed artifact bytes (zip or .py file) and,
    # for URL installs, where it came from. Recorded into PluginEntry so the
    # manager can distinguish registry installs and detect updates.
    sha256: str | None = None
    source_url: str | None = None


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _checksum_error(payload: bytes, expected_sha256: str | None) -> tuple[str, str | None]:
    """Return (actual_digest, error_or_None) for an expected-hash check."""
    actual = _digest(payload)
    if expected_sha256 is not None and actual != expected_sha256.lower():
        return actual, (
            f"checksum mismatch: expected sha256 {expected_sha256.lower()}, "
            f"got {actual} — refusing to install"
        )
    return actual, None


def _member_errors(zf: zipfile.ZipFile) -> list[str]:
    infos = zf.infolist()
    if len(infos) > MAX_ARCHIVE_MEMBERS:
        return [f"archive has {len(infos)} members (limit {MAX_ARCHIVE_MEMBERS})"]
    errors: list[str] = []
    total = 0
    for info in infos:
        name = info.filename
        path = Path(name)
        if path.is_absolute() or ".." in path.parts:
            errors.append(f"unsafe member path: {name!r}")
        if stat.S_ISLNK(info.external_attr >> 16):
            errors.append(f"symlink member rejected: {name!r}")
        total += info.file_size
    if total > MAX_TOTAL_UNCOMPRESSED_BYTES:
        errors.append(f"archive expands to {total} bytes (limit {MAX_TOTAL_UNCOMPRESSED_BYTES})")
    return errors


def _plugin_root(names: list[str]) -> tuple[str | None, str | None]:
    """Return (root_name, error): the single package dir or single .py file."""
    top_dirs = {n.split("/", 1)[0] for n in names if "/" in n}
    top_files = [n for n in names if "/" not in n and not n.endswith("/")]
    if len(top_dirs) == 1 and not top_files:
        root = next(iter(top_dirs))
        if f"{root}/__init__.py" not in names:
            return None, f"top-level directory {root!r} has no __init__.py"
        return root, None
    if not top_dirs and len(top_files) == 1 and top_files[0].endswith(".py"):
        return top_files[0], None
    return None, (
        "archive must contain exactly one plugin: a single top-level package "
        "directory or a single .py file"
    )


def install_from_zip(
    zip_path: Path,
    plugins_dir: Path,
    *,
    app_version: str | None = None,
    expected_sha256: str | None = None,
) -> InstallResult:
    zip_path = Path(zip_path)
    plugins_dir = Path(plugins_dir)
    try:
        digest, checksum_error = _checksum_error(zip_path.read_bytes(), expected_sha256)
    except OSError as exc:
        return InstallResult(ok=False, errors=[f"unreadable archive: {exc}"])
    if checksum_error is not None:
        return InstallResult(ok=False, errors=[checksum_error])
    try:
        zf = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile) as exc:
        return InstallResult(ok=False, errors=[f"not a readable zip archive: {exc}"])

    with zf:
        errors = _member_errors(zf)
        if errors:
            return InstallResult(ok=False, errors=errors)
        names = zf.namelist()
        root, root_error = _plugin_root(names)
        if root is None:
            return InstallResult(ok=False, errors=[root_error or "empty archive"])

        if root.lower() in RESERVED_DIR_NAMES or root.startswith(("_", ".")):
            return InstallResult(ok=False, errors=[f"plugin name {root!r} is reserved"])
        target = plugins_dir / root
        if target.exists():
            return InstallResult(
                ok=False,
                errors=[f"{root} is already installed — uninstall it first"],
            )

        staging = plugins_dir / _STAGING_DIR_NAME
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        try:
            zf.extractall(staging)  # members validated above
            candidate = staging / root
            report = validate_plugin(candidate, app_version=app_version)
            if not report.ok:
                return InstallResult(ok=False, errors=report.errors, warnings=report.warnings)
            plugins_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(candidate), str(target))
            return InstallResult(
                ok=True,
                warnings=report.warnings,
                meta=report.meta,
                installed_path=target,
                sha256=digest,
            )
        finally:
            shutil.rmtree(staging, ignore_errors=True)


def install_from_file(
    path: Path,
    plugins_dir: Path,
    *,
    app_version: str | None = None,
    expected_sha256: str | None = None,
) -> InstallResult:
    """Install a plugin from a local .zip archive or a single .py file."""
    path = Path(path)
    if path.suffix == ".zip":
        return install_from_zip(
            path, plugins_dir, app_version=app_version, expected_sha256=expected_sha256
        )
    if path.suffix == ".py" and path.is_file():
        digest, checksum_error = _checksum_error(path.read_bytes(), expected_sha256)
        if checksum_error is not None:
            return InstallResult(ok=False, errors=[checksum_error])
        report = validate_plugin(path, app_version=app_version)
        if not report.ok:
            return InstallResult(ok=False, errors=report.errors, warnings=report.warnings)
        target = Path(plugins_dir) / path.name
        if target.exists():
            return InstallResult(
                ok=False, errors=[f"{path.name} is already installed — uninstall it first"]
            )
        Path(plugins_dir).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        return InstallResult(
            ok=True,
            warnings=report.warnings,
            meta=report.meta,
            installed_path=target,
            sha256=digest,
        )
    return InstallResult(ok=False, errors=[f"{path} is not a .zip archive or .py file"])


def install_from_url(
    url: str,
    plugins_dir: Path,
    *,
    fetch: Callable[[str], bytes] | None = None,
    app_version: str | None = None,
    expected_sha256: str | None = None,
) -> InstallResult:
    """Download a plugin zip over https and install it.

    ``fetch`` is injectable so the UI can run the download on a worker
    thread (and tests can avoid the network). The default uses httpx with
    a bounded timeout. Registry installs pass ``expected_sha256`` — the
    reviewed artifact hash from the index — so a swapped download is
    refused before any of it is extracted or imported.
    """
    if not url.lower().startswith("https://"):
        return InstallResult(ok=False, errors=["only https:// URLs are allowed"])
    if fetch is None:

        def fetch(target_url: str) -> bytes:
            import httpx

            response = httpx.get(target_url, timeout=30.0, follow_redirects=True)
            response.raise_for_status()
            return response.content

    try:
        payload = fetch(url)
    except Exception as exc:
        return InstallResult(ok=False, errors=[f"download failed: {exc}"])
    if len(payload) > MAX_TOTAL_UNCOMPRESSED_BYTES:
        return InstallResult(ok=False, errors=["download exceeds the archive size limit"])

    plugins_dir = Path(plugins_dir)
    plugins_dir.mkdir(parents=True, exist_ok=True)
    tmp_zip = plugins_dir / _STAGING_DIR_NAME.replace("staging", "download.zip")
    try:
        tmp_zip.write_bytes(payload)
        result = install_from_zip(
            tmp_zip, plugins_dir, app_version=app_version, expected_sha256=expected_sha256
        )
        result.source_url = url
        return result
    finally:
        tmp_zip.unlink(missing_ok=True)


def uninstall(source_path: Path, plugins_dir: Path) -> str | None:
    """Move an installed plugin into plugins/trash/; return an error or None."""
    source_path = Path(source_path)
    plugins_dir = Path(plugins_dir)
    try:
        source_path.relative_to(plugins_dir)
    except ValueError:
        return f"{source_path} is not inside the plugins directory"
    if not source_path.exists():
        return f"{source_path} does not exist"
    trash = plugins_dir / TRASH_DIR_NAME
    trash.mkdir(parents=True, exist_ok=True)
    target = trash / source_path.name
    counter = 1
    while target.exists():
        target = trash / f"{source_path.name}.{counter}"
        counter += 1
    shutil.move(str(source_path), str(target))
    return None
