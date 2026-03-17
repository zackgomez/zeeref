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

from PyQt6 import QtWidgets, QtCore
from PyQt6.QtCore import Qt

from zeeref import constants
from zeeref.actions.actions import actions
from zeeref.config import KeyboardSettings, settings_events


logger = logging.getLogger(__name__)


class KeyboardShortcutsEditor(QtWidgets.QKeySequenceEdit):
    def __init__(self, parent: QtWidgets.QWidget, index: QtCore.QModelIndex) -> None:
        super().__init__(parent)
        self.action = actions[index.row()]
        try:
            self.old_value: str = self.action.get_shortcuts()[index.column() - 2]
        except IndexError:
            self.old_value = ""
        self.setClearButtonEnabled(True)
        self.setMaximumSequenceLength(1)
        self.editingFinished.connect(self.on_editing_finished)
        self.finished_last_called_with: str | None = None
        self.remove_from_other: Any = None

    def on_editing_finished(self) -> None:
        """Don't let users save the same shortcuts on different actions."""

        shortcut = self.keySequence().toString()

        if self.finished_last_called_with == shortcut:
            # Workaround for bug
            # https://bugreports.qt.io/browse/QTBUG-40
            # editingFinished signal is emitted twice because of
            # the QMessageBox below
            return

        self.remove_from_other = None
        self.finished_last_called_with = shortcut
        for action in actions.values():
            if action == self.action:
                continue
            if shortcut in action.get_shortcuts():
                txt = ": ".join(action.menu_path + [action.text])
                txt = txt.replace("&", "").removesuffix("...")
                msg = (
                    "<p>This shortcut is already used for:</p>"
                    f"<p>{txt}</p>"
                    "<p>Do you want to remove the other shortcut"
                    " to save this one?</p>"
                )
                reply = QtWidgets.QMessageBox.question(self, "Save Shortcut?", msg)
                if reply == QtWidgets.QMessageBox.StandardButton.Yes:
                    self.remove_from_other = action
                else:
                    self.setKeySequence(self.old_value)


class KeyboardShortcutsDelegate(QtWidgets.QStyledItemDelegate):
    def createEditor(
        self,
        parent: QtWidgets.QWidget | None,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> QtWidgets.QWidget | None:
        assert parent is not None
        return KeyboardShortcutsEditor(parent, index)

    def setModelData(
        self,
        editor: QtWidgets.QWidget | None,
        model: QtCore.QAbstractItemModel | None,
        index: QtCore.QModelIndex,
    ) -> None:
        assert isinstance(editor, KeyboardShortcutsEditor)
        assert model is not None
        cast(KeyboardShortcutsModel, model).setData(
            index,
            editor.keySequence(),
            QtCore.Qt.ItemDataRole.EditRole,
            remove_from_other=editor.remove_from_other,
        )


class KeyboardShortcutsModel(QtCore.QAbstractTableModel):
    """An entry in the keyboard shortcuts table."""

    HEADER: tuple[str, ...] = (
        "Action",
        constants.CHANGED_SYMBOL,
        "Shortcut",
        "Alternative",
    )

    def __init__(self) -> None:
        super().__init__()
        self.settings = KeyboardSettings()

    def rowCount(self, parent: QtCore.QModelIndex | None = None) -> int:
        return len(actions)

    def columnCount(self, parent: QtCore.QModelIndex | None = None) -> int:
        return len(self.HEADER)

    def data(
        self, index: QtCore.QModelIndex, role: int = Qt.ItemDataRole.DisplayRole
    ) -> Any:
        if role in (
            QtCore.Qt.ItemDataRole.DisplayRole,
            QtCore.Qt.ItemDataRole.EditRole,
        ):
            action = actions[index.row()]
            txt = ": ".join(action.menu_path + [action.text])
            if index.column() == 0:
                return txt.replace("&", "").removesuffix("...")
            if index.column() == 1 and action.shortcuts_changed():
                return constants.CHANGED_SYMBOL
            if index.column() > 1:
                return action.get_qkeysequence(index.column() - 2)

        if role == QtCore.Qt.ItemDataRole.ToolTipRole:
            action = actions[index.row()]
            changed = action.shortcuts_changed()
            if changed and index.column() == 1:
                return "Changed from default"
            if changed and index.column() > 1:
                default = action.get_default_shortcut(index.column() - 2)
                default = default or "Not set"
                return f"Default: {default}"

        return None

    def setData(
        self,
        index: QtCore.QModelIndex,
        value: Any,
        role: int = Qt.ItemDataRole.EditRole,
        remove_from_other: Any = None,
    ) -> bool:
        action = actions[index.row()]
        shortcuts = action.get_shortcuts() + [None, None]
        shortcuts[index.column() - 2] = value.toString()
        shortcuts = list(filter(bool, shortcuts))
        if len(shortcuts) != len(set(shortcuts)):
            # We got the same shortcut twice
            shortcuts = set(shortcuts)
        action.set_shortcuts(shortcuts)
        # Whole row might be affected, so excpliclity emit dataChanged
        self.dataChanged.emit(self.index(index.row(), 1), self.index(index.row(), 3))

        if remove_from_other:
            # This shortcut has conflicts with another action and the
            # user chose to remove the other shortcut
            shortcuts = remove_from_other.get_shortcuts()
            shortcuts.remove(value.toString())
            remove_from_other.set_shortcuts(shortcuts)
            row = list(actions.keys()).index(remove_from_other.id)
            self.dataChanged.emit(self.index(row, 1), self.index(row, 3))

        return True

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> str | None:
        if (
            role == QtCore.Qt.ItemDataRole.DisplayRole
            and orientation == QtCore.Qt.Orientation.Horizontal
        ):
            return self.HEADER[section]
        return None

    def flags(self, index: QtCore.QModelIndex) -> Qt.ItemFlag:
        base = (
            QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemNeverHasChildren
        )
        if index.column() <= 1:
            return base
        else:
            return base | QtCore.Qt.ItemFlag.ItemIsEditable


class KeyboardShortcutsProxy(QtCore.QSortFilterProxyModel):
    def __init__(self) -> None:
        super().__init__()
        self.setSourceModel(KeyboardShortcutsModel())
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
        result: bool = cast(KeyboardShortcutsModel, source_model).setData(
            self.mapToSource(index), value, role, remove_from_other=remove_from_other
        )
        return result


class KeyboardShortcutsView(QtWidgets.QTableView):
    def __init__(self, parent: QtWidgets.QWidget) -> None:
        super().__init__(parent)
        self.setMinimumSize(QtCore.QSize(400, 200))
        self.setItemDelegate(KeyboardShortcutsDelegate())
        self.setShowGrid(False)
        self.setModel(KeyboardShortcutsProxy())
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
