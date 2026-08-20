#!/usr/bin/env python
"""Read the PyInstaller pins out of ``uv.lock`` for the Windows release job.

The Windows build installs PyInstaller from its **source distribution** with
``PYINSTALLER_COMPILE_BOOTLOADER=1`` so the bootloader is compiled on the
runner rather than shipped as the prebuilt binary that is byte-identical for
every PyInstaller user on earth — the bytes heuristic antivirus engines
actually match on (#122).

That override cannot go through ``uv sync``: the lock is shared with the macOS
and Linux jobs and there is no reason to churn platforms that are not
affected. So the Windows job runs a second, narrow ``uv pip install`` — and
this script is what keeps that install pinned to exactly what the lock already
resolved, hash and all, instead of to whatever PyPI serves that day:

    uv run python tools/pyinstaller_source_pin.py requirements -o pyi-source.txt
    uv pip install --no-deps --reinstall-package pyinstaller \\
        --no-binary pyinstaller --require-hashes -r pyi-source.txt

``wheel`` emits the pin for the *prebuilt* wheel the lock would otherwise have
installed. CI downloads it to prove the rebuild really happened: the sdist
**also ships the prebuilt bootloaders** (only ``PyInstaller/bootloader/Linux-*``
is excluded from it), so a source install without
``PYINSTALLER_COMPILE_BOOTLOADER`` set silently packages the very bytes this is
meant to replace and looks exactly like success.

Stdlib only — it runs before anything else is guaranteed importable.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCK = REPO_ROOT / "uv.lock"

PACKAGE = "pyinstaller"


def load_package(lock_path: Path = LOCK, name: str = PACKAGE) -> dict:
    """The lock's entry for ``name``. Exits non-zero if it is not there."""
    try:
        data = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    except OSError as exc:  # pragma: no cover - only on a broken checkout
        raise SystemExit(f"error: cannot read {lock_path}: {exc}") from exc
    for package in data.get("package", []):
        if package.get("name") == name:
            return package
    raise SystemExit(f"error: {name} is not in {lock_path}")


def sdist_pin(package: dict) -> tuple[str, str, str]:
    """``(version, url, sha256)`` of the locked source distribution."""
    version = package.get("version")
    sdist = package.get("sdist")
    if not version or not sdist:
        raise SystemExit(
            f"error: {package.get('name')} has no sdist in the lock — the Windows "
            "release job cannot rebuild the bootloader from a wheel-only pin"
        )
    return version, sdist["url"], _sha256(sdist)


def wheel_pin(package: dict, tag: str) -> tuple[str, str]:
    """``(url, sha256)`` of the locked wheel whose filename carries ``tag``."""
    suffix = f"-{tag}.whl"
    matches = [w for w in package.get("wheels", []) if w["url"].endswith(suffix)]
    if not matches:
        raise SystemExit(f"error: no {package.get('name')} wheel matching *{suffix} in the lock")
    if len(matches) > 1:  # pragma: no cover - one tag, one wheel per version
        raise SystemExit(f"error: {len(matches)} wheels match *{suffix} in the lock")
    return matches[0]["url"], _sha256(matches[0])


def _sha256(entry: dict) -> str:
    digest = entry.get("hash", "")
    if not digest.startswith("sha256:"):
        raise SystemExit(f"error: {entry.get('url')} is not sha256-pinned in the lock")
    return digest.removeprefix("sha256:")


def requirements_text(package: dict) -> str:
    """A ``--require-hashes``-compatible requirements file, one pin."""
    version, _url, digest = sdist_pin(package)
    return f"{package['name']}=={version} \\\n    --hash=sha256:{digest}\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("version", help="print the locked PyInstaller version")
    req = sub.add_parser("requirements", help="write a hash-pinned requirements file for the sdist")
    req.add_argument(
        "-o",
        "--output",
        type=Path,
        help="write here instead of stdout (avoids shell redirection encoding)",
    )
    wheel = sub.add_parser("wheel", help="print '<url> <sha256>' for the prebuilt wheel")
    wheel.add_argument("tag", help="platform tag, e.g. win_amd64")
    args = parser.parse_args(argv)

    package = load_package()
    if args.command == "version":
        print(sdist_pin(package)[0])
    elif args.command == "requirements":
        text = requirements_text(package)
        if args.output:
            args.output.write_text(text, encoding="utf-8")
        else:
            sys.stdout.write(text)
    else:
        url, digest = wheel_pin(package, args.tag)
        print(f"{url} {digest}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
