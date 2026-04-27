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
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt

from zeeref import commands, widgets
from zeeref.items import ZeePixmapItem
from zeeref import fileio

if TYPE_CHECKING:
    from zeeref.view import ZeeGraphicsView

    _MainControlsBase = QtWidgets.QGraphicsView
else:
    _MainControlsBase = object


logger = logging.getLogger(__name__)


@dataclass
class _DeferredMoveState:
    """Press captured for a button whose click could mean something other
    than 'move the window'.  Resolves into either move-window mode (on
    drag) or the alternate action (on no-drag release, e.g. context menu).
    """

    start_pos: QtCore.QPointF
    button: Qt.MouseButton
    can_movewin: bool


class MainControlsMixin(_MainControlsBase):
    """Basic controls shared by the main view and the welcome overlay:

    * Right-click menu
    * Dropping files
    * Moving the window without title bar
    """

    control_target: ZeeGraphicsView
    main_window: QtWidgets.QMainWindow
    event_start: QtCore.QPointF
    movewin_active: bool
    deferred_move_state: _DeferredMoveState | None

    def init_main_controls(self, main_window: QtWidgets.QMainWindow) -> None:
        self.main_window = main_window
        # We manage right-click behavior ourselves so we can distinguish
        # a plain click (open context menu) from a drag (move window).
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.setAcceptDrops(True)
        self.movewin_active = False
        self.deferred_move_state = None

    def on_action_movewin_mode(self) -> None:
        if self.movewin_active:
            # Pressing the same shortcut again should end the action
            self.exit_movewin_mode()
        else:
            self.enter_movewin_mode()

    def enter_movewin_mode(self) -> None:
        logger.debug("Entering movewin mode")
        self.setMouseTracking(True)
        self.movewin_active = True
        vp = self.viewport()
        assert vp is not None
        vp.setCursor(Qt.CursorShape.SizeAllCursor)
        self.event_start = QtCore.QPointF(self.cursor().pos())

    def exit_movewin_mode(self) -> None:
        logger.debug("Exiting movewin mode")
        self.setMouseTracking(False)
        self.movewin_active = False
        vp = self.viewport()
        assert vp is not None
        vp.unsetCursor()

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent | None) -> None:
        assert event is not None
        mimedata = event.mimeData()
        assert mimedata is not None
        logger.debug(f"Drag enter event: {mimedata.formats()}")
        if mimedata.hasUrls():
            event.acceptProposedAction()
        elif mimedata.hasImage():
            event.acceptProposedAction()
        else:
            msg = "Attempted drop not an image or image too big"
            logger.info(msg)
            widgets.ZeeNotification(self.control_target, msg)

    def dragMoveEvent(self, event: QtGui.QDragMoveEvent | None) -> None:
        assert event is not None
        event.acceptProposedAction()

    def dropEvent(self, event: QtGui.QDropEvent | None) -> None:
        assert event is not None
        mimedata = event.mimeData()
        assert mimedata is not None
        logger.debug(f"Handling file drop: {mimedata.formats()}")
        pos = QtCore.QPoint(round(event.position().x()), round(event.position().y()))
        if mimedata.hasUrls():
            logger.debug(f"Found dropped urls: {mimedata.urls()}")
            target = self.control_target
            if not target.scene.items():
                # Check if we have a bee file we can open directly
                url = mimedata.urls()[0]
                if url.isLocalFile():
                    local_path = Path(url.toLocalFile())
                    if fileio.is_zref_file(local_path):
                        target.open_from_file(local_path)
                        return
            target.do_insert_images(mimedata.urls(), pos)
        elif mimedata.hasImage():
            img = QtGui.QImage(mimedata.imageData())
            item = ZeePixmapItem(img)
            pos = self.control_target.mapToScene(pos)
            self.control_target.undo_stack.push(
                commands.InsertItems(self.control_target.scene, [item], pos)
            )
        else:
            logger.info("Drop not an image")

    def mousePressEventMainControls(self, event: QtGui.QMouseEvent) -> bool | None:
        if self.movewin_active:
            self.exit_movewin_mode()
            event.accept()
            return True

        action, inverted = self.control_target.keyboard_settings.mouse_action_for_event(
            event
        )
        is_right = event.button() == Qt.MouseButton.RightButton
        is_movewin = action == "movewindow"

        # Defer the press if either the button has another meaning on a
        # plain click (right → context menu) or the binding is movewindow
        # on a button whose plain click might mean something to the scene
        # (e.g. ⌘+left selection toggle).  A drag past the threshold flips
        # into move-window mode; release without crossing the threshold
        # falls back to the alternate action.
        if is_right or is_movewin:
            self.deferred_move_state = _DeferredMoveState(
                start_pos=event.position(),
                button=event.button(),
                can_movewin=is_movewin,
            )
            event.accept()
            return True

    def mouseMoveEventMainControls(self, event: QtGui.QMouseEvent) -> bool | None:
        if self.movewin_active:
            # Use globalPosition() directly from the event rather than
            # mapToGlobal(event.position()).  After main_window.move() the
            # widget's cached global origin may lag behind the real window
            # position (async window-manager roundtrip on X11/Wayland),
            # which makes mapToGlobal() return a stale value and causes the
            # window to jitter or flash.
            pos = event.globalPosition()
            delta = pos - self.event_start
            self.event_start = pos
            self.main_window.move(
                self.main_window.x() + round(delta.x()),
                self.main_window.y() + round(delta.y()),
            )
            event.accept()
            return True

        if self.deferred_move_state and self.deferred_move_state.can_movewin:
            delta = event.position() - self.deferred_move_state.start_pos
            if delta.manhattanLength() >= 2:
                self.deferred_move_state = None
                self.enter_movewin_mode()
                event.accept()
                return True

    def mouseReleaseEventMainControls(self, event: QtGui.QMouseEvent) -> bool | None:
        if self.movewin_active:
            self.exit_movewin_mode()
            event.accept()
            return True

        if (
            self.deferred_move_state
            and event.button() == self.deferred_move_state.button
        ):
            state = self.deferred_move_state
            self.deferred_move_state = None

            if state.button == Qt.MouseButton.RightButton:
                # Plain right-click (no drag) — open context menu.
                pos = event.position()
                self.control_target.on_context_menu(
                    QtCore.QPoint(int(pos.x()), int(pos.y()))
                )
            elif state.button == Qt.MouseButton.LeftButton:
                # Plain left+modifier click on an item — toggle its
                # selection state, mirroring QGraphicsView's default
                # ⌘/Ctrl+click behaviour that we suppressed by accepting
                # the press.
                point = QtCore.QPoint(
                    int(state.start_pos.x()), int(state.start_pos.y())
                )
                item = self.control_target.itemAt(point)
                if item is not None:
                    item.setSelected(not item.isSelected())

            event.accept()
            return True

    def keyPressEventMainControls(self, event: QtGui.QKeyEvent) -> bool | None:
        if self.movewin_active:
            self.exit_movewin_mode()
            event.accept()
            return True
