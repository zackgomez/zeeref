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

Items subscribe by image_id and manage their own tile requests via
update_visible_tiles(). TileCache handles LRU, PIL->QPixmap conversion,
and dispatches load/unload events to TileCacheListener subscribers.
"""

from __future__ import annotations

import collections
import logging
from pathlib import Path
from typing import Protocol

from PIL import Image
from PyQt6 import QtCore, QtGui

from zeeref.fileio.io import ImageLoader
from zeeref.types.tile import TileKey

logger = logging.getLogger(__name__)


class TileCacheListener(Protocol):
    """Interface for objects that receive tile load/unload events."""

    def on_tile_loaded(self, key: TileKey, pixmap: QtGui.QPixmap) -> None: ...

    def on_tile_unloaded(self, key: TileKey) -> None: ...


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

    Items subscribe by image_id via TileCacheListener to receive
    on_tile_loaded / on_tile_unloaded events.
    """

    def __init__(self, swp_path: Path, capacity: int = 10) -> None:
        super().__init__()
        self._lru: collections.OrderedDict[TileKey, QtGui.QPixmap] = (
            collections.OrderedDict()
        )
        self._capacity = capacity
        self._visible: set[TileKey] = set()
        self._in_frame: bool = False
        self._subscribers: dict[str, list[TileCacheListener]] = {}
        self._loader = ImageLoader(swp_path)
        self._loader.tile_blob_loaded.connect(self._on_tile_blob_loaded)
        self._loader.start()

    def subscribe(self, image_id: str, listener: TileCacheListener) -> None:
        """Register for tile events for an image_id."""
        self._subscribers.setdefault(image_id, []).append(listener)

    def unsubscribe(self, image_id: str, listener: TileCacheListener) -> None:
        """Deregister from tile events for an image_id."""
        listeners = self._subscribers.get(image_id)
        if listeners:
            try:
                listeners.remove(listener)
            except ValueError:
                pass
            if not listeners:
                del self._subscribers[image_id]

    def begin_frame(self) -> None:
        """Start a viewport check frame. Accumulates requests until end_frame."""
        self._visible = set()
        self._in_frame = True

    def end_frame(self) -> None:
        """End a viewport check frame. Runs eviction with the full visible set."""
        self._in_frame = False
        self._evict()

    def request(self, keys: set[TileKey]) -> None:
        """Request specific tiles. Bumps loaded ones in LRU, queues missing."""
        self._visible = self._visible | keys
        for key in keys:
            if key in self._lru:
                self._lru.move_to_end(key)
            else:
                self._loader.request_load(key)

    def stop(self) -> None:
        self._loader.stop()
        self._lru.clear()
        self._subscribers.clear()

    def _notify_loaded(self, key: TileKey, pixmap: QtGui.QPixmap) -> None:
        for listener in self._subscribers.get(key.image_id, []):
            listener.on_tile_loaded(key, pixmap)

    def _notify_unloaded(self, key: TileKey) -> None:
        for listener in self._subscribers.get(key.image_id, []):
            listener.on_tile_unloaded(key)

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
        key = TileKey(image_id, level, col, row)
        pixmap = _pil_to_qpixmap(pil_img)
        self._lru[key] = pixmap
        self._lru.move_to_end(key)
        logger.debug(f"Tile loaded: {key}")
        self._notify_loaded(key, pixmap)

    def _evict(self) -> None:
        """Evict oldest tiles over capacity, skipping visible ones."""
        while len(self._lru) > self._capacity:
            key, pixmap = self._lru.popitem(last=False)
            if key in self._visible:
                self._lru[key] = pixmap
                self._lru.move_to_end(key)
                break
            logger.info(
                f"Tile evicted: {key} (lru={len(self._lru)}, cap={self._capacity}, visible={len(self._visible)})"
            )
            self._notify_unloaded(key)


_instance: TileCache | None = None


def get_tile_cache() -> TileCache:
    assert _instance is not None, "TileCache not initialized"
    return _instance


def set_tile_cache(cache: TileCache | None) -> None:
    global _instance
    if _instance is not None:
        _instance.stop()
    _instance = cache
