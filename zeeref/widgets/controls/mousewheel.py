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

from functools import partial
import logging
from typing import Any, cast

from PyQt6 import QtWidgets, QtCore
from PyQt6.QtCore import Qt

from zeeref.config import KeyboardSettings, settings_events
from zeeref.config.controls import MouseWheelConfig
from zeeref.widgets.controls.common import (
    MouseControlsEditorBase,
    MouseControlsModelBase,
)


logger = logging.getLogger(__name__)


class MouseWheelModifiersEditor(MouseControlsEditorBase):
    def __init__(self, parent: QtWidgets.QWidget, index: QtCore.QModelIndex) -> None:
        self.init_dialog(
            parent,
            index,
            KeyboardSettings.MOUSEWHEEL_ACTIONS,
            "MouseWheel Controls for:",
        )
        self.init_modifiers_input()
        self.init_button_row()
        self.show()

    def get_temp_action(self) -> MouseWheelConfig:
        return MouseWheelConfig(
            modifiers=self.get_modifiers(),
            group=None,
            text=None,
            invertible=None,
            id=None,
        )

    def reset_inputs(self) -> None:
        self.set_modifiers(self.old_modifiers)


class MouseWheelDelegate(QtWidgets.QStyledItemDelegate):
    def createEditor(
        self,
        parent: QtWidgets.QWidget | None,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> MouseWheelModifiersEditor:
        assert parent is not None
        editor = MouseWheelModifiersEditor(parent, index)
        editor.saved.connect(partial(self.setModelData, editor, index.model(), index))
        return editor

    def setModelData(
        self,
        editor: QtWidgets.QWidget | None,
        model: QtCore.QAbstractItemModel | None,
        index: QtCore.QModelIndex,
    ) -> None:
        assert isinstance(editor, MouseWheelModifiersEditor)
        assert model is not None
        if editor.result() == QtWidgets.QDialog.DialogCode.Accepted:
            cast(MouseWheelModel, model).setData(
                index,
                editor.get_modifiers(),
                QtCore.Qt.ItemDataRole.EditRole,
                remove_from_other=editor.remove_from_other,
            )


class MouseWheelModel(MouseControlsModelBase):
    """An entry in the keyboard shortcuts table."""

    COLUMNS = (
        MouseControlsModelBase.COL_ACTION,
        MouseControlsModelBase.COL_CHANGED,
        MouseControlsModelBase.COL_MODIFIERS,
        MouseControlsModelBase.COL_INVERTED,
    )

    def __init__(self) -> None:
        super().__init__(KeyboardSettings.MOUSEWHEEL_ACTIONS)

    def set_data_on_action(self, action: Any, value: Any) -> None:
        action.set_modifiers(value)


class MouseWheelProxy(QtCore.QSortFilterProxyModel):
    def __init__(self) -> None:
        super().__init__()
        self.setSourceModel(MouseWheelModel())
        self.setFilterCaseSensitivity(QtCore.Qt.CaseSensitivity.CaseInsensitive)

    def setData(
        self,
        index: QtCore.QModelIndex,
        value: Any,
        role: int = Qt.ItemDataRole.EditRole,
        remove_from_other: Any = None,
    ) -> bool:
        source_model = self.sourceModel()
        assert source_model is not None
        result: bool = cast(MouseWheelModel, source_model).setData(
            self.mapToSource(index), value, role, remove_from_other=remove_from_other
        )
        return result


class MouseWheelView(QtWidgets.QTableView):
    def __init__(self, parent: QtWidgets.QWidget) -> None:
        super().__init__(parent)
        self.setMinimumSize(QtCore.QSize(400, 200))
        self.setItemDelegate(MouseWheelDelegate())
        self.setShowGrid(False)
        self.setModel(MouseWheelProxy())
        header = self.horizontalHeader()
        assert header is not None
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(
            1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        self.setSelectionMode(QtWidgets.QHeaderView.SelectionMode.SingleSelection)
        self.setAlternatingRowColors(True)
        settings_events.restore_defaults.connect(self.on_restore_defaults)

    def on_restore_defaults(self) -> None:
        viewport = self.viewport()
        assert viewport is not None
        viewport.update()
