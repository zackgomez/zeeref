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
import os.path
from functools import partial
from PyQt6 import QtGui, QtWidgets
from PyQt6.QtCore import Qt

from zeeref.config import ZeeSettings

logger = logging.getLogger(__name__)


class WelcomeOverlay(QtWidgets.QWidget):
    """Info displayed when the scene is empty, with recent file buttons."""

    txt = """<p>Paste or drop images here.</p>
             <p>Right-click for more options.</p>"""

    def __init__(self, parent: QtWidgets.QWidget):
        super().__init__(parent)
        from zeeref.view import ZeeGraphicsView

        view = parent.parent()
        assert isinstance(view, ZeeGraphicsView)
        self.view: ZeeGraphicsView = view
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        lyt = QtWidgets.QVBoxLayout()
        lyt.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.label = QtWidgets.QLabel(self.txt)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lyt.addStretch(50)
        lyt.addWidget(self.label, alignment=Qt.AlignmentFlag.AlignCenter)

        self.recent_header = QtWidgets.QLabel("Recent Files")
        self.recent_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.recent_header.setStyleSheet("QLabel { color: #888; margin-top: 16px; }")
        lyt.addWidget(self.recent_header, alignment=Qt.AlignmentFlag.AlignCenter)

        self.recent_layout = QtWidgets.QVBoxLayout()
        self.recent_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lyt.addLayout(self.recent_layout)
        lyt.addStretch(50)

        self.setLayout(lyt)
        self._rebuild_recent_files()

    def showEvent(self, event: QtGui.QShowEvent | None) -> None:
        super().showEvent(event)
        self._rebuild_recent_files()

    def _rebuild_recent_files(self) -> None:
        while self.recent_layout.count():
            item = self.recent_layout.takeAt(0)
            w = item.widget() if item else None
            if w:
                w.deleteLater()

        files = ZeeSettings().get_recent_files(existing_only=True)[:3]
        self.recent_header.setVisible(bool(files))

        for filepath in files:
            name = os.path.basename(filepath)
            btn = QtWidgets.QPushButton(name)
            btn.setToolTip(filepath)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setMinimumWidth(300)
            btn.setStyleSheet("QPushButton { font-weight: normal; }")
            btn.clicked.connect(partial(self._on_recent_clicked, filepath))
            self.recent_layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)

    def _on_recent_clicked(self, filepath: str) -> None:
        self.view.on_action_open_recent_file(filepath)

    def mousePressEvent(self, event: QtGui.QMouseEvent | None) -> None:
        if event:
            event.ignore()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent | None) -> None:
        if event:
            event.ignore()

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent | None) -> None:
        if event:
            event.ignore()
