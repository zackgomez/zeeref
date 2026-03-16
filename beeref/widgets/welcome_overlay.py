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

import logging

from PyQt6 import QtGui, QtWidgets
from PyQt6.QtCore import Qt

from beeref.config import BeeSettings


logger = logging.getLogger(__name__)


class WelcomeOverlay(QtWidgets.QWidget):
    """Some basic info to be displayed when the scene is empty.

    This widget is purely visual — it sets WA_TransparentForMouseEvents
    so all mouse events fall through to the BeeGraphicsView underneath.
    """

    txt = """<p>Paste or drop images here.</p>
             <p>Right-click for more options.</p>"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAutoFillBackground(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.update_background_color()

    def update_background_color(self):
        canvas_color = BeeSettings().valueOrDefault('View/canvas_color')
        palette = self.palette()
        palette.setColor(self.backgroundRole(), QtGui.QColor(canvas_color))
        self.setPalette(palette)

        # Help text
        self.label = QtWidgets.QLabel(self.txt, self)
        self.label.setAlignment(Qt.AlignmentFlag.AlignVCenter
                                | Qt.AlignmentFlag.AlignCenter)
        self.layout = QtWidgets.QHBoxLayout()
        self.layout.addStretch(50)
        self.layout.addWidget(self.label)
        self.layout.addStretch(50)
        self.setLayout(self.layout)
