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
- URL installs are https-only *on every hop* and streamed against a byte
  budget; the byte fetch is injectable so the UI can route it through a
  worker thread.
- Uninstall moves the plugin into ``plugins/trash/`` rather than deleting.
  The host pairs it with ``PluginHost.forget`` so the consent record and the
  plugin's private data go with it.
- Updating (``replace=``) is the same pipeline with the already-installed
  refusal inverted: the new code must validate *and* prove it is the same
  ``meta.id`` installed at the same path before the old copy is moved aside.
  A failure at any point leaves the working copy in place, and — unlike
  uninstall — consent and ``plugin-data/`` are deliberately untouched.

Note: validation imports the plugin, so its module-level code runs at
install time — the same trust boundary as running the plugin. The manager
UI states this next to the install buttons.
"""

from __future__ import annotations

import hashlib
import shutil
import stat
import sys
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nparseplus.core.plugins.discovery import is_reserved_name
from nparseplus_sdk import PluginMeta
from nparseplus_sdk.loading import MODULE_NAMESPACE
from nparseplus_sdk.validate import validate_plugin

MAX_ARCHIVE_MEMBERS = 2000
MAX_TOTAL_UNCOMPRESSED_BYTES = 50 * 1024 * 1024  # 50 MiB
MAX_REDIRECTS = 5
TRASH_DIR_NAME = "trash"
# Uninstalled plugin data is parked beside the uninstalled code, not deleted.
PLUGIN_DATA_TRASH_NAME = "plugin-data"
_STAGING_DIR_NAME = ".install-staging"
# The old copy is parked here between "new copy validated" and "new copy in
# place", then moved on to trash/. Dot-prefixed, so discover_dir_plugins
# skips it even if a crash leaves one behind.
_BACKUP_DIR_NAME = ".install-backup"
_COPY_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True)
class ReplaceTarget:
    """Permission to install over one specific installed plugin.

    Updating is the same pipeline as installing with one gate inverted, so it
    is a parameter rather than a second entry point. Both fields are checked,
    and at different points: ``installed_path`` before anything is extracted,
    ``plugin_id`` only after validation, because ``meta.id`` does not exist
    until the candidate has been imported.
    """

    plugin_id: str
    installed_path: Path


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
    # Where the replaced copy was parked, for an update. The UI tells the user,
    # because the index carries only `latest` — trash/ is the only way back.
    replaced_path: Path | None = None


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


def fetch_https_bytes(
    url: str,
    *,
    timeout: float,
    max_bytes: int,
    transport: Any = None,
) -> bytes:
    """GET an https URL into memory, bounded in both scheme and size.

    Redirects are followed by hand instead of by httpx: we keep legitimate
    hops (release artifacts routinely bounce to a CDN) but re-assert https
    on every one of them, so an https URL that 302s to http is refused
    rather than silently downloaded in plaintext — which is what
    ``follow_redirects=True`` did. The body is streamed and aborted the
    moment it passes ``max_bytes``, so an endless response can't be
    buffered whole before anyone checks its length.

    ``transport`` is an httpx transport seam for tests; production passes
    None. Raises on any refusal — callers turn that into a user message.
    """
    import httpx

    with httpx.Client(timeout=timeout, follow_redirects=False, transport=transport) as client:
        for _ in range(MAX_REDIRECTS + 1):
            if not url.lower().startswith("https://"):
                raise ValueError(f"refusing non-https URL {url!r}")
            with client.stream("GET", url) as response:
                if response.is_redirect:
                    location = response.headers.get("location", "")
                    if not location:
                        raise ValueError("redirect without a Location header")
                    url = str(response.url.join(location))
                    continue
                response.raise_for_status()
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError(f"response exceeds the {max_bytes} byte limit")
                    chunks.append(chunk)
                return b"".join(chunks)
    raise ValueError(f"too many redirects (limit {MAX_REDIRECTS})")


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
    # Declared sizes are attacker-controlled, so this is only a cheap early
    # reject; _extract_limited re-enforces the cap on bytes actually written.
    if total > MAX_TOTAL_UNCOMPRESSED_BYTES:
        errors.append(f"archive expands to {total} bytes (limit {MAX_TOTAL_UNCOMPRESSED_BYTES})")
    return errors


def _extract_limited(zf: zipfile.ZipFile, staging: Path, budget: int) -> str | None:
    """Extract every member under a hard output budget; return an error or None.

    Replaces ``extractall`` because that trusts the archive twice over: it
    writes a member to disk before its CRC/size mismatch is discovered, and
    the only size the pre-check can see is the one the archive declares. Here
    the budget counts bytes as they are written, so a member that expands
    past what it declared is cut off mid-write rather than after.

    Member paths were validated by ``_member_errors``; the containment check
    below is belt-and-braces against a path that survives that screen.
    """
    staging_root = staging.resolve()
    written = 0
    try:
        for info in zf.infolist():
            target = (staging / info.filename).resolve()
            if not target.is_relative_to(staging_root):
                return f"unsafe member path: {info.filename!r}"
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as source, target.open("wb") as sink:
                while chunk := source.read(_COPY_CHUNK_BYTES):
                    written += len(chunk)
                    if written > budget:
                        return f"archive expands past {budget} bytes during extraction"
                    sink.write(chunk)
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        # BadZipFile covers a member whose real content disagrees with its
        # declared size or CRC; RuntimeError covers encrypted archives.
        return f"archive member could not be extracted: {exc}"
    return None


@contextmanager
def _preserved_plugin_modules(stem: str) -> Iterator[None]:
    """Undo what validating a candidate does to an already-loaded plugin.

    ``import_plugin_module`` keys ``sys.modules`` by the plugin's *path stem*
    (``nparseplus_user_plugins.<stem>``), and ``validate_plugin`` imports the
    candidate to check it. So validating a copy whose stem matches a plugin
    the app already loaded rebinds that key to a module whose ``__file__`` and
    ``__path__`` point into the staging directory — which is deleted the
    moment validation finishes. The live plugin object survives (the host
    holds it), but its next relative import resolves through the rebound entry
    into a directory that is gone, and any module-level singleton has silently
    forked into two copies.

    So snapshot the namespace, let validation do what it likes, then drop
    whatever it added and restore what was there. This is not a sandbox: the
    candidate's module-level code and its ``activate()`` still ran in this
    process, which is the trust boundary installing already has (see the
    module docstring). It restores the *import namespace*, nothing more.
    """
    prefix = f"{MODULE_NAMESPACE}.{stem}"

    def matching() -> list[str]:
        return [name for name in sys.modules if name == prefix or name.startswith(f"{prefix}.")]

    saved = {name: sys.modules[name] for name in matching()}
    try:
        yield
    finally:
        for name in matching():
            sys.modules.pop(name, None)
        sys.modules.update(saved)


def _module_stem(name: str) -> str:
    """The ``sys.modules`` stem a plugin path would import under.

    Mirrors ``import_plugin_module``'s ``path.stem if path.is_dir() else
    entry.stem`` for a root name that may or may not carry a ``.py`` suffix.
    """
    return name[:-3] if name.endswith(".py") else name


def _replace_target_error(replace: ReplaceTarget, plugins_dir: Path, target: Path) -> str | None:
    """Screen a replace request before anything is extracted; None if usable.

    Three separate things can be wrong, and conflating them produces an error
    nobody can act on, so each says exactly what it found. The identity check
    that matters most — that the archive really *is* this plugin — cannot run
    here; see ``_replace_identity_error``.
    """
    installed = Path(replace.installed_path)
    try:
        installed.relative_to(plugins_dir)
    except ValueError:
        return f"{installed} is not inside the plugins directory"
    if not installed.exists():
        return (
            f"the installed copy of {replace.plugin_id} is no longer at {installed} — "
            "install it fresh instead of updating"
        )
    if target != installed:
        # The install path comes from the archive root, never from meta.id, so
        # an archive that renames its root would install a SECOND copy beside
        # the old one under the same id. Refuse, and name both.
        return (
            f"this archive installs as {target.name!r} but the installed copy is "
            f"{installed.name!r} — that is a new plugin, not an update to this one"
        )
    return None


def _replace_identity_error(replace: ReplaceTarget, meta: PluginMeta | None) -> str | None:
    """Refuse an archive that validated as a *different* plugin.

    This is the check that stops an update from being an identity takeover: a
    plugin installed over ``replace.plugin_id`` inherits that id's consent
    record and its ``plugin-data/`` directory. It has to run after
    ``validate_plugin``, because nothing before that knows ``meta.id``.
    """
    if meta is None:
        return "the archive did not produce readable plugin metadata"
    if meta.id != replace.plugin_id:
        return (
            f"this archive is plugin {meta.id!r}, not {replace.plugin_id!r} — "
            "refusing to install it over a different plugin"
        )
    return None


def _swap_in(candidate: Path, target: Path, plugins_dir: Path) -> tuple[Path | None, str | None]:
    """Move ``candidate`` onto ``target``, keeping the old copy recoverable.

    Returns ``(trashed_path, error)``. The old copy goes to a private backup
    sibling first and only reaches ``trash/`` once the new copy is in place:
    both moves are same-filesystem renames inside ``plugins_dir``, so the
    window where ``target`` does not exist is a rename apart, and a rollback
    never has to reason about a numbered public trash slot the user may
    already be looking at.

    A failure to trash the backup afterwards is deliberately not an error —
    the update itself succeeded, and stranding a ``.install-backup`` entry is
    a worse answer than reporting a failure that did not happen.
    """
    backup_root = plugins_dir / _BACKUP_DIR_NAME
    shutil.rmtree(backup_root, ignore_errors=True)
    backup_root.mkdir(parents=True, exist_ok=True)
    backup = backup_root / target.name
    try:
        shutil.move(str(target), str(backup))
    except OSError as exc:
        shutil.rmtree(backup_root, ignore_errors=True)
        return None, f"could not set the installed copy aside: {exc}"
    try:
        shutil.move(str(candidate), str(target))
    except OSError as exc:
        try:
            shutil.move(str(backup), str(target))
        except OSError as restore_exc:  # pragma: no cover - both renames failing
            return None, (
                f"could not install the new version ({exc}) and could not restore "
                f"the old one ({restore_exc}) — it is in {backup}"
            )
        shutil.rmtree(backup_root, ignore_errors=True)
        return None, f"could not install the new version: {exc}"
    trashed: Path | None = None
    try:
        trashed = _trash_slot(plugins_dir / TRASH_DIR_NAME, target.name)
        shutil.move(str(backup), str(trashed))
    except OSError:
        trashed = None
    shutil.rmtree(backup_root, ignore_errors=True)
    return trashed, None


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
    replace: ReplaceTarget | None = None,
) -> InstallResult:
    """Install a plugin zip, optionally replacing an installed copy in place.

    With ``replace``, the old code is moved to ``trash/`` only after the new
    code has validated *and* proved it is the same plugin. Nothing else about
    the plugin is touched: its consent record and its ``plugin-data/`` survive,
    which is the whole difference between updating and uninstall+reinstall.
    """
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

        if is_reserved_name(root) or root.startswith(("_", ".")):
            return InstallResult(ok=False, errors=[f"plugin name {root!r} is reserved"])
        target = plugins_dir / root
        if replace is None:
            if target.exists():
                return InstallResult(
                    ok=False,
                    errors=[f"{root} is already installed — update it, or uninstall it first"],
                )
        else:
            replace_error = _replace_target_error(replace, plugins_dir, target)
            if replace_error is not None:
                return InstallResult(ok=False, errors=[replace_error])

        staging = plugins_dir / _STAGING_DIR_NAME
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        try:
            extract_error = _extract_limited(zf, staging, MAX_TOTAL_UNCOMPRESSED_BYTES)
            if extract_error is not None:
                return InstallResult(ok=False, errors=[extract_error])
            candidate = staging / root
            with _preserved_plugin_modules(_module_stem(root)):
                report = validate_plugin(candidate, app_version=app_version)
            if not report.ok:
                return InstallResult(ok=False, errors=report.errors, warnings=report.warnings)
            plugins_dir.mkdir(parents=True, exist_ok=True)
            if replace is None:
                shutil.move(str(candidate), str(target))
                trashed = None
            else:
                identity_error = _replace_identity_error(replace, report.meta)
                if identity_error is not None:
                    return InstallResult(
                        ok=False, errors=[identity_error], warnings=report.warnings
                    )
                trashed, swap_error = _swap_in(candidate, target, plugins_dir)
                if swap_error is not None:
                    return InstallResult(ok=False, errors=[swap_error], warnings=report.warnings)
            return InstallResult(
                ok=True,
                warnings=report.warnings,
                meta=report.meta,
                installed_path=target,
                sha256=digest,
                replaced_path=trashed,
            )
        finally:
            shutil.rmtree(staging, ignore_errors=True)


def install_from_file(
    path: Path,
    plugins_dir: Path,
    *,
    app_version: str | None = None,
    expected_sha256: str | None = None,
    replace: ReplaceTarget | None = None,
) -> InstallResult:
    """Install a plugin from a local .zip archive or a single .py file."""
    path = Path(path)
    plugins_dir = Path(plugins_dir)
    if path.suffix == ".zip":
        return install_from_zip(
            path,
            plugins_dir,
            app_version=app_version,
            expected_sha256=expected_sha256,
            replace=replace,
        )
    if path.suffix == ".py" and path.is_file():
        # The zip path screens the name before extracting; this one never did,
        # so a file called `trash.py` (or `.hidden.py`) could land in the
        # plugins folder and shadow a reserved directory.
        if is_reserved_name(path.stem) or path.name.startswith(("_", ".")):
            return InstallResult(ok=False, errors=[f"plugin name {path.name!r} is reserved"])
        digest, checksum_error = _checksum_error(path.read_bytes(), expected_sha256)
        if checksum_error is not None:
            return InstallResult(ok=False, errors=[checksum_error])
        target = plugins_dir / path.name
        # Screen the replace request BEFORE validation, matching the zip path:
        # a refusable update should not run the candidate's module code.
        if replace is not None:
            replace_error = _replace_target_error(replace, plugins_dir, target)
            if replace_error is not None:
                return InstallResult(ok=False, errors=[replace_error])
        with _preserved_plugin_modules(path.stem):
            report = validate_plugin(path, app_version=app_version)
        if not report.ok:
            return InstallResult(ok=False, errors=report.errors, warnings=report.warnings)
        if replace is None:
            if target.exists():
                return InstallResult(
                    ok=False,
                    errors=[f"{path.name} is already installed — update it, or uninstall it first"],
                )
        else:
            identity_error = _replace_identity_error(replace, report.meta)
            if identity_error is not None:
                return InstallResult(ok=False, errors=[identity_error], warnings=report.warnings)
        plugins_dir.mkdir(parents=True, exist_ok=True)
        trashed: Path | None = None
        if replace is None:
            shutil.copyfile(path, target)
        else:
            # copyfile, not move: the source is the user's own file, which an
            # update has no business consuming. Stage a copy, then swap.
            staging = plugins_dir / _STAGING_DIR_NAME
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True, exist_ok=True)
            try:
                candidate = staging / path.name
                shutil.copyfile(path, candidate)
                trashed, swap_error = _swap_in(candidate, target, plugins_dir)
                if swap_error is not None:
                    return InstallResult(ok=False, errors=[swap_error], warnings=report.warnings)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
        return InstallResult(
            ok=True,
            warnings=report.warnings,
            meta=report.meta,
            installed_path=target,
            sha256=digest,
            replaced_path=trashed,
        )
    return InstallResult(ok=False, errors=[f"{path} is not a .zip archive or .py file"])


def install_from_url(
    url: str,
    plugins_dir: Path,
    *,
    fetch: Callable[[str], bytes] | None = None,
    app_version: str | None = None,
    expected_sha256: str | None = None,
    replace: ReplaceTarget | None = None,
) -> InstallResult:
    """Download a plugin zip over https and install it.

    ``fetch`` is injectable so the UI can run the download on a worker
    thread (and tests can avoid the network). The default streams through
    ``fetch_https_bytes``: https on every redirect hop, size-capped as it
    arrives. Registry installs pass ``expected_sha256`` — the reviewed
    artifact hash from the index — so a swapped download is refused before
    any of it is extracted or imported.
    """
    if not url.lower().startswith("https://"):
        return InstallResult(ok=False, errors=["only https:// URLs are allowed"])
    if fetch is None:

        def fetch(target_url: str) -> bytes:
            return fetch_https_bytes(
                target_url, timeout=30.0, max_bytes=MAX_TOTAL_UNCOMPRESSED_BYTES
            )

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
            tmp_zip,
            plugins_dir,
            app_version=app_version,
            expected_sha256=expected_sha256,
            replace=replace,
        )
        result.source_url = url
        return result
    finally:
        tmp_zip.unlink(missing_ok=True)


def _trash_slot(trash: Path, name: str) -> Path:
    """A free path under ``trash`` for ``name`` (numbered if it's taken)."""
    trash.mkdir(parents=True, exist_ok=True)
    target = trash / name
    counter = 1
    while target.exists():
        target = trash / f"{name}.{counter}"
        counter += 1
    return target


def uninstall(source_path: Path, plugins_dir: Path) -> str | None:
    """Move an installed plugin into plugins/trash/; return an error or None.

    This only handles the code. Forgetting the plugin — its consent record
    and its private data — is ``PluginHost.forget``, which the manager UI
    calls with the id of whatever it just uninstalled.
    """
    source_path = Path(source_path)
    plugins_dir = Path(plugins_dir)
    try:
        source_path.relative_to(plugins_dir)
    except ValueError:
        return f"{source_path} is not inside the plugins directory"
    if not source_path.exists():
        return f"{source_path} does not exist"
    shutil.move(str(source_path), str(_trash_slot(plugins_dir / TRASH_DIR_NAME, source_path.name)))
    return None


def trash_plugin_data(data_dir: Path, plugins_dir: Path) -> str | None:
    """Move a plugin's private data dir into plugins/trash/plugin-data/.

    Same recoverable-not-deleted contract as ``uninstall``: the bytes stay
    around for a user who uninstalled by mistake, but they are out of the
    live data directory, so a later plugin claiming the same id starts with
    empty storage instead of inheriting its predecessor's.
    """
    data_dir = Path(data_dir)
    if not data_dir.exists():
        return None
    trash = Path(plugins_dir) / TRASH_DIR_NAME / PLUGIN_DATA_TRASH_NAME
    try:
        shutil.move(str(data_dir), str(_trash_slot(trash, data_dir.name)))
    except OSError as exc:
        return f"could not move plugin data aside: {exc}"
    return None
