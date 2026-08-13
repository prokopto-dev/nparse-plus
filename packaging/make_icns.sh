#!/bin/sh
# Generate packaging/icon.icns from data/assets/icon.svg (macOS only: iconutil).
#
# Every representation in the iconset is a native render off the vector master,
# so there are no upscales here any more — this used to `sips` a 64x64 PNG up
# to 1024 and said so in this comment. tools/gen_icons.py --iconset does the
# rasterizing (PySide6's QSvgRenderer); iconutil is the only macOS-only part
# and is why this stayed a shell script.
set -eu
cd "$(dirname "$0")/.."
ICONSET=packaging/icon.iconset
rm -rf "$ICONSET"
uv run python tools/gen_icons.py --iconset "$ICONSET"
iconutil -c icns "$ICONSET" -o packaging/icon.icns
rm -rf "$ICONSET"
echo "wrote packaging/icon.icns"
