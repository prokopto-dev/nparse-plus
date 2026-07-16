#!/bin/sh
# Generate packaging/icon.icns from data/ui/icon.png (macOS only: sips+iconutil).
# The source art is 64x64, so the large representations are upscales — good
# enough for 1.0; regenerate from higher-res art when it exists.
set -eu
cd "$(dirname "$0")/.."
ICONSET=packaging/icon.iconset
rm -rf "$ICONSET"
mkdir -p "$ICONSET"
for size in 16 32 64 128 256 512; do
    sips -z $size $size data/ui/icon.png --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
    double=$((size * 2))
    sips -z $double $double data/ui/icon.png --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o packaging/icon.icns
rm -rf "$ICONSET"
echo "wrote packaging/icon.icns"
