# This file is part of ZeeRef.
#
# ZeeRef is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# ZeeRef is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with ZeeRef.  If not, see <https://www.gnu.org/licenses/>.

"""Tile pyramid generation for image storage."""

from __future__ import annotations

from collections.abc import Iterator
from io import BytesIO
from math import ceil

from PIL import Image

TILE_SIZE = 512


def generate_tiles(
    pil_img: Image.Image,
) -> Iterator[tuple[Image.Image, int, int, int]]:
    """Yield (tile_pil, level, col, row) for each tile in the pyramid.

    Level 0 is full resolution. Each subsequent level halves the image.
    Stops after the first level where the entire image fits in one tile.
    """
    level = 0
    current = pil_img
    while True:
        w, h = current.size
        cols = ceil(w / TILE_SIZE)
        rows = ceil(h / TILE_SIZE)
        for row in range(rows):
            for col in range(cols):
                x0 = col * TILE_SIZE
                y0 = row * TILE_SIZE
                x1 = min(x0 + TILE_SIZE, w)
                y1 = min(y0 + TILE_SIZE, h)
                tile = current.crop((x0, y0, x1, y1))
                yield (tile, level, col, row)
        if w <= TILE_SIZE and h <= TILE_SIZE:
            break
        current = pil_img.resize(
            (
                max(1, pil_img.width >> (level + 1)),
                max(1, pil_img.height >> (level + 1)),
            ),
            Image.Resampling.LANCZOS,
        )
        level += 1


def encode_tile(tile: Image.Image, fmt: str) -> bytes:
    """Encode a PIL tile image to bytes."""
    buf = BytesIO()
    save_fmt = "JPEG" if fmt == "jpeg" else "PNG"
    tile.save(buf, save_fmt, quality=90)
    return buf.getvalue()


def pick_format(pil_img: Image.Image) -> str:
    """Choose storage format: png for small/alpha images, jpeg otherwise."""
    w, h = pil_img.size
    if pil_img.mode == "RGBA" or (w < 500 and h < 500):
        return "png"
    return "jpeg"
