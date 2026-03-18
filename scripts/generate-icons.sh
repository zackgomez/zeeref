#!/usr/bin/env bash
set -euo pipefail

# Generate icon assets from logo.svg
# Requires: rsvg-convert (librsvg), magick (ImageMagick 7)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
SVG="$REPO_ROOT/zeeref/assets/logo.svg"
ASSETS="$REPO_ROOT/zeeref/assets"
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

if [ ! -f "$SVG" ]; then
    echo "Error: $SVG not found" >&2
    exit 1
fi

echo "Source: $SVG"
echo "Output: $ASSETS"

# Rasterize at all needed sizes
SIZES=(16 32 48 128 256 512 1024)
for size in "${SIZES[@]}"; do
    echo "  Rasterizing ${size}x${size}..."
    rsvg-convert -w "$size" -h "$size" "$SVG" -o "$TMPDIR/logo-${size}.png"
done

# logo.png — 256x256 for Qt window icon
cp "$TMPDIR/logo-256.png" "$ASSETS/logo.png"
echo "  -> logo.png (256x256)"

# logo.ico — Windows icon (multi-size)
magick "$TMPDIR/logo-16.png" "$TMPDIR/logo-32.png" "$TMPDIR/logo-48.png" "$TMPDIR/logo-256.png" "$ASSETS/logo.ico"
echo "  -> logo.ico (16,32,48,256)"

# logo.icns — macOS icon
# icns needs specific sizes: 16,32,128,256,512,1024
magick "$TMPDIR/logo-16.png" "$TMPDIR/logo-32.png" "$TMPDIR/logo-128.png" \
       "$TMPDIR/logo-256.png" "$TMPDIR/logo-512.png" "$TMPDIR/logo-1024.png" \
       "$ASSETS/logo.icns"
echo "  -> logo.icns (16,32,128,256,512,1024)"

echo "Done."
