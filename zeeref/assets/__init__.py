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

from importlib.resources import files as rsc_files
import logging
from typing import cast

from PyQt6 import QtGui, QtWidgets


logger = logging.getLogger(__name__)


class ZeeAssets:
    _instance = None
    PATH = rsc_files("zeeref.assets")

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super().__new__(cls, *args, **kwargs)
            cls._instance.on_new()
        return cls._instance

    def on_new(self):
        logger.debug(f"Assets path: {self.PATH}")

        self.logo = QtGui.QIcon(str(self.PATH.joinpath("logo.png")))
        assert self.logo.isNull() is False
        self.cursor_rotate = self.cursor_from_image("cursor_rotate.png", (20, 20))
        self.cursor_flip_h = self.cursor_from_image("cursor_flip_h.png", (20, 20))
        self.cursor_flip_v = self.cursor_from_image("cursor_flip_v.png", (20, 20))

    def cursor_from_image(self, filename, hotspot):
        app = cast(QtWidgets.QApplication, QtWidgets.QApplication.instance())
        screen = app.primaryScreen()
        assert screen is not None
        scaling = screen.devicePixelRatio()
        img = QtGui.QImage(str(self.PATH.joinpath(filename)))
        assert img.isNull() is False
        pixmap = QtGui.QPixmap.fromImage(img)
        pixmap.setDevicePixelRatio(scaling)
        return QtGui.QCursor(
            pixmap, int(hotspot[0] / scaling), int(hotspot[1] / scaling)
        )
