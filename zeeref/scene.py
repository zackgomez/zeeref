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
import math
from pathlib import Path
from queue import Queue
from typing import TYPE_CHECKING, Any, Iterator, Literal, cast, overload

from PyQt6 import QtCore, QtWidgets, QtGui
from PyQt6.QtCore import Qt

import rpack

from zeeref import commands
from zeeref.config import ZeeSettings
from zeeref.types.snapshot import ItemSnapshot
from zeeref.items import (
    ZeeItemMixin,
    ZeePixmapItem,
    ZeeTextItem,
    item_registry,
    ZeeErrorItem,
    sort_by_filename,
)
from zeeref.selection import MultiSelectItem, RubberbandItem

if TYPE_CHECKING:
    from zeeref.view import ZeeGraphicsView


logger = logging.getLogger(__name__)


class ZeeGraphicsScene(QtWidgets.QGraphicsScene):
    cursor_changed = QtCore.pyqtSignal(QtGui.QCursor)
    cursor_cleared = QtCore.pyqtSignal()

    MOVE_MODE = 1
    RUBBERBAND_MODE = 2

    def __init__(self, undo_stack: QtGui.QUndoStack) -> None:
        super().__init__()
        self.active_mode: int | None = None
        self.undo_stack: QtGui.QUndoStack = undo_stack
        self._scratch_file: Path | None = None
        self.max_z: float = 0
        self.min_z: float = 0
        self.Z_STEP: float = 0.001
        self.selectionChanged.connect(self.on_selection_change)
        self.changed.connect(self.on_change)
        self.items_to_add: Queue[tuple[dict[str, Any], bool]] = Queue()
        self.edit_item: ZeeTextItem | None = None
        self.crop_item: ZeePixmapItem | None = None
        self.internal_clipboard: list[ZeeItemMixin] = []
        self.event_start: QtCore.QPointF = QtCore.QPointF()
        self.settings: ZeeSettings = ZeeSettings()
        self.clear()
        self._clear_ongoing: bool = False

    def clear(self) -> None:
        self._clear_ongoing = True
        super().clear()
        self.internal_clipboard: list[ZeeItemMixin] = []
        self.rubberband_item: RubberbandItem = RubberbandItem()
        self.multi_select_item: MultiSelectItem = MultiSelectItem()
        self._clear_ongoing = False

    def addItem(self, item: QtWidgets.QGraphicsItem) -> None:
        logger.debug(f"Adding item {item}")
        super().addItem(item)

    def removeItem(self, item: QtWidgets.QGraphicsItem) -> None:
        logger.debug(f"Removing item {item}")
        super().removeItem(item)

    def cancel_active_modes(self) -> None:
        """Cancels ongoing crop modes, rubberband modes etc, if there are
        any.
        """
        self.cancel_crop_mode()
        self.end_rubberband_mode()

    def end_rubberband_mode(self) -> None:
        if self.rubberband_item.scene():
            logger.debug("Ending rubberband selection")
            self.removeItem(self.rubberband_item)
        self.active_mode = None

    def cancel_crop_mode(self) -> None:
        """Cancels an ongoing crop mode, if there is any."""
        if self.crop_item:
            self.crop_item.exit_crop_mode(confirm=False)

    def copy_selection_to_internal_clipboard(self) -> None:
        self.internal_clipboard = []
        for item in self.selectedItems(user_only=True):
            self.internal_clipboard.append(item)

    def paste_from_internal_clipboard(self, position: QtCore.QPointF) -> None:
        copies = []
        for item in self.internal_clipboard:
            copy = item.create_copy()
            copies.append(copy)
        self.undo_stack.push(commands.InsertItems(self, copies, position))

    def raise_to_top(self) -> None:
        self.cancel_active_modes()
        items = self.selectedItems(user_only=True)
        z_values = map(lambda i: i.zValue(), items)
        delta = self.max_z + self.Z_STEP - min(z_values)
        logger.debug(f"Raise to top, delta: {delta}")
        for item in items:
            item.setZValue(item.zValue() + delta)

    def lower_to_bottom(self) -> None:
        self.cancel_active_modes()
        items = self.selectedItems(user_only=True)
        z_values = map(lambda i: i.zValue(), items)
        delta = self.min_z - self.Z_STEP - max(z_values)
        logger.debug(f"Lower to bottom, delta: {delta}")

        for item in items:
            item.setZValue(item.zValue() + delta)

    def normalize_width_or_height(self, mode: str) -> None:
        """Scale the selected images to have the same width or height, as
        specified by ``mode``.

        :param mode: "width" or "height".
        """

        self.cancel_active_modes()
        values = []
        items = self.selectedItems(user_only=True)
        for item in items:
            rect = self.itemsBoundingRect(items=[item])
            values.append(getattr(rect, mode)())
        if len(values) < 2:
            return
        avg = sum(values) / len(values)
        logger.debug(f"Calculated average {mode} {avg}")

        scale_factors = []
        for item in items:
            rect = self.itemsBoundingRect(items=[item])
            scale_factors.append(avg / getattr(rect, mode)())
        self.undo_stack.push(commands.NormalizeItems(items, scale_factors))

    def normalize_height(self) -> None:
        """Scale selected images to the same height."""
        self.normalize_width_or_height("height")

    def normalize_width(self) -> None:
        """Scale selected images to the same width."""
        self.normalize_width_or_height("width")

    def normalize_size(self) -> None:
        """Scale selected images to the same size.

        Size meaning the area = widh * height.
        """

        self.cancel_active_modes()
        sizes = []
        items = self.selectedItems(user_only=True)
        for item in items:
            rect = self.itemsBoundingRect(items=[item])
            sizes.append(rect.width() * rect.height())

        if len(sizes) < 2:
            return

        avg = sum(sizes) / len(sizes)
        logger.debug(f"Calculated average size {avg}")

        scale_factors = []
        for item in items:
            rect = self.itemsBoundingRect(items=[item])
            scale_factors.append(math.sqrt(avg / rect.width() / rect.height()))
        self.undo_stack.push(commands.NormalizeItems(items, scale_factors))

    def arrange_default(self) -> None:
        default = self.settings.valueOrDefault("Items/arrange_default")
        MAPPING = {
            "optimal": self.arrange_optimal,
            "horizontal": self.arrange,
            "vertical": partial(self.arrange, vertical=True),
            "square": self.arrange_square,
        }

        MAPPING[default]()

    def arrange(self, vertical: bool = False) -> None:
        """Arrange items in a line (horizontally or vertically)."""

        self.cancel_active_modes()

        items = sort_by_filename(self.selectedItems(user_only=True))
        if len(items) < 2:
            return

        gap = self.settings.valueOrDefault("Items/arrange_gap")
        center = self.get_selection_center()
        positions = []
        rects = []
        for item in items:
            rects.append({"rect": self.itemsBoundingRect(items=[item]), "item": item})

        if vertical:
            rects.sort(key=lambda r: r["rect"].topLeft().y())
            sum_height = sum(map(lambda r: r["rect"].height(), rects))
            y = round(center.y() - sum_height / 2)
            for rect in rects:
                positions.append(
                    QtCore.QPointF(round(center.x() - rect["rect"].width() / 2), y)
                )
                y += rect["rect"].height() + gap

        else:
            rects.sort(key=lambda r: r["rect"].topLeft().x())
            sum_width = sum(map(lambda r: r["rect"].width(), rects))
            x = round(center.x() - sum_width / 2)
            for rect in rects:
                positions.append(
                    QtCore.QPointF(x, round(center.y() - rect["rect"].height() / 2))
                )
                x += rect["rect"].width() + gap

        self.undo_stack.push(
            commands.ArrangeItems(self, [r["item"] for r in rects], positions)
        )

    def arrange_optimal(self) -> None:
        self.cancel_active_modes()

        items = self.selectedItems(user_only=True)
        if len(items) < 2:
            return

        gap = self.settings.valueOrDefault("Items/arrange_gap")

        sizes = []
        for item in items:
            rect = self.itemsBoundingRect(items=[item])
            sizes.append((round(rect.width() + gap), round(rect.height() + gap)))

        # The minimal area the items need if they could be packed optimally;
        # we use this as a starting shape for the packing algorithm
        min_area = sum(map(lambda s: s[0] * s[1], sizes))
        width = math.ceil(math.sqrt(min_area))

        positions = None
        while not positions:
            try:
                positions = rpack.pack(sizes, max_width=width, max_height=width)
            except rpack.PackingImpossibleError:
                width = math.ceil(width * 1.2)

        # We want the items to center around the selection's center,
        # not (0, 0)
        center = self.get_selection_center()
        bounds = rpack.bbox_size(sizes, positions)
        diff = center - QtCore.QPointF(bounds[0] / 2, bounds[1] / 2)
        positions = [QtCore.QPointF(*pos) + diff for pos in positions]

        self.undo_stack.push(commands.ArrangeItems(self, items, positions))

    def arrange_square(self) -> None:
        self.cancel_active_modes()
        max_width = 0
        max_height = 0
        gap = self.settings.valueOrDefault("Items/arrange_gap")
        items = sort_by_filename(self.selectedItems(user_only=True))

        if len(items) < 2:
            return

        for item in items:
            rect = self.itemsBoundingRect(items=[item])
            max_width = max(max_width, rect.width() + gap)
            max_height = max(max_height, rect.height() + gap)

        # We want the items to center around the selection's center,
        # not (0, 0)
        num_rows = math.ceil(math.sqrt(len(items)))
        center = self.get_selection_center()
        diff = center - num_rows / 2 * QtCore.QPointF(max_width, max_height)

        iter_items = iter(items)
        positions = []
        for j in range(num_rows):
            for i in range(num_rows):
                try:
                    item = next(iter_items)
                    rect = self.itemsBoundingRect(items=[item])
                    point = QtCore.QPointF(
                        i * max_width + (max_width - rect.width()) / 2,
                        j * max_height + (max_height - rect.height()) / 2,
                    )
                    positions.append(point + diff)
                except StopIteration:
                    break

        self.undo_stack.push(commands.ArrangeItems(self, items, positions))

    def flip_items(self, vertical: bool = False) -> None:
        """Flip selected items."""
        self.cancel_active_modes()
        self.undo_stack.push(
            commands.FlipItems(
                self.selectedItems(user_only=True),
                self.get_selection_center(),
                vertical=vertical,
            )
        )

    def crop_items(self) -> None:
        """Crop selected item."""

        if self.crop_item:
            return
        if self.has_single_image_selection():
            item = self.selectedItems(user_only=True)[0]
            if isinstance(item, ZeePixmapItem):
                item.enter_crop_mode()

    def sample_color_at(self, position: QtCore.QPointF) -> QtGui.QColor | None:
        item_at_pos = self.itemAt(position, self.views()[0].transform())
        if item_at_pos:
            return cast(Any, item_at_pos).sample_color_at(position)
        return None

    def select_all_items(self) -> None:
        self.cancel_active_modes()
        path = QtGui.QPainterPath()
        path.addRect(self.itemsBoundingRect())
        # This is faster than looping through all items and calling setSelected
        self.setSelectionArea(path)

    def deselect_all_items(self) -> None:
        self.cancel_active_modes()
        self.clearSelection()

    def has_selection(self) -> bool:
        """Checks whether there are currently items selected."""

        return bool(self.selectedItems(user_only=True))

    def has_single_selection(self) -> bool:
        """Checks whether there's currently exactly one item selected."""

        return len(self.selectedItems(user_only=True)) == 1

    def has_multi_selection(self) -> bool:
        """Checks whether there are currently more than one items selected."""

        return len(self.selectedItems(user_only=True)) > 1

    def has_single_image_selection(self) -> bool:
        """Checks whether the current selection is a single image."""

        if self.has_single_selection():
            return self.selectedItems(user_only=True)[0].is_image
        return False

    def mousePressEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent | None) -> None:
        assert event is not None
        if event.button() == Qt.MouseButton.RightButton:
            # Right-click invokes the context menu on the
            # GraphicsView. We don't need it here.
            return

        if event.button() == Qt.MouseButton.LeftButton:
            self.event_start = event.scenePos()
            item_at_pos = self.itemAt(event.scenePos(), self.views()[0].transform())

            if self.edit_item:
                if item_at_pos != self.edit_item:
                    self.edit_item.exit_edit_mode()
                else:
                    super().mousePressEvent(event)
                    return
            if self.crop_item:
                if item_at_pos != self.crop_item:
                    self.cancel_crop_mode()
                else:
                    super().mousePressEvent(event)
                    return
            if item_at_pos:
                self.active_mode = self.MOVE_MODE
            elif self.items():
                self.active_mode = self.RUBBERBAND_MODE

        super().mousePressEvent(event)

    def mouseDoubleClickEvent(
        self, event: QtWidgets.QGraphicsSceneMouseEvent | None
    ) -> None:
        self.cancel_active_modes()
        assert event is not None
        item = self.itemAt(event.scenePos(), self.views()[0].transform())
        if item:
            if not item.isSelected():
                item.setSelected(True)
            bee_item = cast(Any, item)
            if bee_item.is_editable:
                bee_item.enter_edit_mode()
                self.mousePressEvent(event)
            else:
                view = cast("ZeeGraphicsView", self.views()[0])
                view.fit_rect(self.itemsBoundingRect(items=[item]), toggle_item=item)
            return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent | None) -> None:
        assert event is not None
        if self.active_mode == self.RUBBERBAND_MODE:
            if not self.rubberband_item.scene():
                logger.debug("Activating rubberband selection")
                self.addItem(self.rubberband_item)
                self.rubberband_item.bring_to_front()
            self.rubberband_item.fit(self.event_start, event.scenePos())
            self.setSelectionArea(self.rubberband_item.shape())
            cast("ZeeGraphicsView", self.views()[0]).reset_previous_transform()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(
        self, event: QtWidgets.QGraphicsSceneMouseEvent | None
    ) -> None:
        assert event is not None
        if self.active_mode == self.RUBBERBAND_MODE:
            self.end_rubberband_mode()
        if (
            self.active_mode == self.MOVE_MODE
            and self.has_selection()
            and self.multi_select_item.active_mode is None
            and self.selectedItems(user_only=True)[0].active_mode is None
        ):
            delta = event.scenePos() - self.event_start
            if not delta.isNull():
                self.undo_stack.push(
                    commands.MoveItemsBy(
                        self.selectedItems(), delta, ignore_first_redo=True
                    )
                )
        self.active_mode = None
        super().mouseReleaseEvent(event)

    @overload
    def selectedItems(self, user_only: Literal[True]) -> list[ZeeItemMixin]: ...

    @overload
    def selectedItems(
        self, user_only: Literal[False] = ...
    ) -> list[QtWidgets.QGraphicsItem]: ...

    def selectedItems(
        self, user_only: bool = False
    ) -> list[ZeeItemMixin] | list[QtWidgets.QGraphicsItem]:
        """If ``user_only`` is set to ``True``, only return items added
        by the user (i.e. no multi select outlines and other UI items).

        User items are items that have a ``save_id`` attribute.
        """

        items = super().selectedItems()
        if user_only:
            return [i for i in items if isinstance(i, ZeeItemMixin)]
        return items

    def items_by_type(self, itype: str) -> Iterator[ZeeItemMixin]:
        """Returns all items of the given type."""

        return (
            i for i in self.items() if isinstance(i, ZeeItemMixin) and i.TYPE == itype
        )

    def user_items(self) -> list[ZeeItemMixin]:
        """Returns user-created items (excludes internal Qt items)."""
        return [
            i
            for i in self.items(order=Qt.SortOrder.AscendingOrder)
            if isinstance(i, ZeeItemMixin)
        ]

    def snapshot_for_save(self) -> list[ItemSnapshot]:
        """Snapshot all user items for thread-safe saving."""
        return [item.snapshot() for item in self.user_items()]

    def on_view_scale_change(self) -> None:
        for item in self.selectedItems(user_only=True):
            item.on_view_scale_change()

    def itemsBoundingRect(
        self,
        selection_only: bool = False,
        items: list[Any] | None = None,
    ) -> QtCore.QRectF:
        """Returns the bounding rect of the scene's items; either all of them
        or only selected ones, or the items givin in ``items``.

        Re-implemented to not include the items's selection handles.
        """

        def filter_user_items(ilist: list[Any]) -> list[ZeeItemMixin]:
            return [i for i in ilist if isinstance(i, ZeeItemMixin)]

        if selection_only:
            base = filter_user_items(self.selectedItems())
        elif items:
            base = items
        else:
            base = filter_user_items(self.items())

        if not base:
            return QtCore.QRectF(0, 0, 0, 0)

        x = []
        y = []

        for item in base:
            for corner in item.corners_scene_coords:
                x.append(corner.x())
                y.append(corner.y())

        return QtCore.QRectF(
            QtCore.QPointF(min(x), min(y)), QtCore.QPointF(max(x), max(y))
        )

    def get_selection_center(self) -> QtCore.QPointF:
        rect = self.itemsBoundingRect(selection_only=True)
        return (rect.topLeft() + rect.bottomRight()) / 2

    def on_selection_change(self) -> None:
        if self._clear_ongoing:
            # Ignore events while clearing the scene since the
            # multiselect item will get cleared, too
            return
        if self.has_multi_selection():
            self.multi_select_item.fit_selection_area(
                self.itemsBoundingRect(selection_only=True)
            )
        if self.has_multi_selection() and not self.multi_select_item.scene():
            self.addItem(self.multi_select_item)
            self.multi_select_item.bring_to_front()
        if not self.has_multi_selection() and self.multi_select_item.scene():
            self.removeItem(self.multi_select_item)

    def on_change(self, region: list[QtCore.QRectF]) -> None:
        if self._clear_ongoing:
            # Ignore events while clearing the scene since the
            # multiselect item will get cleared, too
            return
        if (
            self.multi_select_item.scene()
            and self.multi_select_item.active_mode is None
        ):
            self.multi_select_item.fit_selection_area(
                self.itemsBoundingRect(selection_only=True)
            )

    def add_item_later(self, itemdata: dict[str, Any], selected: bool = False) -> None:
        """Keep an item for adding later via ``add_queued_items``

        :param dict itemdata: Defines the item's data
        :param bool selected: Whether the item is initialised as selected
        """

        self.items_to_add.put((itemdata, selected))

    def add_queued_items(self) -> None:
        """Adds items added via ``add_item_later``"""

        while not self.items_to_add.empty():
            data, selected = self.items_to_add.get()
            typ = data.pop("type")
            cls = item_registry.get(typ)
            if not cls:
                # Just in case we add new item types in future versions
                logger.warning(f"Encountered item of unknown type: {typ}")
                cls = ZeeErrorItem
                data["data"] = {"text": f"Item of unknown type: {typ}"}
            item = cast(Any, cls).create_from_data(**data)
            # Set the values common to all item types:
            item.update_from_data(**data)
            self.addItem(item)
            # Force recalculation of min/max z values:
            item.setZValue(item.zValue())
            if selected:
                item.setSelected(True)
                item.bring_to_front()
