#!/usr/bin/env python3
"""Check the PyInstaller bundle's shared-library closure before packaging it.

**The failure this exists to catch.** PyInstaller bundles whatever ``ldd``
resolves *at build time*. A library merely absent from the build container is
not bundled and the build still SUCCEEDS — the artifact silently loses
whatever needed it. The first Debian 12 build hit exactly that: the container
had no ``libxkbfile1``, so every QtWebEngine binary came out with an
unresolved ``libxkbfile.so.1`` and the Discord overlay would have been gone
with nothing to show for it.

**Why it is not simply "no unresolved dependencies anywhere".** PySide6 ships
plugins for Qt modules this app never loads — TIFF images, Wayland, GTK
theming, PulseAudio multimedia — and one of those failing to load costs
nothing. ``libtiff.so.5`` cannot even be satisfied on bookworm, which ships
``libtiff.so.6``; the Ubuntu tarball has carried that same unresolved entry
its whole life without anyone noticing, because nothing loads the TIFF image
plugin. A gate that fails on those is a gate that gets disabled.

So: unresolved dependencies of the binaries the app genuinely needs are
FATAL, and everything else is reported. The report is the point — it is how a
new gap gets noticed and either fixed or added to ``KNOWN_ABSENT`` on
purpose.

Stdlib only, and the classification is a pure function so
``tests/test_deb_packaging.py`` can exercise it without ``ldd``.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

# Unresolved dependencies of these are fatal: the app does not work without
# them. Matched on basename, since the bundle's layout is PyInstaller's.
CRITICAL = frozenset(
    {
        # The launcher itself.
        "nparseplus",
        # Core Qt. Nothing renders without these.
        "libQt6Core.so.6",
        "libQt6Gui.so.6",
        "libQt6Widgets.so.6",
        "libQt6Network.so.6",
        "libQt6DBus.so.6",
        # The platform plugin the app pins itself to (app._runtime_env_defaults
        # sets QT_QPA_PLATFORM=xcb). Its Wayland sibling is deliberately NOT
        # here — the overlays cannot stay on top under native Wayland, which is
        # why the app does not use it.
        "libqxcb.so",
        # QtWebEngine — the Discord overlay, and the reason this check exists.
        "QtWebEngineCore.abi3.so",
        "QtWebEngineWidgets.abi3.so",
        "libQt6WebEngineCore.so.6",
        "libQt6WebEngineWidgets.so.6",
        "QtWebEngineProcess",
    }
)

# Unresolved dependencies we have looked at and accepted, with the reason.
# Anything unresolved and NOT listed here still gets reported — this map only
# annotates the report, it never suppresses a line.
KNOWN_ABSENT = {
    "libtiff.so.5": (
        "bookworm ships libtiff.so.6; the Qt TIFF image plugin is never "
        "loaded, and the Ubuntu tarball has the same unresolved entry"
    ),
}


def missing_libraries(path: Path) -> list[str]:
    """SONAMEs ``ldd`` cannot resolve for one file ([] for a non-ELF file)."""
    try:
        proc = subprocess.run(["ldd", str(path)], capture_output=True, text=True, check=False)
    except OSError:
        return []
    return sorted(
        {line.split("=>")[0].strip() for line in proc.stdout.splitlines() if "not found" in line}
    )


def scan(dist_dir: Path) -> dict[str, list[str]]:
    """Every bundled ELF with an unresolved dependency, path -> SONAMEs."""
    found: dict[str, list[str]] = {}
    for path in sorted(dist_dir.rglob("*")):
        if not path.is_file():
            continue
        name = path.name
        if not (name == "nparseplus" or ".so" in name or name.startswith("Qt")):
            continue
        if missing := missing_libraries(path):
            found[str(path.relative_to(dist_dir))] = missing
    return found


def classify(findings: dict[str, list[str]]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Split findings into (fatal, tolerated) on the CRITICAL basenames."""
    fatal = {p: libs for p, libs in findings.items() if Path(p).name in CRITICAL}
    tolerated = {p: libs for p, libs in findings.items() if Path(p).name not in CRITICAL}
    return fatal, tolerated


def report(fatal: dict[str, list[str]], tolerated: dict[str, list[str]]) -> str:
    lines: list[str] = []
    if tolerated:
        sonames = sorted({lib for libs in tolerated.values() for lib in libs})
        lines.append(f"Unresolved in non-essential components ({len(tolerated)} files):")
        for soname in sonames:
            why = KNOWN_ABSENT.get(soname)
            lines.append(f"  {soname}" + (f"  — accepted: {why}" if why else ""))
        lines.append("")
    if fatal:
        lines.append("UNRESOLVED IN COMPONENTS THE APP NEEDS:")
        for path, libs in sorted(fatal.items()):
            lines.append(f"  {path}: {', '.join(libs)}")
        lines.append("")
        lines.append("Install the providing packages in the build container.")
    else:
        lines.append("Every component the app needs resolves.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    fatal, tolerated = classify(scan(args.dist_dir))
    print(report(fatal, tolerated))
    if fatal:
        print("::error::bundled components the app needs have unresolved dependencies")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
