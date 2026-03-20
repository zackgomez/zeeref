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

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6 import QtCore, QtGui, QtWidgets

from zeeref.config import ZeeSettings
from .errors import ZeeFileIOError
from zeeref.types.snapshot import IOResult
from zeeref import widgets
from zeeref.logging import getLogger

if TYPE_CHECKING:
    from zeeref.fileio.thread import ThreadedIO
    from zeeref.scene import ZeeGraphicsScene


logger = getLogger(__name__)


class ExporterBase:
    def emit_begin_processing(self, worker: ThreadedIO | None, start: int) -> None:
        if worker:
            worker.begin_processing.emit(start)

    def emit_progress(self, worker: ThreadedIO | None, progress: int) -> None:
        if worker:
            worker.progress.emit(progress)

    def emit_finished(
        self, worker: ThreadedIO | None, filename: Path, errors: list[str]
    ) -> None:

        if worker:
            worker.finished.emit(IOResult(filename=filename, errors=errors))

    def emit_user_input_required(self, worker: ThreadedIO | None, msg: str) -> None:
        if worker:
            worker.user_input_required.emit(msg)

    def handle_export_error(
        self, filename: Path, error: Exception | str, worker: ThreadedIO | None
    ) -> None:

        logger.debug(f"Export failed: {error}")
        if worker:
            worker.finished.emit(IOResult(filename=filename, errors=[str(error)]))
            return
        else:
            e = error if isinstance(error, Exception) else None
            raise ZeeFileIOError(msg=str(error), filename=filename) from e


class SceneExporterBase(ExporterBase):
    """For exporting the scene to a single image."""

    def get_user_input(self, parent: QtWidgets.QWidget) -> bool:
        """Ask user for export parameters. Override in subclasses."""
        raise NotImplementedError

    def export(self, filename: Path, worker: ThreadedIO | None = None) -> None:
        """Export the scene. Override in subclasses."""
        raise NotImplementedError

    def __init__(self, scene: ZeeGraphicsScene) -> None:
        self.scene: ZeeGraphicsScene = scene
        self.scene.cancel_active_modes()
        self.scene.deselect_all_items()
        # Selection outlines/handles will be rendered to the exported
        # image, so deselect first. (Alternatively, pass an attribute
        # to paint functions to not paint them?)
        rect = self.scene.itemsBoundingRect()
        logger.trace(f"Items bounding rect: {rect}")
        size = QtCore.QSize(int(rect.width()), int(rect.height()))
        logger.trace(f"Export size without margins: {size}")
        self.margin: float = max(size.width(), size.height()) * 0.03
        self.default_size: QtCore.QSize = size.grownBy(
            QtCore.QMargins(*([int(self.margin)] * 4))
        )
        logger.debug(f"Default export margin: {self.margin}")
        logger.debug(f"Default export size with margins: {self.default_size}")


class ExporterRegistry(dict[str | int, type[SceneExporterBase]]):
    DEFAULT_TYPE = 0

    def __getitem__(self, key: str | int) -> type[SceneExporterBase]:
        if isinstance(key, str):
            key = key.removeprefix(".")
        exp = self.get(key, super().__getitem__(self.DEFAULT_TYPE))
        logger.debug(f"Exporter for type {key}: {exp}")
        return exp


exporter_registry = ExporterRegistry()


def register_exporter[T: type[SceneExporterBase]](cls: T) -> T:
    exporter_registry[cls.TYPE] = cls
    return cls


@register_exporter
class SceneToPixmapExporter(SceneExporterBase):
    TYPE = ExporterRegistry.DEFAULT_TYPE

    def get_user_input(self, parent: QtWidgets.QWidget) -> bool:
        """Ask user for final export size."""

        dialog = widgets.SceneToPixmapExporterDialog(
            parent=parent,
            default_size=self.default_size,
        )
        if dialog.exec():
            size = dialog.value()
            logger.debug(f"Got export size {size}")
            self.size = size
            return True
        else:
            return False

    def render_to_image(self) -> QtGui.QImage:
        logger.debug(f"Final export size: {self.size}")
        margin = self.margin * self.size.width() / self.default_size.width()
        logger.debug(f"Final export margin: {margin}")

        image = QtGui.QImage(self.size, QtGui.QImage.Format.Format_RGB32)
        canvas_color = ZeeSettings().valueOrDefault("View/canvas_color")
        image.fill(QtGui.QColor(canvas_color))
        painter = QtGui.QPainter(image)
        target_rect = QtCore.QRectF(
            margin,
            margin,
            self.size.width() - 2 * margin,
            self.size.height() - 2 * margin,
        )
        logger.trace(f"Final export target_rect: {target_rect}")
        self.scene.render(
            painter, source=self.scene.itemsBoundingRect(), target=target_rect
        )
        painter.end()
        return image

    def export(self, filename: Path, worker: ThreadedIO | None = None) -> None:
        logger.debug(f"Exporting scene to {filename}")
        self.emit_begin_processing(worker, 1)
        image = self.render_to_image()

        if worker and worker.canceled:
            logger.debug("Export canceled")
            self.emit_finished(worker, filename, [])
            return

        if not image.save(str(filename), quality=90):
            self.handle_export_error(filename, "Error writing file", worker)
            return

        logger.debug("Export finished")
        self.emit_progress(worker, 1)
        self.emit_finished(worker, filename, [])
