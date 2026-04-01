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

Thread safety: request() and request_blocking() are protected by a lock
so they can be called from background threads (e.g. for image stitching).
"""

from __future__ import annotations

import collections
import logging
import threading
from pathlib import Path
from typing import Protocol

from PIL import Image
from PyQt6 import QtCore, QtGui

from zeeref.fileio.io import ImageLoader
from zeeref.types.tile import TileKey
from zeeref.utils import bg_thread_only, main_thread_only

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

    request() and request_blocking() are thread-safe.
    """

    def __init__(self, swp_path: Path, capacity_mb: int = 256) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._lru: collections.OrderedDict[TileKey, QtGui.QPixmap] = (
            collections.OrderedDict()
        )
        self._capacity_bytes = capacity_mb * 1024 * 1024
        self._current_bytes = 0
        self._visible: set[TileKey] = set()
        self._in_frame: bool = False
        self._subscribers: dict[str, list[TileCacheListener]] = {}
        self._blocking_waiters: dict[TileKey, list[threading.Event]] = {}
        self._blocking_keys: set[TileKey] = set()
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
        with self._lock:
            self._visible = set()
            self._in_frame = True

    def end_frame(self) -> None:
        """End a viewport check frame. Runs eviction with the full visible set."""
        with self._lock:
            self._in_frame = False
            self._evict()

    @main_thread_only
    def request(self, keys: set[TileKey]) -> dict[TileKey, QtGui.QPixmap]:
        """Request tiles. Returns cached ones immediately, queues the rest.

        Thread-safe.
        """
        with self._lock:
            self._visible = self._visible | keys
            hits: dict[TileKey, QtGui.QPixmap] = {}
            for key in keys:
                if key in self._lru:
                    self._lru.move_to_end(key)
                    hits[key] = self._lru[key]
                else:
                    self._loader.request_load(key)
            return hits

    @bg_thread_only
    def request_blocking(self, keys: set[TileKey]) -> dict[TileKey, QtGui.QPixmap]:
        """Request tiles, blocking until all are loaded.

        Thread-safe. Requested keys are protected from eviction.
        Returns all tiles as QPixmaps.
        """
        with self._lock:
            self._visible = self._visible | keys
            self._blocking_keys = self._blocking_keys | keys
            result: dict[TileKey, QtGui.QPixmap] = {}
            missing: dict[TileKey, threading.Event] = {}
            for key in keys:
                if key in self._lru:
                    self._lru.move_to_end(key)
                    result[key] = self._lru[key]
                else:
                    event = threading.Event()
                    self._blocking_waiters.setdefault(key, []).append(event)
                    self._loader.request_load(key)
                    missing[key] = event

        # Wait outside the lock
        for key, event in missing.items():
            event.wait()
            with self._lock:
                if key in self._lru:
                    result[key] = self._lru[key]

        # Release blocking protection
        with self._lock:
            self._blocking_keys -= keys

        return result

    def stop(self) -> None:
        self._loader.stop()
        with self._lock:
            self._lru.clear()
            self._subscribers.clear()
            # Wake any blocked waiters
            for events in self._blocking_waiters.values():
                for event in events:
                    event.set()
            self._blocking_waiters.clear()

    def _notify_loaded(self, key: TileKey, pixmap: QtGui.QPixmap) -> None:
        for listener in self._subscribers.get(key.image_id, []):
            listener.on_tile_loaded(key, pixmap)

    def _notify_unloaded(self, key: TileKey) -> None:
        for listener in self._subscribers.get(key.image_id, []):
            listener.on_tile_unloaded(key)

    @staticmethod
    def _pixmap_bytes(pixmap: QtGui.QPixmap) -> int:
        return pixmap.width() * pixmap.height() * pixmap.depth() // 8

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
        with self._lock:
            if key in self._lru:
                self._current_bytes -= self._pixmap_bytes(self._lru[key])
            self._lru[key] = pixmap
            self._lru.move_to_end(key)
            self._current_bytes += self._pixmap_bytes(pixmap)
            # Wake any blocking waiters for this key
            waiters = self._blocking_waiters.pop(key, [])
            for event in waiters:
                event.set()
        logger.debug(f"Tile loaded: {key}")
        self._notify_loaded(key, pixmap)

    def _evict(self) -> None:
        """Evict oldest tiles over capacity, skipping visible ones.

        Must be called with self._lock held.
        """
        evicted = 0
        while self._current_bytes > self._capacity_bytes and self._lru:
            key, pixmap = self._lru.popitem(last=False)
            if key in self._visible or key in self._blocking_keys:
                self._lru[key] = pixmap
                self._lru.move_to_end(key)
                break
            self._current_bytes -= self._pixmap_bytes(pixmap)
            logger.debug(f"Tile evicted: {key}")
            self._notify_unloaded(key)
            evicted += 1
        if evicted:
            logger.info(
                f"Evicted {evicted} tiles (lru={self._current_bytes // 1024 // 1024}MB/"
                f"{self._capacity_bytes // 1024 // 1024}MB, "
                f"count={len(self._lru)}, visible={len(self._visible)})"
            )


_instance: TileCache | None = None


def get_tile_cache() -> TileCache:
    assert _instance is not None, "TileCache not initialized"
    return _instance


def set_tile_cache(cache: TileCache | None) -> None:
    global _instance
    if _instance is not None:
        _instance.stop()
    _instance = cache
