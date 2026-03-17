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
from typing import Any, cast

from PyQt6 import QtCore, QtWidgets

from zeeref.config import KeyboardSettings
from zeeref.widgets.controls.keyboard import KeyboardShortcutsView
from zeeref.widgets.controls.mouse import MouseView
from zeeref.widgets.controls.mousewheel import MouseWheelView


logger = logging.getLogger(__name__)


class ControlsDialog(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Keyboard & Mouse Controls")
        tabs = QtWidgets.QTabWidget()

        # Keyboard shortcuts
        keyboard = QtWidgets.QWidget(parent)
        kb_layout = QtWidgets.QVBoxLayout()
        keyboard.setLayout(kb_layout)
        table = KeyboardShortcutsView(keyboard)
        search_input = QtWidgets.QLineEdit()
        search_input.setPlaceholderText("Search...")
        kb_proxy = cast(QtCore.QSortFilterProxyModel, table.model())
        search_input.textChanged.connect(kb_proxy.setFilterFixedString)
        kb_layout.addWidget(search_input)
        kb_layout.addWidget(table)
        tabs.addTab(keyboard, "&Keyboard Shortcuts")

        # Mouse controls
        mouse = QtWidgets.QWidget(parent)
        mouse_layout = QtWidgets.QVBoxLayout()
        mouse.setLayout(mouse_layout)
        table = MouseView(mouse)
        search_input = QtWidgets.QLineEdit()
        search_input.setPlaceholderText("Search...")
        mouse_proxy = cast(QtCore.QSortFilterProxyModel, table.model())
        search_input.textChanged.connect(mouse_proxy.setFilterFixedString)
        mouse_layout.addWidget(search_input)
        mouse_layout.addWidget(table)
        tabs.addTab(mouse, "&Mouse")

        # Mouse wheel controls
        mousewheel = QtWidgets.QWidget(parent)
        wheel_layout = QtWidgets.QVBoxLayout()
        mousewheel.setLayout(wheel_layout)
        table = MouseWheelView(mousewheel)
        search_input = QtWidgets.QLineEdit()
        search_input.setPlaceholderText("Search...")
        wheel_proxy = cast(QtCore.QSortFilterProxyModel, table.model())
        search_input.textChanged.connect(wheel_proxy.setFilterFixedString)
        wheel_layout.addWidget(search_input)
        wheel_layout.addWidget(table)
        tabs.addTab(mousewheel, "Mouse &Wheel")

        layout = QtWidgets.QVBoxLayout()
        self.setLayout(layout)
        layout.addWidget(tabs)

        # Bottom row of buttons
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Close
        )
        buttons.rejected.connect(self.reject)
        reset_btn = QtWidgets.QPushButton("&Restore Defaults")
        reset_btn.setAutoDefault(False)
        reset_btn.clicked.connect(self.on_restore_defaults)
        buttons.addButton(reset_btn, QtWidgets.QDialogButtonBox.ButtonRole.ActionRole)

        layout.addWidget(buttons)
        self.show()

    def on_restore_defaults(self, *args: Any, **kwargs: Any) -> None:
        reply = QtWidgets.QMessageBox.question(
            self,
            "Restore defaults?",
            "Do you want to restore all keyboard and mouse settings "
            "to their default values?",
        )

        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            KeyboardSettings().restore_defaults()
