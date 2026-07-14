#!/usr/bin/env python
"""Compare nparse's map files against EQTool's map files.

Compares data/maps/map_files/*.txt (nparse, authoritative) with
$EQTOOL_SRC/EQTool/map_files/*.txt by filename (case-insensitive) and
SHA-256 content hash. Prints a summary and per-file details. Does NOT
modify any map files.

Usage:
    .venv/bin/python tools/merge_maps.py [path-to-eqtool-checkout]

The EQTool checkout path may also be given via the EQTOOL_SRC env var.
"""

import hashlib
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
NPARSE_MAPS = REPO_ROOT / "data" / "maps" / "map_files"
EQTOOL_MAPS_REL = Path("EQTool") / "map_files"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def index_maps(directory: Path) -> dict[str, Path]:
    """Map lower-cased filename -> path for every .txt map file in directory."""
    out: dict[str, Path] = {}
    for p in sorted(directory.glob("*.txt")):
        key = p.name.lower()
        if key in out:
            raise ValueError(f"duplicate case-insensitive filename in {directory}: {p.name}")
        out[key] = p
    return out


def main() -> None:
    if len(sys.argv) > 1:
        eqtool_src = Path(sys.argv[1])
    elif os.environ.get("EQTOOL_SRC"):
        eqtool_src = Path(os.environ["EQTOOL_SRC"])
    else:
        sys.exit("usage: merge_maps.py <path-to-eqtool-checkout>  (or set EQTOOL_SRC)")

    eqtool_maps = eqtool_src / EQTOOL_MAPS_REL
    nparse = index_maps(NPARSE_MAPS)
    eqtool = index_maps(eqtool_maps)

    identical, differs = [], []
    only_nparse = sorted(set(nparse) - set(eqtool))
    only_eqtool = sorted(set(eqtool) - set(nparse))
    for name in sorted(set(nparse) & set(eqtool)):
        if sha256(nparse[name]) == sha256(eqtool[name]):
            identical.append(name)
        else:
            differs.append(name)

    print("map file comparison: nparse (data/maps/map_files) vs EQTool (EQTool/map_files)")
    print(f"nparse files:  {len(nparse)}")
    print(f"eqtool files:  {len(eqtool)}")
    print()
    print(f"identical:      {len(identical)}")
    print(f"differs:        {len(differs)}")
    print(f"only in nparse: {len(only_nparse)}")
    print(f"only in eqtool: {len(only_eqtool)}")
    print()
    if differs:
        print("-- differs --")
        for name in differs:
            n, e = nparse[name], eqtool[name]
            print(f"{n.name}: nparse {n.stat().st_size} bytes, eqtool {e.stat().st_size} bytes")
        print()
    if only_nparse:
        print("-- only in nparse --")
        for name in only_nparse:
            print(nparse[name].name)
        print()
    if only_eqtool:
        print("-- only in eqtool --")
        for name in only_eqtool:
            print(eqtool[name].name)
        print()
    print("NOTE: nparse's map set stays authoritative; no files were modified.")


if __name__ == "__main__":
    main()
