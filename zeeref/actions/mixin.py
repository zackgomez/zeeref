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

from collections import defaultdict
from functools import partial
import os.path
from typing import TYPE_CHECKING, Any

from PyQt6 import QtGui, QtWidgets

from .actions import Action, actions
from .menu_structure import menu_structure, MENU_SEPARATOR

if TYPE_CHECKING:
    from zeeref.config import ZeeSettings

    _ActionsMixinBase = QtWidgets.QWidget
else:
    _ActionsMixinBase = object


class ActionsMixin(_ActionsMixinBase):
    settings: ZeeSettings
    on_action_open_recent_file: Any
    zee_actiongroups: defaultdict[str, list[QtGui.QAction]]
    context_menu: QtWidgets.QMenu
    _post_create_functions: list[tuple[Any, Any]]
    _recent_files_submenu: QtWidgets.QMenu

    def actiongroup_set_enabled(self, group: str, value: bool) -> None:
        for action in self.zee_actiongroups[group]:
            action.setEnabled(value)

    def build_menu_and_actions(self) -> None:
        """Creates a new menu or rebuilds the given menu."""
        self.context_menu = QtWidgets.QMenu(self)
        self.zee_actiongroups = defaultdict(list)
        self._post_create_functions = []
        self._create_actions()
        self._create_menu(self.context_menu, menu_structure)

        for func, arg in self._post_create_functions:
            func(arg)
        del self._post_create_functions

    def update_menu_and_actions(self) -> None:
        self._build_recent_files()

    def _store_checkable_setting(self, key: str, value: bool) -> None:
        self.settings.setValue(key, value)

    def _init_action_checkable(self, actiondef: Action, qaction: QtGui.QAction) -> None:
        qaction.setCheckable(True)
        assert actiondef.callback is not None
        callback = getattr(self, actiondef.callback)
        qaction.toggled.connect(callback)
        settings_key = actiondef.settings
        checked = actiondef.checked
        qaction.setChecked(checked)
        if settings_key:
            val = self.settings.value(settings_key, checked, type=bool)
            qaction.setChecked(val)
            self._post_create_functions.append((callback, val))
            qaction.toggled.connect(
                partial(self._store_checkable_setting, settings_key)
            )

    def _create_actions(self) -> None:
        for action in actions.values():
            qaction = QtGui.QAction(action.text, self)
            qaction.setAutoRepeat(False)
            shortcuts = action.get_shortcuts()
            if shortcuts:
                qaction.setShortcuts(shortcuts)
            # ZeeRef's right-click menu is the primary menu (no menubar);
            # show shortcuts even on macOS where HIG hides them by default.
            qaction.setShortcutVisibleInContextMenu(True)
            if action.checkable:
                self._init_action_checkable(action, qaction)
            else:
                qaction.triggered.connect(getattr(self, action.callback))
            self.addAction(qaction)
            qaction.setEnabled(action.enabled)
            if action.group:
                self.zee_actiongroups[action.group].append(qaction)
                qaction.setEnabled(False)
            action.qaction = qaction

    def _create_menu(self, menu: QtWidgets.QMenu, items: Any) -> QtWidgets.QMenu:
        if isinstance(items, str):
            getattr(self, items)(menu)
            return menu
        for item in items:
            if isinstance(item, str):
                menu.addAction(actions[item].qaction)
            if item == MENU_SEPARATOR:
                menu.addSeparator()
            if isinstance(item, dict):
                submenu = menu.addMenu(item["menu"])
                self._create_menu(submenu, item["items"])

        return menu

    def _build_recent_files(self, menu: QtWidgets.QMenu | None = None) -> None:
        if menu:
            self._recent_files_submenu = menu
        self._clear_recent_files()

        files = self.settings.get_recent_files(existing_only=True)
        items: list[str] = []

        for i in range(10):
            action_id = f"recent_files_{i}"
            key = 0 if i == 9 else i + 1
            action = Action(
                id=action_id,
                menu_id="_build_recent_files",
                text=f"File {i + 1}",
                shortcuts=[f"Ctrl+{key}"],
            )
            actions[action_id] = action

            if i < len(files):
                filename = files[i]
                qaction = QtGui.QAction(os.path.basename(filename), self)
                qaction.setShortcuts(action.get_shortcuts())
                qaction.setShortcutVisibleInContextMenu(True)
                qaction.triggered.connect(
                    partial(self.on_action_open_recent_file, filename)
                )
                self.addAction(qaction)
                action.qaction = qaction
                self._recent_files_submenu.addAction(qaction)
                items.append(action_id)

    def _clear_recent_files(self) -> None:
        for action in self._recent_files_submenu.actions():
            self.removeAction(action)
        self._recent_files_submenu.clear()
        for key in list(actions.keys()):
            if key.startswith("recent_files_"):
                actions[key].qaction = None
