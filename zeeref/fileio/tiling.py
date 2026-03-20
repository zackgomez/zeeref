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
from math import ceil

from PIL import Image
from PyQt6 import QtCore, QtGui

TILE_SIZE = 512


def _pil_to_qimage(pil_img: Image.Image) -> QtGui.QImage:
    """Convert a PIL Image to a QImage."""
    if pil_img.mode == "RGBA":
        fmt = QtGui.QImage.Format.Format_RGBA8888
        channels = 4
    else:
        pil_img = pil_img.convert("RGB")
        fmt = QtGui.QImage.Format.Format_RGB888
        channels = 3
    data = pil_img.tobytes()
    stride = channels * pil_img.width
    qimg = QtGui.QImage(data, pil_img.width, pil_img.height, stride, fmt)
    return qimg.copy()


def generate_tiles(
    pil_img: Image.Image,
) -> Iterator[tuple[QtGui.QImage, int, int, int]]:
    """Yield (tile_qimage, level, col, row) for each tile in the pyramid.

    Level 0 is full resolution. Each subsequent level halves the image.
    Uses Qt for scaling (fast) and cropping.
    Stops after the first level where the entire image fits in one tile.
    """
    current = _pil_to_qimage(pil_img)
    level = 0
    while True:
        w = current.width()
        h = current.height()
        for row in range(ceil(h / TILE_SIZE)):
            for col in range(ceil(w / TILE_SIZE)):
                tw = min(TILE_SIZE, w - col * TILE_SIZE)
                th = min(TILE_SIZE, h - row * TILE_SIZE)
                tile = current.copy(col * TILE_SIZE, row * TILE_SIZE, tw, th)
                yield (tile, level, col, row)
        if w <= TILE_SIZE and h <= TILE_SIZE:
            break
        current = current.scaled(
            max(1, w >> 1),
            max(1, h >> 1),
            transformMode=QtCore.Qt.TransformationMode.SmoothTransformation,
        )
        level += 1


def encode_tile(tile: QtGui.QImage, fmt: str) -> bytes:
    """Encode a QImage tile to bytes."""
    buf = QtCore.QByteArray()
    buffer = QtCore.QBuffer(buf)
    buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
    tile.save(buffer, "JPEG" if fmt == "jpeg" else "PNG", quality=90)
    return buf.data()


def pick_format(pil_img: Image.Image) -> str:
    """Choose storage format: png for small/alpha images, jpeg otherwise."""
    w, h = pil_img.size
    if pil_img.mode == "RGBA" or (w < 500 and h < 500):
        return "png"
    return "jpeg"
