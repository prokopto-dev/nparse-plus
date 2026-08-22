"""``packaging/deb/build_deb.py`` — the Debian 12 package.

The generic Linux tarball is built on ``ubuntu-latest``, so its glibc floor is
that image's (2.39) and it does not start on Debian 12 (2.36). The ``.deb`` is
the same application built in a ``debian:12`` container. It is an ADDITIONAL
artifact — the tarball and the Flatpak are untouched by it — and these assert
the two properties that make that true: the filename cannot be picked up by
the updater, and the packaging cannot drift away from the Flatpak's.

``dpkg-deb`` is never invoked here, so these run on any platform.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "packaging" / "deb"))

import build_deb  # noqa: E402

FLATPAK_MANIFEST = REPO_ROOT / "packaging/flatpak/io.github.prokopto_dev.nparse_plus.yml"


@pytest.fixture
def staged(tmp_path: Path) -> Path:
    """A staged package tree over a stand-in for the PyInstaller onedir."""
    dist = tmp_path / "dist" / "nparseplus"
    (dist / "_internal").mkdir(parents=True)
    (dist / "nparseplus").write_text("#!/bin/sh\n", encoding="utf-8")
    (dist / "_internal" / "libpython.so").write_text("x", encoding="utf-8")
    root = tmp_path / "root"
    build_deb.stage(root, dist, "2.24.0", "2.36")
    return root


# --- the filename is the load-bearing decision -----------------------------


def test_the_filename_is_inert_to_the_deployed_updater() -> None:
    """Every already-released binary carries its own ``pick_asset``.

    That copy cannot be patched by this change, so the ``.deb`` has to be
    invisible to it by NAME: no ``-linux`` substring, and none of the suffixes
    any platform branch sweeps for. ``tests/test_updater.py`` asserts the
    other half — that a release carrying this name still picks correctly.
    """
    name = build_deb.deb_filename("2.24.0")
    assert name == "nparseplus_2.24.0_amd64.deb"
    assert "-linux" not in name
    for suffix in (".tar.gz", ".flatpak", ".zip", ".dmg"):
        assert not name.endswith(suffix)


# --- control ----------------------------------------------------------------


def test_control_carries_the_fields_dpkg_and_apt_need(staged: Path) -> None:
    control = (staged / "DEBIAN" / "control").read_text(encoding="utf-8")
    for field in ("Package: nparseplus", "Version: 2.24.0", "Architecture: amd64"):
        assert field in control
    assert "Section:" in control and "Maintainer:" in control
    # Installed-Size must be a real measurement, not the unrendered token.
    size = next(line for line in control.splitlines() if line.startswith("Installed-Size:"))
    assert int(size.split(":", 1)[1].strip()) > 0
    for token in ("@VERSION@", "@INSTALLED_SIZE@", "@GLIBC_FLOOR@"):
        assert token not in control, f"unrendered control.in token {token}"


def test_the_glibc_floor_is_declared(staged: Path) -> None:
    """The floor is the whole reason this package exists, so apt must enforce it.

    CI measures the real value with ``objdump`` over the bundled ELF files and
    passes it in; the default is bookworm's. Either way it lands in Depends,
    so apt refuses the install rather than letting it fail at exec time with
    the ``GLIBC_2.xx not found`` message that started all this.
    """
    control = (staged / "DEBIAN" / "control").read_text(encoding="utf-8")
    assert "libc6 (>= 2.36)" in control


def test_tts_is_recommended_not_required(staged: Path) -> None:
    """``audio/tts.py`` finds espeak on PATH; its absence degrades, not breaks."""
    control = (staged / "DEBIAN" / "control").read_text(encoding="utf-8")
    recommends = next(line for line in control.splitlines() if line.startswith("Recommends:"))
    assert "espeak-ng" in recommends
    depends_block = control.split("Depends:", 1)[1].split("Recommends:", 1)[0]
    assert "espeak" not in depends_block


# --- layout, and not drifting from the Flatpak ------------------------------


def test_the_launcher_is_a_bare_exec(staged: Path) -> None:
    """One source of truth for the Linux env defaults.

    ``app._runtime_env_defaults`` sets QT_QPA_PLATFORM and
    QTWEBENGINE_DISABLE_SANDBOX and never overrides a user's value. The
    Flatpak's launcher repeats them because the portal relaunch path can
    bypass it; nothing bypasses ``/usr/bin/nparseplus``, so repeating them
    here would only create a second copy to forget.
    """
    launcher = (staged / "usr/bin/nparseplus").read_text(encoding="utf-8")
    assert "exec /opt/nparseplus/nparseplus" in launcher
    # Comments (the shebang included) may NAME the variables — the wrapper
    # explains why it omits them. What must not appear is code that sets one.
    code = [
        line for line in launcher.splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]
    assert code == ['exec /opt/nparseplus/nparseplus "$@"']


def test_the_desktop_entry_is_the_flatpak_one_verbatim(staged: Path) -> None:
    """Copied, not forked — the two packagings cannot describe the app differently."""
    installed = staged / f"usr/share/applications/{build_deb.APP_ID}.desktop"
    source = REPO_ROOT / f"packaging/flatpak/{build_deb.APP_ID}.desktop"
    assert installed.read_bytes() == source.read_bytes()
    entry = installed.read_text(encoding="utf-8")
    # Exec resolves through /usr/bin, Icon through the hicolor names below.
    assert "Exec=nparseplus" in entry
    assert f"Icon={build_deb.APP_ID}" in entry


def test_the_icon_set_matches_the_flatpak_manifest(staged: Path) -> None:
    """Same sizes from the same sources, so a new render reaches both packagings.

    hicolor has no 24px directory, which is why ``icon-24.png`` — the Windows
    small-icon step — is the one size ``tools/gen_icons.py`` produces that
    neither packaging installs.
    """
    manifest = FLATPAK_MANIFEST.read_text(encoding="utf-8")
    for size, source in build_deb.ICON_SIZES.items():
        installed = staged / f"usr/share/icons/hicolor/{size}/apps/{build_deb.APP_ID}.png"
        assert installed.is_file(), f"missing {size} icon"
        assert (REPO_ROOT / source).is_file()
        assert f"hicolor/{size}/apps/{build_deb.APP_ID}.png" in manifest
    assert (staged / f"usr/share/icons/hicolor/scalable/apps/{build_deb.APP_ID}.svg").is_file()
    assert "24x24" not in "".join(build_deb.ICON_SIZES)


def test_the_payload_lands_under_opt(staged: Path) -> None:
    assert (staged / "opt/nparseplus/nparseplus").is_file()
    assert (staged / "opt/nparseplus/_internal/libpython.so").is_file()
    assert (staged / "usr/share/doc/nparseplus/copyright").is_file()


def test_maintainer_scripts_never_fail_the_install(staged: Path) -> None:
    """Desktop/icon caches are a nicety; a headless box has neither tool."""
    for name in ("postinst", "postrm"):
        script = staged / "DEBIAN" / name
        assert script.stat().st_mode & 0o111, f"{name} must be executable"
        body = script.read_text(encoding="utf-8")
        assert "command -v" in body and "|| true" in body
