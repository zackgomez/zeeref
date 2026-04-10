#!/usr/bin/env python3

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

import logging
import os
import platform
import signal
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Optional, cast

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt, QEvent

if TYPE_CHECKING:
    pass

from zeeref import constants
from zeeref.assets import ZeeAssets
from zeeref.config import CommandlineArgs, ZeeSettings, logfile_name
from zeeref.fileio.scratch import delete_scratch_file
from zeeref.utils import create_palette_from_dict
from zeeref.view import ZeeGraphicsView

logger = logging.getLogger(__name__)


class ZeeRefApplication(QtWidgets.QApplication):
    def event(self, event: Optional[QtCore.QEvent]) -> bool:
        assert event is not None
        if event.type() == QtCore.QEvent.Type.FileOpen:
            file_event = cast(QtGui.QFileOpenEvent, event)
            for widget in self.topLevelWidgets():
                if isinstance(widget, ZeeRefMainWindow):
                    widget.view.open_from_file(Path(file_event.file()))
                    return True
            return False
        else:
            return super().event(event)


class ZeeRefMainWindow(QtWidgets.QMainWindow):
    RESIZE_BORDER = 6

    def __init__(self, app):
        super().__init__()
        app.setOrganizationName(constants.APPNAME)
        app.setApplicationName(constants.APPNAME)
        self.setWindowIcon(ZeeAssets().logo)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self._resize_edges = Qt.Edge(0)
        self.view = ZeeGraphicsView(app, self)
        default_window_size = QtCore.QSize(500, 300)
        geom = self.view.settings.value("MainWindow/geometry")
        if geom is None:
            self.resize(default_window_size)
        else:
            if not self.restoreGeometry(geom):
                self.resize(default_window_size)
        self.setCentralWidget(self.view)
        self.show()

    def _edges_at(self, pos: QtCore.QPoint) -> Qt.Edge:
        b = self.RESIZE_BORDER
        edges = Qt.Edge(0)
        if pos.x() < b:
            edges |= Qt.Edge.LeftEdge
        if pos.x() > self.width() - b:
            edges |= Qt.Edge.RightEdge
        if pos.y() < b:
            edges |= Qt.Edge.TopEdge
        if pos.y() > self.height() - b:
            edges |= Qt.Edge.BottomEdge
        return edges

    _EDGE_CURSORS: dict[Qt.Edge, Qt.CursorShape] = {
        Qt.Edge.LeftEdge: Qt.CursorShape.SizeHorCursor,
        Qt.Edge.RightEdge: Qt.CursorShape.SizeHorCursor,
        Qt.Edge.TopEdge: Qt.CursorShape.SizeVerCursor,
        Qt.Edge.BottomEdge: Qt.CursorShape.SizeVerCursor,
        Qt.Edge.LeftEdge | Qt.Edge.TopEdge: Qt.CursorShape.SizeFDiagCursor,
        Qt.Edge.RightEdge | Qt.Edge.BottomEdge: Qt.CursorShape.SizeFDiagCursor,
        Qt.Edge.RightEdge | Qt.Edge.TopEdge: Qt.CursorShape.SizeBDiagCursor,
        Qt.Edge.LeftEdge | Qt.Edge.BottomEdge: Qt.CursorShape.SizeBDiagCursor,
    }

    def event(self, ev: Optional[QtCore.QEvent]) -> bool:
        assert ev is not None
        t = ev.type()
        if t == QEvent.Type.HoverMove:
            self._resize_edges = self._edges_at(ev.position().toPoint())  # type: ignore[union-attr]
            cursor = self._EDGE_CURSORS.get(self._resize_edges)
            if cursor:
                self.setCursor(cursor)
            else:
                self.unsetCursor()
        elif t == QEvent.Type.MouseButtonPress and self._resize_edges:
            wh = self.windowHandle()
            if wh:
                wh.startSystemResize(self._resize_edges)
        elif t in (QEvent.Type.HoverLeave, QEvent.Type.MouseButtonRelease):
            self._resize_edges = Qt.Edge(0)
            self.unsetCursor()
        return super().event(ev)

    def closeEvent(self, event: Optional[QtGui.QCloseEvent]) -> None:
        assert event is not None
        if not self.view.get_confirmation_unsaved_changes(
            "There are unsaved changes. Are you sure you want to quit?"
        ):
            event.ignore()
            return
        logger.info("Exiting...")
        try:
            self.view.scene.selectionChanged.disconnect(self.view.on_selection_changed)
        except (TypeError, RuntimeError):
            pass  # Already disconnected or scene deleted
        geom = self.saveGeometry()
        self.view.settings.setValue("MainWindow/geometry", geom)
        if self.view.scene._scratch_file:
            delete_scratch_file(self.view.scene._scratch_file)
            self.view.scene._scratch_file = None
        event.accept()

    def __del__(self):
        del self.view


def safe_timer(timeout, func, *args, **kwargs):
    """Create a timer that is safe against garbage collection and
    overlapping calls.
    See: http://ralsina.me/weblog/posts/BB974.html
    """

    def timer_event():
        try:
            func(*args, **kwargs)
        finally:
            QtCore.QTimer.singleShot(timeout, timer_event)

    QtCore.QTimer.singleShot(timeout, timer_event)


def handle_sigint(signum, frame):
    logger.info("Received interrupt. Exiting...")
    QtWidgets.QApplication.quit()


def handle_uncaught_exception(exc_type, exc, traceback):
    logger.critical("Unhandled exception", exc_info=(exc_type, exc, traceback))
    QtWidgets.QApplication.quit()


sys.excepthook = handle_uncaught_exception


def main():
    logger.info(f"Starting {constants.APPNAME} version {constants.VERSION}")
    logger.debug("System: %s", " ".join(platform.uname()))
    logger.debug("Python: %s", platform.python_version())
    logger.debug("LD_LIBRARY_PATH: %s", os.environ.get("LD_LIBRARY_PATH"))
    settings = ZeeSettings()
    logger.info(f"Using settings: {settings.fileName()}")
    logger.info(f"Logging to: {logfile_name()}")
    settings.on_startup()
    args = CommandlineArgs(with_check=True)  # Force checking
    assert not args.debug_raise_error, args.debug_raise_error

    os.environ["QT_DEBUG_PLUGINS"] = "1"
    fmt = QtGui.QSurfaceFormat()
    fmt.setRedBufferSize(8)
    fmt.setGreenBufferSize(8)
    fmt.setBlueBufferSize(8)
    fmt.setAlphaBufferSize(8)
    QtGui.QSurfaceFormat.setDefaultFormat(fmt)
    app = ZeeRefApplication(sys.argv)
    if sys.platform == "win32":
        app.setStyle("Fusion")
    palette = create_palette_from_dict(constants.COLORS)
    app.setPalette(palette)
    bee = ZeeRefMainWindow(app)  # NOQA:F841

    signal.signal(signal.SIGINT, handle_sigint)
    # Repeatedly run python-noop to give the interpreter time to
    # handle signals
    safe_timer(50, lambda: None)

    app.exec()
    del bee
    del app
    logger.debug("ZeeRef closed")
    QtCore.qInstallMessageHandler(None)


if __name__ == "__main__":
    main()  # pragma: no cover
