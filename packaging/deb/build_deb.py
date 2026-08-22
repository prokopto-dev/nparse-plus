#!/usr/bin/env python3
"""Package the PyInstaller onedir build as a Debian .deb.

Run after ``pyinstaller packaging/nparseplus.spec``::

    python packaging/deb/build_deb.py --dist-dir dist/nparseplus \
        --version 2.24.0 --outdir dist

**Why this package exists.** The generic Linux tarball is built on the
``ubuntu-latest`` runner, so its glibc floor is whatever that image ships
(2.39 today) and it will not start on Debian 12 (2.36). This artifact is the
same application built inside a ``debian:12`` container, so its floor is
bookworm's. It is a SEPARATE artifact: the tarball and the Flatpak are
untouched by it.

**The filename is load-bearing.** ``updater.pick_asset`` finds the Linux
tarball with ``"-linux" in name and name.endswith(".tar.gz")``, taking the
first match -- and that predicate ships inside every already-released binary,
so it cannot be fixed retroactively for anyone. A new release asset must
therefore be inert to it. ``nparseplus_<version>_amd64.deb`` (the Debian
convention) contains no ``-linux`` substring and matches no suffix
``pick_asset`` looks for, on any platform branch. See #160 for the bug that
happened the last time two artifacts shared a predicate.

Stdlib only, and the staging half is importable without ``dpkg-deb`` so
``tests/test_deb_packaging.py`` can assert the layout and the control fields
on any platform.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEB_DIR = Path(__file__).resolve().parent
FLATPAK_DIR = REPO_ROOT / "packaging" / "flatpak"

PACKAGE = "nparseplus"
ARCH = "amd64"

# The Flatpak's app id. The .deb installs the Flatpak's .desktop file VERBATIM
# rather than forking it, so the icon basenames have to match that file's
# ``Icon=`` line. One source of truth; the two packagings cannot drift.
APP_ID = "io.github.prokopto_dev.nparse_plus"

# Where the onedir tree lands. /opt keeps a self-contained bundle out of the
# way of the distribution's own /usr layout, which is what Debian policy asks
# of a package that ships its own Python and Qt.
INSTALL_ROOT = "/opt/nparseplus"
LAUNCHER = "/usr/bin/nparseplus"

# Exactly the icon set the Flatpak manifest installs, from the same sources:
# tools/gen_icons.py renders real art at each step from data/assets/icon.svg,
# so these are not resamples of one PNG. hicolor has no 24px directory, which
# is why icon-24.png (Windows' small-icon step) is the one generated size that
# does not appear here -- same omission the Flatpak makes.
ICON_SIZES = {
    "16x16": "data/ui/icon-16.png",
    "32x32": "data/ui/icon-32.png",
    "48x48": "data/ui/icon-48.png",
    "64x64": "data/ui/icon-64.png",
    "128x128": "data/ui/icon-128.png",
    "256x256": "data/ui/icon.png",
}
SVG_SOURCE = "data/assets/icon.svg"

# Fallback only. CI measures the real floor with objdump over the bundled ELF
# files and passes it in, so a dependency that quietly raises it is caught at
# build time instead of by a user. Debian 12 ships 2.36.
DEFAULT_GLIBC_FLOOR = "2.36"

MAINTAINER_SCRIPT = """\
#!/bin/sh
set -e
if [ "$1" = "{action}" ]; then
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q /usr/share/applications || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
    fi
fi
exit 0
"""


def deb_filename(version: str) -> str:
    """The published asset name.

    Must stay inert to ``updater.pick_asset`` -- see the module docstring.
    ``tests/test_deb_packaging.py`` asserts that directly.
    """
    return f"{PACKAGE}_{version}_{ARCH}.deb"


def render_control(version: str, installed_size_kb: int, glibc_floor: str) -> str:
    """Fill control.in. Kept separate from staging so tests can read it."""
    template = (DEB_DIR / "control.in").read_text(encoding="utf-8")
    return (
        template.replace("@VERSION@", version)
        .replace("@INSTALLED_SIZE@", str(installed_size_kb))
        .replace("@GLIBC_FLOOR@", glibc_floor)
    )


def _tree_size_kb(root: Path) -> int:
    total = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
    return max(1, total // 1024)


def stage(root: Path, dist_dir: Path, version: str, glibc_floor: str) -> Path:
    """Build the package tree under ``root``. No dpkg-deb involved."""
    payload = root / INSTALL_ROOT.lstrip("/")
    payload.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(dist_dir, payload)

    launcher = root / LAUNCHER.lstrip("/")
    launcher.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(DEB_DIR / "nparseplus", launcher)
    launcher.chmod(0o755)

    desktop = root / "usr/share/applications" / f"{APP_ID}.desktop"
    desktop.parent.mkdir(parents=True, exist_ok=True)
    # Verbatim, not a copy with edits: Exec=nparseplus resolves through
    # /usr/bin/nparseplus and Icon=<app id> through the hicolor names below.
    shutil.copy2(FLATPAK_DIR / f"{APP_ID}.desktop", desktop)
    desktop.chmod(0o644)

    for size, source in ICON_SIZES.items():
        target = root / f"usr/share/icons/hicolor/{size}/apps/{APP_ID}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / source, target)
        target.chmod(0o644)

    svg = root / f"usr/share/icons/hicolor/scalable/apps/{APP_ID}.svg"
    svg.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO_ROOT / SVG_SOURCE, svg)
    svg.chmod(0o644)

    doc = root / "usr/share/doc" / PACKAGE / "copyright"
    doc.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(DEB_DIR / "copyright", doc)
    doc.chmod(0o644)

    debian = root / "DEBIAN"
    debian.mkdir(parents=True, exist_ok=True)
    control = render_control(version, _tree_size_kb(root), glibc_floor)
    (debian / "control").write_text(control, encoding="utf-8")
    for name, action in (("postinst", "configure"), ("postrm", "remove")):
        script = debian / name
        script.write_text(MAINTAINER_SCRIPT.format(action=action), encoding="utf-8")
        script.chmod(0o755)
    return root


def build(dist_dir: Path, version: str, outdir: Path, glibc_floor: str) -> Path:
    staging = outdir / f"{PACKAGE}_{version}_deb_root"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    stage(staging, dist_dir, version, glibc_floor)

    out = outdir / deb_filename(version)
    # --root-owner-group avoids needing fakeroot (dpkg >= 1.19); the container
    # builds as root anyway, but this keeps a local build reproducible.
    subprocess.run(
        ["dpkg-deb", "--build", "--root-owner-group", "-Zxz", str(staging), str(out)],
        check=True,
    )
    shutil.rmtree(staging)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--outdir", type=Path, default=REPO_ROOT / "dist")
    parser.add_argument("--glibc-floor", default=DEFAULT_GLIBC_FLOOR)
    args = parser.parse_args(argv)

    if not (args.dist_dir / PACKAGE).is_file():
        parser.error(f"{args.dist_dir} does not look like a PyInstaller onedir build")
    args.outdir.mkdir(parents=True, exist_ok=True)
    out = build(args.dist_dir, args.version, args.outdir, args.glibc_floor)
    print(f"built {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
