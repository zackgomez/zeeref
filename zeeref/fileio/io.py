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
from typing import TYPE_CHECKING, cast

from PIL import Image
from dataclasses import dataclass

from PyQt6 import QtCore, QtGui

from zeeref.fileio.errors import ZeeFileIOError
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
    from zeeref.fileio.tilecache import TileCache
    from zeeref.scene import ZeeGraphicsScene


@dataclass
class ImageResult(IOResult):
    """Result from stitching a full image."""

    image: QtGui.QImage | None = None


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


def _insert_image(
    pil_img: Image.Image,
    filename: str | None,
    pos: QtCore.QPointF,
    io: SQLiteIO,
    scene: ZeeGraphicsScene,
) -> PixmapItemSnapshot:
    """Write tiles to .swp and queue a snapshot for the main thread."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from zeeref.fileio.tiling import encode_tile, generate_tiles, pick_format

    t0 = time.monotonic()
    image_id = uuid.uuid4().hex
    w, h = pil_img.size
    fmt = pick_format(pil_img)
    logger.debug(f"_insert_image: {w}x{h} fmt={fmt}")

    io.ex(
        "INSERT INTO images (id, width, height, format) VALUES (?, ?, ?, ?)",
        (image_id, w, h, fmt),
    )

    def _encode(
        args: tuple[QtGui.QImage, int, int, int],
    ) -> tuple[int, int, int, bytes]:
        tile_qimg, level, col, row = args
        return (level, col, row, encode_tile(tile_qimg, fmt))

    tile_count = 0
    with ThreadPoolExecutor() as pool:
        futures = [pool.submit(_encode, args) for args in generate_tiles(pil_img)]
        for future in as_completed(futures):
            level, col, row, data = future.result()
            io.ex(
                "INSERT INTO tiles (image_id, level, col, row, data) "
                "VALUES (?, ?, ?, ?, ?)",
                (image_id, level, col, row, data),
            )
            tile_count += 1
    io.connection.commit()
    logger.debug(f"_insert_image: {tile_count} tiles in {time.monotonic() - t0:.3f}s")

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
    return snap


def insert_image_files(
    filenames: Sequence[str | QtCore.QUrl],
    pos: QtCore.QPointF,
    scene: ZeeGraphicsScene,
    worker: ThreadedIO,
) -> None:
    """Add images from files to existing scene."""
    from zeeref.fileio.image import load_pil_from_source

    errors = []
    worker.begin_processing.emit(len(filenames))
    assert scene._scratch_file is not None
    io = SQLiteIO(scene._scratch_file)

    for i, raw_filename in enumerate(filenames):
        t_load = time.monotonic()
        logger.info(f"Loading image from file {raw_filename}")
        load_path: Path | QtCore.QUrl = (
            Path(raw_filename) if isinstance(raw_filename, str) else raw_filename
        )
        pil_img, filename = load_pil_from_source(load_path)
        logger.debug(f"insert_image_files: load took {time.monotonic() - t_load:.3f}s")
        worker.progress.emit(i)
        if pil_img is None:
            logger.info(f"Could not load file {filename}")
            errors.append(filename)
            continue

        _insert_image(pil_img, filename, pos, io, scene)
        if worker.canceled:
            break
        worker.msleep(10)

    io._close_connection()
    worker.finished.emit(IOResult(filename=None, errors=errors))


def insert_image_from_clipboard(
    qimage: QtGui.QImage,
    pos: QtCore.QPointF,
    scene: ZeeGraphicsScene,
    worker: ThreadedIO | None = None,
) -> None:
    """Add a QImage from the clipboard to the scene."""
    # Convert QImage to PIL
    qimage = qimage.convertToFormat(QtGui.QImage.Format.Format_RGBA8888)
    ptr = qimage.constBits()
    assert ptr is not None
    ptr.setsize(qimage.sizeInBytes())
    raw_bytes = bytes(cast(bytearray, ptr))
    pil_img = Image.frombytes(
        "RGBA",
        (qimage.width(), qimage.height()),
        raw_bytes,
        "raw",
        "RGBA",
        qimage.bytesPerLine(),
    )

    assert scene._scratch_file is not None
    io = SQLiteIO(scene._scratch_file)
    _insert_image(pil_img, None, pos, io, scene)
    io._close_connection()

    if worker:
        worker.finished.emit(IOResult(filename=None))


def stitch_image(
    tile_cache: TileCache,
    image_id: str,
    width: int,
    height: int,
    worker: ThreadedIO | None = None,
) -> None:
    """Fetch all level-0 tiles and stitch into a full QImage.

    Runs on a background thread via run_async. Uses request_blocking
    to fetch tiles through the TileCache (warms the LRU).
    The stitched QImage is emitted via worker.finished as IOResult.image.
    """
    from math import ceil

    from zeeref.fileio.tiling import TILE_SIZE

    keys: set[TileKey] = set()
    num_cols = ceil(width / TILE_SIZE)
    num_rows = ceil(height / TILE_SIZE)
    for row in range(num_rows):
        for col in range(num_cols):
            keys.add(TileKey(image_id, 0, col, row))
    logger.debug(f"stitch_image: requesting {len(keys)} tiles for {image_id[:8]}")

    tiles = tile_cache.request_blocking(keys)
    logger.debug(f"stitch_image: got {len(tiles)} tiles back")

    img = QtGui.QImage(width, height, QtGui.QImage.Format.Format_ARGB32)
    img.fill(QtGui.QColor(0, 0, 0, 0))
    painter = QtGui.QPainter(img)
    for key, pixmap in tiles.items():
        painter.drawPixmap(key.col * TILE_SIZE, key.row * TILE_SIZE, pixmap)
    painter.end()
    logger.debug(
        f"stitch_image: stitched {img.width()}x{img.height()}, null={img.isNull()}"
    )

    if worker:
        worker.finished.emit(ImageResult(filename=None, image=img))


_SENTINEL = None


class _LoaderWorker(QtCore.QThread):
    """Single worker thread that loads tile blobs from the .swp file."""

    tile_blob_loaded = QtCore.pyqtSignal(str, int, int, int, object)

    def __init__(
        self,
        swp_path: Path,
        work_queue: queue.Queue[TileKey | None],
    ) -> None:
        super().__init__()
        self._swp_path = swp_path
        self._queue = work_queue

    def run(self) -> None:
        io = SQLiteIO(self._swp_path, readonly=True)
        while True:
            key = self._queue.get()
            if key is _SENTINEL:
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
            except Exception:
                logger.exception(f"Failed to load tile {key}")
        io._close_connection()


class ImageLoader(QtCore.QObject):
    """Thread pool that loads tile blobs from the .swp file.

    Multiple workers share a single queue. Each has its own SQLite
    connection (concurrent readers are fine). Dedup via _requested set
    prevents duplicate queue entries.
    """

    tile_blob_loaded = QtCore.pyqtSignal(str, int, int, int, object)

    def __init__(self, swp_path: Path, num_workers: int = 4) -> None:
        super().__init__()
        self._queue: queue.Queue[TileKey | None] = queue.Queue()
        self._requested: set[TileKey] = set()
        self._workers: list[_LoaderWorker] = []
        for _ in range(num_workers):
            worker = _LoaderWorker(swp_path, self._queue)
            worker.tile_blob_loaded.connect(self._on_worker_loaded)
            self._workers.append(worker)

    def start(self) -> None:
        for worker in self._workers:
            worker.start()

    def request_load(self, key: TileKey) -> None:
        """Request a tile to be loaded. Thread-safe, deduplicating."""
        if key not in self._requested:
            self._requested.add(key)
            self._queue.put(key)

    def stop(self) -> None:
        for _ in self._workers:
            self._queue.put(_SENTINEL)
        for worker in self._workers:
            worker.wait()
        self._workers.clear()

    def _on_worker_loaded(
        self,
        image_id: str,
        level: int,
        col: int,
        row: int,
        pil_img: object,
    ) -> None:
        key = TileKey(image_id, level, col, row)
        self._requested.discard(key)
        self.tile_blob_loaded.emit(image_id, level, col, row, pil_img)
