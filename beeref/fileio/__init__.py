# This file is part of BeeRef.
#
# BeeRef is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# BeeRef is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with BeeRef.  If not, see <https://www.gnu.org/licenses/>.

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PyQt6 import QtCore

from beeref import commands
from beeref.fileio.errors import BeeFileIOError
from beeref.fileio.image import load_image
from beeref.fileio.scratch import (
    create_scratch_file,
    delete_scratch_file,
    derive_swp_path,
    list_recovery_files,
)
from beeref.fileio.sql import SQLiteIO, is_bee_file
from beeref.fileio.snapshot import IOResult, ItemSnapshot, LoadResult, SaveResult

if TYPE_CHECKING:
    from beeref.scene import BeeGraphicsScene


__all__ = [
    "is_bee_file",
    "load_bee",
    "save_bee",
    "load_images",
    "ThreadedIO",
    "BeeFileIOError",
    "create_scratch_file",
    "delete_scratch_file",
    "derive_swp_path",
    "list_recovery_files",
]

logger = logging.getLogger(__name__)


def load_bee(
    filename: str, scene: BeeGraphicsScene, worker: ThreadedIO | None = None
) -> None:
    """Load BeeRef native file via scratch copy."""
    logger.info(f"Loading from file {filename}...")
    try:
        swp = create_scratch_file(filename, worker=worker)
    except Exception as e:
        logger.exception(f"Failed to create scratch file for {filename}")
        if worker:
            worker.finished.emit(LoadResult(filename=filename, errors=[str(e)]))
            return
        raise BeeFileIOError(msg=str(e), filename=filename) from e
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


def save_bee(
    filename: str,
    snapshots: list[ItemSnapshot],
    create_new: bool = False,
    worker: ThreadedIO | None = None,
) -> None:
    """Save BeeRef native file."""
    logger.info(f"Saving to file {filename}...")
    logger.debug(f"Create new: {create_new}")
    try:
        io = SQLiteIO(filename, create_new=create_new, worker=worker)
        newly_saved = io.write(snapshots)
    except BeeFileIOError as e:
        logger.exception(f"Failed to save {filename}")
        if worker:
            worker.finished.emit(SaveResult(filename=filename, errors=[str(e)]))
        return
    logger.info("End save")
    if worker:
        worker.finished.emit(
            SaveResult(
                filename=filename,
                newly_saved=newly_saved or [],
            )
        )


def load_images(filenames, pos, scene, worker):
    """Add images to existing scene."""
    from beeref.items import BeePixmapItem

    errors = []
    items = []
    worker.begin_processing.emit(len(filenames))
    for i, filename in enumerate(filenames):
        logger.info(f"Loading image from file {filename}")
        img, filename = load_image(filename)
        worker.progress.emit(i)
        if img.isNull():
            logger.info(f"Could not load file {filename}")
            errors.append(filename)
            continue

        item = BeePixmapItem(img, filename)
        item.set_pos_center(pos)
        scene.add_item_later({"item": item, "type": "pixmap"}, selected=True)
        items.append(item)
        if worker.canceled:
            break
        # Give main thread time to process items:
        worker.msleep(10)

    scene.undo_stack.push(commands.InsertItems(scene, items, ignore_first_redo=True))
    worker.finished.emit(IOResult(filename="", errors=errors))


class ThreadedIO(QtCore.QThread):
    """Dedicated thread for loading and saving."""

    progress = QtCore.pyqtSignal(int)
    finished = QtCore.pyqtSignal(IOResult)
    begin_processing = QtCore.pyqtSignal(int)
    user_input_required = QtCore.pyqtSignal(str)

    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.kwargs["worker"] = self
        self.canceled = False

    def run(self):
        self.func(*self.args, **self.kwargs)

    def on_canceled(self):
        self.canceled = True
