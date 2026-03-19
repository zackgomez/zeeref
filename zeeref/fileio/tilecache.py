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

"""Tile cache with LRU eviction.

Sits between the view and ImageLoader. The view calls mark_visible()
with the set of tile keys it needs. TileCache bumps loaded tiles in
the LRU, requests missing ones from ImageLoader, and evicts excess.
Emits tile_loaded (with QPixmap) and tile_unloaded signals.
"""

from __future__ import annotations

import collections
import logging
from pathlib import Path

from PIL import Image
from PyQt6 import QtCore, QtGui

from zeeref.fileio.io import ImageLoader, TileKey

logger = logging.getLogger(__name__)


def _pil_to_qpixmap(pil_img: Image.Image) -> QtGui.QPixmap:
    """Convert a PIL Image to a QPixmap."""
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
    return QtGui.QPixmap.fromImage(qimg.copy())


class TileCache(QtCore.QObject):
    """LRU tile cache backed by an ImageLoader.

    All methods are called on the main thread. The ImageLoader runs
    on a background thread and delivers decoded PIL images via signal.
    """

    tile_loaded = QtCore.pyqtSignal(str, int, int, int, object)
    tile_unloaded = QtCore.pyqtSignal(str, int, int, int)

    def __init__(self, swp_path: Path, capacity: int = 10) -> None:
        super().__init__()
        self._lru: collections.OrderedDict[TileKey, QtGui.QPixmap] = (
            collections.OrderedDict()
        )
        self._capacity = capacity
        self._visible: set[TileKey] = set()
        self._loader = ImageLoader(swp_path)
        self._loader.tile_blob_loaded.connect(self._on_tile_blob_loaded)
        self._loader.start()

    def mark_visible(self, needed: set[TileKey]) -> None:
        """Declare which tiles are currently needed.

        Bumps loaded tiles in LRU, requests missing ones, evicts excess.
        """
        self._visible = needed
        for key in needed:
            if key in self._lru:
                self._lru.move_to_end(key)
            else:
                self._loader.request_load(key)
        self._evict()

    def stop(self) -> None:
        self._loader.stop()
        self._lru.clear()

    def _on_tile_blob_loaded(
        self,
        image_id: str,
        level: int,
        col: int,
        row: int,
        pil_img: object,
    ) -> None:
        """Handle decoded PIL image from ImageLoader."""
        assert isinstance(pil_img, Image.Image)
        key: TileKey = (image_id, level, col, row)
        pixmap = _pil_to_qpixmap(pil_img)
        self._lru[key] = pixmap
        self._lru.move_to_end(key)
        logger.debug(f"Tile loaded: {key}")
        self.tile_loaded.emit(image_id, level, col, row, pixmap)
        self._evict()

    def _evict(self) -> None:
        """Evict oldest tiles over capacity, skipping visible ones."""
        while len(self._lru) > self._capacity:
            key, _pixmap = self._lru.popitem(last=False)
            if key in self._visible:
                # Don't evict something currently visible; put it back
                self._lru[key] = _pixmap
                self._lru.move_to_end(key)
                break
            image_id, level, col, row = key
            logger.debug(f"Tile evicted: {key}")
            self.tile_unloaded.emit(image_id, level, col, row)
