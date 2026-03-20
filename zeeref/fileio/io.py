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

"""High-level IO orchestration: load, save, drain."""

from __future__ import annotations

import logging
import os
import queue
import tempfile
import time
import uuid
from collections.abc import Sequence
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image
from PyQt6 import QtCore

from zeeref.fileio.errors import ZeeFileIOError
from zeeref.fileio.image import load_image
from zeeref.fileio.scratch import copy_with_progress, create_scratch_file
from zeeref.types.snapshot import (
    IOResult,
    ItemSnapshot,
    LoadResult,
    PixmapItemSnapshot,
    SaveResult,
)
from zeeref.types.tile import TileKey
from zeeref.fileio.sql import SQLiteIO
from zeeref.fileio.thread import ThreadedIO

if TYPE_CHECKING:
    from zeeref.scene import ZeeGraphicsScene

logger = logging.getLogger(__name__)


def load_zref(
    filename: Path, scene: ZeeGraphicsScene, worker: ThreadedIO | None = None
) -> None:
    """Load ZeeRef native file via scratch copy."""
    logger.info(f"Loading from file {filename}...")
    try:
        swp = create_scratch_file(filename, worker=worker)
    except Exception as e:
        logger.exception(f"Failed to create scratch file for {filename}")
        if worker:
            worker.finished.emit(LoadResult(filename=filename, errors=[str(e)]))
            return
        raise ZeeFileIOError(msg=str(e), filename=filename) from e
    scene._scratch_file = swp
    io = SQLiteIO(swp, readonly=True, worker=worker)
    io.filename = filename
    snapshots = io.read()
    if worker:
        worker.finished.emit(
            LoadResult(
                filename=filename,
                snapshots=snapshots,
                scratch_file=swp,
            )
        )


def load_zref_metadata(
    filename: Path, scene: ZeeGraphicsScene, worker: ThreadedIO | None = None
) -> None:
    """Load ZeeRef native file — metadata only, no blob data."""
    logger.info(f"Loading metadata from file {filename}...")
    try:
        swp = create_scratch_file(filename, worker=worker)
    except Exception as e:
        logger.exception(f"Failed to create scratch file for {filename}")
        if worker:
            worker.finished.emit(LoadResult(filename=filename, errors=[str(e)]))
            return
        raise ZeeFileIOError(msg=str(e), filename=filename) from e
    scene._scratch_file = swp
    io = SQLiteIO(swp, readonly=True, worker=worker)
    io.filename = filename
    snapshots = io.read_metadata()
    if worker:
        worker.finished.emit(
            LoadResult(
                filename=filename,
                snapshots=snapshots,
                scratch_file=swp,
            )
        )


def save_zref(
    filename: Path,
    snapshots: list[ItemSnapshot],
    swp_path: Path,
    worker: ThreadedIO | None = None,
) -> None:
    """Save ZeeRef native file via .swp drain + copy + compact.

    1. Final drain to .swp (no deletes, no VACUUM)
    2. Copy .swp to temp file next to target
    3. Compact the copy (delete stale rows, VACUUM)
    4. Atomic replace target .zref
    """
    logger.info(f"Saving to file {filename}...")
    temp_path: Path | None = None
    try:
        # 1. Final drain to .swp
        drain_io = SQLiteIO(swp_path, worker=worker)
        drain_io.write(snapshots, compact=False)
        drain_io._close_connection()

        # 2. Copy .swp to temp file next to target
        target_dir = filename.resolve().parent
        tf = tempfile.NamedTemporaryFile(dir=target_dir, suffix=".zref", delete=False)
        temp_path = Path(tf.name)
        tf.close()
        copy_with_progress(swp_path, temp_path, worker=worker)

        # 3. Compact the copy
        live_ids = {snap.save_id for snap in snapshots}
        compact_io = SQLiteIO(temp_path)
        existing_ids = {row[0] for row in compact_io.fetchall("SELECT id FROM items")}
        stale_ids = existing_ids - live_ids
        if stale_ids:
            compact_io.delete_items(stale_ids)
        compact_io.ex("VACUUM")
        compact_io.connection.commit()
        compact_io._close_connection()

        # 4. Atomic replace
        os.replace(temp_path, filename)
        temp_path = None
    except Exception as e:
        logger.exception(f"Failed to save {filename}")
        if temp_path and temp_path.exists():
            temp_path.unlink()
        if worker:
            worker.finished.emit(SaveResult(filename=filename, errors=[str(e)]))
        return

    logger.info("End save")
    if worker:
        worker.finished.emit(SaveResult(filename=filename))


def drain_zref(
    filename: Path,
    snapshots: list[ItemSnapshot],
    worker: ThreadedIO | None = None,
) -> None:
    """Drain scene state to the scratch file (no deletes, no VACUUM)."""
    logger.info(f"Draining to scratch file {filename}...")
    try:
        io = SQLiteIO(filename, worker=worker)
        io.write(snapshots, compact=False)
    except ZeeFileIOError as e:
        logger.exception(f"Failed to drain {filename}")
        if worker:
            worker.finished.emit(SaveResult(filename=filename, errors=[str(e)]))
        return
    logger.info("End drain")
    if worker:
        worker.finished.emit(SaveResult(filename=filename))


def load_images(
    filenames: Sequence[str | QtCore.QUrl],
    pos: QtCore.QPointF,
    scene: ZeeGraphicsScene,
    worker: ThreadedIO,
) -> None:
    """Add images to existing scene.

    Each image is written to the .swp as a tile, then a snapshot is
    queued for the main thread to create the item from.
    """
    errors = []
    snapshots: list[PixmapItemSnapshot] = []
    worker.begin_processing.emit(len(filenames))
    assert scene._scratch_file is not None
    io = SQLiteIO(scene._scratch_file)

    for i, raw_filename in enumerate(filenames):
        logger.info(f"Loading image from file {raw_filename}")
        load_path: Path | QtCore.QUrl = (
            Path(raw_filename) if isinstance(raw_filename, str) else raw_filename
        )
        img, filename = load_image(load_path)
        worker.progress.emit(i)
        if img.isNull():
            logger.info(f"Could not load file {filename}")
            errors.append(filename)
            continue

        # Write tile to .swp
        image_id = uuid.uuid4().hex
        w, h = img.width(), img.height()
        barray = QtCore.QByteArray()
        buf = QtCore.QBuffer(barray)
        buf.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
        fmt = "png" if img.hasAlphaChannel() or (w < 500 and h < 500) else "jpeg"
        img.save(buf, fmt.upper(), quality=90)
        tile_bytes = barray.data()

        io.ex(
            "INSERT OR IGNORE INTO images (id, width, height, format) "
            "VALUES (?, ?, ?, ?)",
            (image_id, w, h, fmt),
        )
        io.ex(
            "INSERT INTO tiles (image_id, level, col, row, data) "
            "VALUES (?, 0, 0, 0, ?)",
            (image_id, tile_bytes),
        )
        io.connection.commit()

        snap = PixmapItemSnapshot(
            save_id=uuid.uuid4().hex,
            type="pixmap",
            x=pos.x() - w / 2,
            y=pos.y() - h / 2,
            z=0,
            scale=1,
            rotation=0,
            flip=1,
            data={"filename": filename},
            created_at=time.time(),
            image_id=image_id,
            width=w,
            height=h,
        )
        scene.add_item_later(snap, selected=True)
        snapshots.append(snap)
        if worker.canceled:
            break
        worker.msleep(10)

    io._close_connection()
    worker.finished.emit(IOResult(filename=None, errors=errors))


_SENTINEL = None


class ImageLoader(QtCore.QThread):
    """Background thread that loads tile blobs from the .swp file.

    Opens its own read-only SQLite connection. Receives tile key requests
    via a thread-safe queue, fetches the blob, decodes with Pillow,
    and emits tile_blob_loaded back to the main thread.
    """

    tile_blob_loaded = QtCore.pyqtSignal(str, int, int, int, object)

    def __init__(self, swp_path: Path) -> None:
        super().__init__()
        self._swp_path = swp_path
        self._queue: queue.Queue[TileKey | None] = queue.Queue()
        self._requested: set[TileKey] = set()
        self._stop = False

    def request_load(self, key: TileKey) -> None:
        """Request a tile to be loaded. Thread-safe, deduplicating."""
        if key not in self._requested:
            self._requested.add(key)
            self._queue.put(key)

    def stop(self) -> None:
        self._stop = True
        self._queue.put(_SENTINEL)
        self.wait()

    def run(self) -> None:
        io = SQLiteIO(self._swp_path, readonly=True)
        while not self._stop:
            key = self._queue.get()
            if key is _SENTINEL or self._stop:
                break
            image_id, level, col, row = key
            try:
                row_data = io.fetchone(
                    "SELECT data FROM tiles "
                    "WHERE image_id=? AND level=? AND col=? AND row=?",
                    (image_id, level, col, row),
                )
                if row_data is None:
                    logger.warning(f"No tile found for {key}")
                    continue
                pil_img = Image.open(BytesIO(row_data[0]))
                pil_img.load()
                self.tile_blob_loaded.emit(image_id, level, col, row, pil_img)
                self._requested.discard(key)
            except Exception:
                logger.exception(f"Failed to load tile {key}")
                self._requested.discard(key)
        io._close_connection()
