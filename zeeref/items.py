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

"""Classes for items that are added to the scene by the user (images,
text).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from functools import cached_property
import logging
import os.path
import time
import uuid
from typing import Any, cast

import mistune
from PIL import Image
from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt

from zeeref import commands
from zeeref.config import ZeeSettings
from zeeref.constants import COLORS
from zeeref.fileio.tilecache import get_tile_cache
from zeeref.fileio.tiling import TILE_SIZE
from zeeref.types.tile import TileKey
from zeeref.types.snapshot import ErrorItemSnapshot, ItemSnapshot, PixmapItemSnapshot
from zeeref.selection import SelectableMixin

logger = logging.getLogger(__name__)

item_registry: dict[str, type[ZeeItemMixin]] = {}


def register_item(cls: type[ZeeItemMixin]) -> type[ZeeItemMixin]:
    item_registry[cls.TYPE] = cls
    return cls


def create_item_from_snapshot(snap: ItemSnapshot) -> ZeeItemMixin:
    """Create a scene item from a snapshot. Dispatches by type.

    If the factory raises, returns a ZeeErrorItem preserving the
    item's position and save_id for future recovery.
    """
    cls = item_registry.get(snap.type)
    if cls is None:
        err = ZeeErrorItem(f"Item of unknown type: {snap.type}")
        err.save_id = snap.save_id
        err.setPos(snap.x, snap.y)
        err.setZValue(snap.z)
        return err

    try:
        return cls.from_snapshot(snap)
    except Exception as e:
        logger.exception(f"Failed to create {snap.type} from snapshot")
        filename = snap.data.get("filename", "unknown")
        err = ZeeErrorItem(f"Failed to load {snap.type}: {filename}\n{e}")
        err.save_id = snap.save_id
        err.setPos(snap.x, snap.y)
        err.setZValue(snap.z)
        return err


def sort_by_filename(items: list[ZeeItemMixin]) -> list[ZeeItemMixin]:
    """Order items by filename.

    Items with a filename (ordered by filename) first, then remaining
    items ordered by creation time.
    """

    items_by_filename: list[ZeeItemMixin] = []
    items_remaining: list[ZeeItemMixin] = []

    for item in items:
        if getattr(item, "filename", None):
            items_by_filename.append(item)
        else:
            items_remaining.append(item)

    items_by_filename.sort(key=lambda x: x.filename)
    items_remaining.sort(key=lambda x: x.created_at)
    return items_by_filename + items_remaining


class ZeeItemMixin(SelectableMixin):
    """Base for all items added by the user."""

    TYPE: str
    save_id: str
    created_at: float
    filename: str | None
    is_image: bool

    def get_extra_save_data(self) -> dict[str, Any]:
        """Return type-specific data for JSON serialization. Override in subclasses."""
        return {}

    def create_copy(self) -> ZeeItemMixin:
        """Create a copy of this item. Override in subclasses."""
        raise NotImplementedError

    def copy_to_clipboard(self, clipboard: QtGui.QClipboard) -> None:
        """Copy this item to the system clipboard. Override in subclasses."""
        raise NotImplementedError

    @classmethod
    def from_snapshot(cls, snap: ItemSnapshot) -> ZeeItemMixin:
        """Create an item from a snapshot. Override in subclasses."""
        raise NotImplementedError

    def set_pos_center(self, pos: QtCore.QPointF) -> None:
        """Sets the position using the item's center as the origin point."""

        self.setPos(pos - self.center_scene_coords)

    def has_selection_outline(self) -> bool:
        return self.isSelected()

    def has_selection_handles(self) -> bool:
        scene = self.zee_scene()
        return self.isSelected() and scene is not None and scene.has_single_selection()

    def selection_action_items(self) -> list[Any]:
        """The items affected by selection actions like scaling and rotating."""
        return [self]

    def snapshot(self) -> ItemSnapshot:
        """Create an immutable snapshot of this item for thread-safe saving."""
        return ItemSnapshot(
            save_id=self.save_id,
            type=self.TYPE,
            x=self.pos().x(),
            y=self.pos().y(),
            z=self.zValue(),
            scale=self.scale(),
            rotation=self.rotation(),
            flip=self.flip(),
            data=self.get_extra_save_data(),
            created_at=self.created_at,
        )

    def on_selected_change(self, value: Any) -> None:
        scene = self.zee_scene()
        if (
            value
            and scene
            and not scene.has_selection()
            and scene.active_mode is not None
        ):
            self.bring_to_front()

    def update_from_data(self, **kwargs: Any) -> None:
        self.save_id = kwargs.get("save_id", self.save_id)
        self.created_at = kwargs.get("created_at", self.created_at)
        self.setPos(kwargs.get("x", self.pos().x()), kwargs.get("y", self.pos().y()))
        self.setZValue(kwargs.get("z", self.zValue()))
        self.setScale(kwargs.get("scale", self.scale()))
        self.setRotation(kwargs.get("rotation", self.rotation()))
        if kwargs.get("flip", 1) != self.flip():
            self.do_flip()


@register_item
class ZeePixmapItem(ZeeItemMixin, QtWidgets.QGraphicsPixmapItem):
    """Class for images added by the user."""

    TYPE = "pixmap"
    CROP_HANDLE_SIZE: int = 15

    crop_temp: QtCore.QRectF | None
    crop_mode_move: Callable[[], QtCore.QRectF] | None
    crop_mode_event_start: QtCore.QPointF | None

    def __init__(
        self, image: QtGui.QImage, filename: str | None = None, **kwargs: Any
    ) -> None:
        super().__init__(QtGui.QPixmap.fromImage(image))
        self.save_id: str = uuid.uuid4().hex
        self.created_at: float = time.time()
        self.filename = filename
        self.is_image = True
        self.crop_mode: bool = False
        self._placeholder: bool = False
        self._subscribed: bool = False
        self._tile_children: dict[TileKey, QtWidgets.QGraphicsPixmapItem] = {}
        self._current_level: int = 0
        pm = self.pixmap()
        self._image_width: int = pm.width()
        self._image_height: int = pm.height()
        self.reset_crop()
        self.image_id: str = uuid.uuid4().hex
        self.init_selectable()
        self.settings = ZeeSettings()

    def snapshot(self) -> PixmapItemSnapshot:
        """Create an immutable snapshot. Tile data lives in the .swp."""
        return PixmapItemSnapshot(
            save_id=self.save_id,
            type=self.TYPE,
            x=self.pos().x(),
            y=self.pos().y(),
            z=self.zValue(),
            scale=self.scale(),
            rotation=self.rotation(),
            flip=self.flip(),
            data=self.get_extra_save_data(),
            created_at=self.created_at,
            image_id=self.image_id,
            width=self._image_width,
            height=self._image_height,
        )

    @classmethod
    def create_from_data(cls, **kwargs: Any) -> ZeePixmapItem:
        item: ZeePixmapItem = kwargs.pop("item")
        data: dict[str, Any] = kwargs.pop("data", {})
        item.filename = item.filename or data.get("filename")
        if "crop" in data:
            item.crop = QtCore.QRectF(*data["crop"])
        item.setOpacity(data.get("opacity", 1))
        return item

    @classmethod
    def from_snapshot(cls, snap: PixmapItemSnapshot) -> ZeePixmapItem:
        """Create a placeholder ZeePixmapItem from a loaded snapshot.

        Tile data is loaded on demand via the TileCache.
        """
        item = cls(QtGui.QImage())
        item._placeholder = True
        item._image_width = snap.width
        item._image_height = snap.height
        item._crop = QtCore.QRectF(0, 0, snap.width, snap.height)
        item.save_id = snap.save_id
        item.created_at = snap.created_at
        item.image_id = snap.image_id
        item.filename = snap.data.get("filename")
        if "crop" in snap.data:
            item.crop = QtCore.QRectF(*snap.data["crop"])
        item.setOpacity(snap.data.get("opacity", 1))
        item.setPos(snap.x, snap.y)
        item.setZValue(snap.z)
        item.setScale(snap.scale)
        item.setRotation(snap.rotation)
        if snap.flip != item.flip():
            item.do_flip()
        return item

    def __str__(self) -> str:
        suffix = " (placeholder)" if self._placeholder else ""
        return f'Image "{self.filename}" {self._image_width} x {self._image_height}{suffix}'

    @property
    def crop(self) -> QtCore.QRectF:
        return self._crop

    @crop.setter
    def crop(self, value: QtCore.QRectF) -> None:
        logger.debug(f"Setting crop for {self} to {value}")
        self.prepareGeometryChange()
        self._crop = value
        self.update()

    def sample_color_at(self, pos: QtCore.QPointF) -> QtGui.QColor | None:
        if self._placeholder:
            return None
        ipos = self.mapFromScene(pos)
        img = self.pixmap().toImage()

        color = img.pixelColor(int(ipos.x()), int(ipos.y()))
        if color.alpha():
            return color
        return None

    def bounding_rect_unselected(self) -> QtCore.QRectF:
        if self.crop_mode:
            return QtWidgets.QGraphicsPixmapItem.boundingRect(self)
        else:
            return self.crop

    def get_extra_save_data(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "opacity": self.opacity(),
            "crop": [
                self.crop.topLeft().x(),
                self.crop.topLeft().y(),
                self.crop.width(),
                self.crop.height(),
            ],
        }

    def get_filename_for_export(
        self, imgformat: str, save_id_default: str | None = None
    ) -> str:
        save_id = self.save_id or save_id_default
        assert save_id is not None

        short_id = save_id[:8]
        if self.filename:
            basename = os.path.splitext(os.path.basename(self.filename))[0]
            return f"{short_id}-{basename}.{imgformat}"
        else:
            return f"{short_id}.{imgformat}"

    def get_imgformat(self, img: QtGui.QImage) -> str:
        """Determines the format for storing this image."""

        formt = self.settings.valueOrDefault("Items/image_storage_format")

        if formt == "best":
            # Images with alpha channel and small images are stored as png
            if img.hasAlphaChannel() or (img.height() < 500 and img.width() < 500):
                formt = "png"
            else:
                formt = "jpg"

        logger.debug(f"Found format {formt} for {self}")
        return formt

    def pixmap_to_bytes(self, apply_crop: bool = False) -> tuple[bytes, str]:
        """Convert the pixmap data to PNG bytestring."""
        barray = QtCore.QByteArray()
        buffer = QtCore.QBuffer(barray)
        buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
        pm = self.pixmap()

        if apply_crop:
            pm = pm.copy(self.crop.toRect())

        img = pm.toImage()
        imgformat = self.get_imgformat(img)
        img.save(buffer, imgformat.upper(), quality=90)
        return (barray.data(), imgformat)

    def _qpixmap_to_pil(self, pixmap: QtGui.QPixmap) -> Image.Image:
        """Convert a QPixmap to a PIL Image."""
        img = pixmap.toImage()
        if img.hasAlphaChannel():
            img = img.convertToFormat(QtGui.QImage.Format.Format_RGBA8888)
            mode = "RGBA"
        else:
            img = img.convertToFormat(QtGui.QImage.Format.Format_RGB888)
            mode = "RGB"
        ptr = img.constBits()
        assert ptr is not None
        ptr.setsize(img.sizeInBytes())
        raw_bytes: bytes = bytes(cast(Any, ptr))
        return Image.frombytes(
            mode,
            (img.width(), img.height()),
            raw_bytes,
            "raw",
            mode,
            img.bytesPerLine(),
        )

    def _pil_to_qpixmap(self, pil_img: Image.Image) -> QtGui.QPixmap:
        """Convert a PIL Image to a QPixmap."""
        if pil_img.mode == "RGBA":
            fmt = QtGui.QImage.Format.Format_RGBA8888
            channels = 4
        else:
            fmt = QtGui.QImage.Format.Format_RGB888
            channels = 3
        data = pil_img.tobytes()
        stride = channels * pil_img.width
        qimg = QtGui.QImage(data, pil_img.width, pil_img.height, stride, fmt)
        return QtGui.QPixmap.fromImage(qimg.copy())

    def setPixmap(self, pixmap: QtGui.QPixmap) -> None:
        super().setPixmap(pixmap)
        self._image_width = pixmap.width()
        self._image_height = pixmap.height()
        self.reset_crop()

    @property
    def _max_level(self) -> int:
        if self._image_width == 0 or self._image_height == 0:
            return 0
        from math import floor, log2

        return max(
            0, floor(log2(max(self._image_width, self._image_height) / TILE_SIZE))
        )

    def _ensure_subscribed(self) -> None:
        """Lazily subscribe to tile cache on first visibility check."""
        if not self._subscribed:
            get_tile_cache().subscribe(self.image_id, self)
            self._subscribed = True

    def unsubscribe_tile_cache(self) -> None:
        """Unsubscribe from tile cache. Called on removal from scene."""
        if self._subscribed:
            get_tile_cache().unsubscribe(self.image_id, self)
            self._subscribed = False

    def update_visible_tiles(self) -> None:
        """Compute and request needed tiles for the current viewport.

        Called by the view for each visible item during viewport checks.
        """
        from math import ceil, floor, log2

        self._ensure_subscribed()

        # Compute effective scale (view zoom × item scale)
        scene = self.scene()
        if scene is None:
            return
        views = scene.views()
        if not views:
            return
        view_scale = abs(views[0].transform().m11())
        effective_scale = view_scale * self.scale()

        # Pick level
        if effective_scale > 0:
            level = max(0, floor(-log2(effective_scale)))
        else:
            level = 0
        level = min(level, self._max_level)

        # If level changed, remove old tile children
        if level != self._current_level:
            logger.info(
                f"Level change {self._current_level} -> {level} for {self.image_id[:8]} "
                f"(effective_scale={effective_scale:.4f}, view_scale={view_scale:.4f}, "
                f"item_scale={self.scale():.4f}, max_level={self._max_level})"
            )
            self._remove_all_tile_children()
            self._current_level = level

        # Compute tile grid dimensions at this level
        level_w = max(1, self._image_width >> level)
        level_h = max(1, self._image_height >> level)
        num_cols = ceil(level_w / TILE_SIZE)
        num_rows = ceil(level_h / TILE_SIZE)

        # Request all tiles at this level
        keys: set[TileKey] = set()
        for row in range(num_rows):
            for col in range(num_cols):
                keys.add(TileKey(self.image_id, level, col, row))
        get_tile_cache().request(keys)

    def _remove_all_tile_children(self) -> None:
        """Remove all tile child items from the scene."""
        for child in self._tile_children.values():
            scene = child.scene()
            if scene is not None:
                scene.removeItem(child)
        self._tile_children.clear()
        self._placeholder = True
        self.update()

    def on_tile_loaded(self, key: TileKey, pixmap: QtGui.QPixmap) -> None:
        # Ignore tiles for a different level than what we're currently showing
        if key.level != self._current_level:
            return
        # Create or update child pixmap item
        if key in self._tile_children:
            self._tile_children[key].setPixmap(pixmap)
        else:
            child = QtWidgets.QGraphicsPixmapItem(pixmap, self)
            child.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
            child.setFlag(
                QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False
            )
            # Position in image coords: tile covers TILE_SIZE pixels at the
            # level resolution, which maps to TILE_SIZE * 2^level in full-res
            scale_factor = 1 << key.level
            child.setPos(
                key.col * TILE_SIZE * scale_factor, key.row * TILE_SIZE * scale_factor
            )
            child.setScale(scale_factor)
            self._tile_children[key] = child
        self._placeholder = False
        logger.debug(f"Tile child added: {key}")

    def on_tile_unloaded(self, key: TileKey) -> None:
        child = self._tile_children.pop(key, None)
        if child is not None:
            scene = child.scene()
            if scene is not None:
                scene.removeItem(child)
            logger.debug(f"Tile child removed: {key}")
        if not self._tile_children:
            self._placeholder = True
            self.update()

    def create_copy(self) -> ZeePixmapItem:
        item = ZeePixmapItem(QtGui.QImage(), self.filename)
        item.setPixmap(self.pixmap())
        item.setPos(self.pos())
        item.setZValue(self.zValue())
        item.setScale(self.scale())
        item.setRotation(self.rotation())
        item.setOpacity(self.opacity())
        if self.flip() == -1:
            item.do_flip()
        item.crop = self.crop
        return item

    @cached_property
    def color_gamut(self) -> defaultdict[tuple[int, int], int]:
        logger.debug(f"Calculating color gamut for {self}")
        gamut: defaultdict[tuple[int, int], int] = defaultdict(int)
        if self._placeholder:
            return gamut
        img = self.pixmap().toImage()
        # Don't evaluate every pixel for larger images:
        step = max(1, int(max(img.width(), img.height()) / 1000))
        logger.debug(f"Considering every {step}. row/column")

        # Not actually faster than solution below :(
        # ptr = img.bits()
        # size = img.sizeInBytes()
        # pixelsize = int(img.sizeInBytes() / img.width() / img.height())
        # ptr.setsize(size)
        # for pixel in batched(ptr, n=pixelsize):
        #     r, g, b, alpha = tuple(map(ord, pixel))
        #     if 5 < alpha and 5 < r < 250 and 5 < g < 250 and 5 < b < 250:
        #         # Only consider pixels that aren't close to
        #         # transparent, white or black
        #         rgb = QtGui.QColor(r, g, b)
        #         gamut[rgb.hue(), rgb.saturation()] += 1

        for i in range(0, img.width(), step):
            for j in range(0, img.height(), step):
                rgb = img.pixelColor(i, j)
                rgbtuple = (rgb.red(), rgb.blue(), rgb.green())
                if 5 < rgb.alpha() and min(rgbtuple) < 250 and max(rgbtuple) > 5:
                    # Only consider pixels that aren't close to
                    # transparent, white or black
                    gamut[rgb.hue(), rgb.saturation()] += 1

        logger.debug(f"Got {len(gamut)} color gamut values")
        return gamut

    def copy_to_clipboard(self, clipboard: QtGui.QClipboard) -> None:
        clipboard.setPixmap(self.pixmap())

    def reset_crop(self) -> None:
        self.crop = QtCore.QRectF(0, 0, self._image_width, self._image_height)

    @property
    def crop_handle_size(self) -> float:
        return self.fixed_length_for_viewport(self.CROP_HANDLE_SIZE)

    def crop_handle_topleft(self) -> QtCore.QRectF:
        assert self.crop_temp is not None
        topleft = self.crop_temp.topLeft()
        return QtCore.QRectF(
            topleft.x(), topleft.y(), self.crop_handle_size, self.crop_handle_size
        )

    def crop_handle_bottomleft(self) -> QtCore.QRectF:
        assert self.crop_temp is not None
        bottomleft = self.crop_temp.bottomLeft()
        return QtCore.QRectF(
            bottomleft.x(),
            bottomleft.y() - self.crop_handle_size,
            self.crop_handle_size,
            self.crop_handle_size,
        )

    def crop_handle_bottomright(self) -> QtCore.QRectF:
        assert self.crop_temp is not None
        bottomright = self.crop_temp.bottomRight()
        return QtCore.QRectF(
            bottomright.x() - self.crop_handle_size,
            bottomright.y() - self.crop_handle_size,
            self.crop_handle_size,
            self.crop_handle_size,
        )

    def crop_handle_topright(self) -> QtCore.QRectF:
        assert self.crop_temp is not None
        topright = self.crop_temp.topRight()
        return QtCore.QRectF(
            topright.x() - self.crop_handle_size,
            topright.y(),
            self.crop_handle_size,
            self.crop_handle_size,
        )

    def crop_handles(
        self,
    ) -> tuple[
        Callable[[], QtCore.QRectF],
        Callable[[], QtCore.QRectF],
        Callable[[], QtCore.QRectF],
        Callable[[], QtCore.QRectF],
    ]:
        return (
            self.crop_handle_topleft,
            self.crop_handle_bottomleft,
            self.crop_handle_bottomright,
            self.crop_handle_topright,
        )

    def crop_edge_top(self) -> QtCore.QRectF:
        assert self.crop_temp is not None
        topleft = self.crop_temp.topLeft()
        return QtCore.QRectF(
            topleft.x() + self.crop_handle_size,
            topleft.y(),
            self.crop_temp.width() - 2 * self.crop_handle_size,
            self.crop_handle_size,
        )

    def crop_edge_left(self) -> QtCore.QRectF:
        assert self.crop_temp is not None
        topleft = self.crop_temp.topLeft()
        return QtCore.QRectF(
            topleft.x(),
            topleft.y() + self.crop_handle_size,
            self.crop_handle_size,
            self.crop_temp.height() - 2 * self.crop_handle_size,
        )

    def crop_edge_bottom(self) -> QtCore.QRectF:
        assert self.crop_temp is not None
        bottomleft = self.crop_temp.bottomLeft()
        return QtCore.QRectF(
            bottomleft.x() + self.crop_handle_size,
            bottomleft.y() - self.crop_handle_size,
            self.crop_temp.width() - 2 * self.crop_handle_size,
            self.crop_handle_size,
        )

    def crop_edge_right(self) -> QtCore.QRectF:
        assert self.crop_temp is not None
        topright = self.crop_temp.topRight()
        return QtCore.QRectF(
            topright.x() - self.crop_handle_size,
            topright.y() + self.crop_handle_size,
            self.crop_handle_size,
            self.crop_temp.height() - 2 * self.crop_handle_size,
        )

    def crop_edges(
        self,
    ) -> tuple[
        Callable[[], QtCore.QRectF],
        Callable[[], QtCore.QRectF],
        Callable[[], QtCore.QRectF],
        Callable[[], QtCore.QRectF],
    ]:
        return (
            self.crop_edge_top,
            self.crop_edge_left,
            self.crop_edge_bottom,
            self.crop_edge_right,
        )

    def get_crop_handle_cursor(
        self, handle: Callable[[], QtCore.QRectF]
    ) -> Qt.CursorShape:
        """Gets the crop cursor for the given handle."""

        is_topleft_or_bottomright = handle in (
            self.crop_handle_topleft,
            self.crop_handle_bottomright,
        )
        return self.get_diag_cursor(is_topleft_or_bottomright)

    def get_crop_edge_cursor(self, edge: Callable[[], QtCore.QRectF]) -> Qt.CursorShape:
        """Gets the crop edge cursor for the given edge."""

        top_or_bottom = edge in (self.crop_edge_top, self.crop_edge_bottom)
        sideways = 45 < self.rotation() < 135 or 225 < self.rotation() < 315

        if top_or_bottom is sideways:
            return Qt.CursorShape.SizeHorCursor
        else:
            return Qt.CursorShape.SizeVerCursor

    def draw_crop_rect(self, painter: QtGui.QPainter, rect: QtCore.QRectF) -> None:
        """Paint a dotted rectangle for the cropping UI."""
        pen = QtGui.QPen(QtGui.QColor(255, 255, 255))
        pen.setWidth(2)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawRect(rect)
        pen.setColor(QtGui.QColor(0, 0, 0))
        pen.setStyle(Qt.PenStyle.DotLine)
        painter.setPen(pen)
        painter.drawRect(rect)

    def has_selection_handles(self) -> bool:
        if self._placeholder:
            return False
        return super().has_selection_handles()

    def paint(
        self,
        painter: QtGui.QPainter | None,
        option: QtWidgets.QStyleOptionGraphicsItem | None,
        widget: QtWidgets.QWidget | None = None,
    ) -> None:
        assert painter is not None

        if self._placeholder:
            rect = self.crop
            fill = QtGui.QColor(128, 128, 128, 50)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QBrush(fill))
            painter.drawRect(rect)
            pen = QtGui.QPen(QtGui.QColor(128, 128, 128, 128))
            pen.setWidthF(1.0)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)

        # Tile children paint themselves via Qt's parent-child mechanism.
        # We only paint the selection overlay and crop UI here.

        if self.crop_mode:
            assert self.crop_temp is not None
            self.paint_debug(painter, option, widget)
            self.draw_crop_rect(painter, self.crop_temp)
            for handle in self.crop_handles():
                self.draw_crop_rect(painter, handle())

        self.paint_selectable(painter, option, widget)

    def enter_crop_mode(self) -> None:
        logger.debug(f"Entering crop mode on {self}")
        self.prepareGeometryChange()
        self.crop_mode = True
        self.crop_temp = QtCore.QRectF(self.crop)
        self.crop_mode_move: Callable[[], QtCore.QRectF] | None = None
        self.crop_mode_event_start: QtCore.QPointF | None = None
        self.grabKeyboard()
        self.update()
        self.require_scene().crop_item = self

    def exit_crop_mode(self, confirm: bool) -> None:
        logger.debug(f"Exiting crop mode with {confirm} on {self}")
        scene = self.require_scene()
        if confirm and self.crop != self.crop_temp:
            scene.undo_stack.push(commands.CropItem(self, self.crop_temp))
        self.prepareGeometryChange()
        self.crop_mode = False
        self.crop_temp = None
        self.crop_mode_move = None
        self.crop_mode_event_start = None
        self.ungrabKeyboard()
        self.update()
        scene.crop_item = None

    def keyPressEvent(self, event: QtGui.QKeyEvent | None) -> None:
        assert event is not None
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.exit_crop_mode(confirm=True)
        elif event.key() == Qt.Key.Key_Escape:
            self.exit_crop_mode(confirm=False)
        else:
            super().keyPressEvent(event)

    def hoverMoveEvent(self, event: QtWidgets.QGraphicsSceneHoverEvent | None) -> None:
        assert event is not None
        if not self.crop_mode:
            return super().hoverMoveEvent(event)

        for handle in self.crop_handles():
            if handle().contains(event.pos()):
                self.set_cursor(self.get_crop_handle_cursor(handle))
                return
        for edge in self.crop_edges():
            if edge().contains(event.pos()):
                self.set_cursor(self.get_crop_edge_cursor(edge))
                return
        self.unset_cursor()

    def mousePressEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent | None) -> None:
        assert event is not None
        if not self.crop_mode:
            return super().mousePressEvent(event)

        event.accept()
        for handle in self.crop_handles():
            # Click into a handle?
            if handle().contains(event.pos()):
                self.crop_mode_event_start = event.pos()
                self.crop_mode_move = handle
                return
        for edge in self.crop_edges():
            # Click into an edge handle?
            if edge().contains(event.pos()):
                self.crop_mode_event_start = event.pos()
                self.crop_mode_move = edge
                return
        # Click not in handle, end cropping mode:
        assert self.crop_temp is not None
        self.exit_crop_mode(confirm=self.crop_temp.contains(event.pos()))

    def ensure_point_within_crop_bounds(
        self, point: QtCore.QPointF, handle: Callable[[], QtCore.QRectF]
    ) -> QtCore.QPointF:
        """Returns the point, or the nearest point within the pixmap."""
        assert self.crop_temp is not None

        if handle == self.crop_handle_topleft:
            topleft = QtCore.QPointF(0, 0)
            bottomright = self.crop_temp.bottomRight()
        if handle == self.crop_handle_bottomleft:
            topleft = QtCore.QPointF(0, self.crop_temp.top())
            bottomright = QtCore.QPointF(
                self.crop_temp.right(), self.pixmap().size().height()
            )
        if handle == self.crop_handle_bottomright:
            topleft = self.crop_temp.topLeft()
            bottomright = QtCore.QPointF(
                self.pixmap().size().width(), self.pixmap().size().height()
            )
        if handle == self.crop_handle_topright:
            topleft = QtCore.QPointF(self.crop_temp.left(), 0)
            bottomright = QtCore.QPointF(
                self.pixmap().size().width(), self.crop_temp.bottom()
            )
        if handle == self.crop_edge_top:
            topleft = QtCore.QPointF(0, 0)
            bottomright = QtCore.QPointF(
                self.pixmap().size().width(), self.crop_temp.bottom()
            )
        if handle == self.crop_edge_bottom:
            topleft = QtCore.QPointF(0, self.crop_temp.top())
            bottomright = QtCore.QPointF(
                self.pixmap().size().width(), self.pixmap().size().height()
            )
        if handle == self.crop_edge_left:
            topleft = QtCore.QPointF(0, 0)
            bottomright = QtCore.QPointF(
                self.crop_temp.right(), self.pixmap().size().height()
            )
        if handle == self.crop_edge_right:
            topleft = QtCore.QPointF(self.crop_temp.left(), 0)
            bottomright = QtCore.QPointF(
                self.pixmap().size().width(), self.pixmap().size().height()
            )

        point.setX(min(bottomright.x(), max(topleft.x(), point.x())))
        point.setY(min(bottomright.y(), max(topleft.y(), point.y())))

        return point

    def mouseMoveEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent | None) -> None:
        assert event is not None
        if self.crop_mode and self.crop_mode_event_start is not None:
            assert self.crop_temp is not None
            diff = event.pos() - self.crop_mode_event_start
            if self.crop_mode_move == self.crop_handle_topleft:
                new = self.ensure_point_within_crop_bounds(
                    self.crop_temp.topLeft() + diff, self.crop_mode_move
                )
                self.crop_temp.setTopLeft(new)
            if self.crop_mode_move == self.crop_handle_bottomleft:
                new = self.ensure_point_within_crop_bounds(
                    self.crop_temp.bottomLeft() + diff, self.crop_mode_move
                )
                self.crop_temp.setBottomLeft(new)
            if self.crop_mode_move == self.crop_handle_bottomright:
                new = self.ensure_point_within_crop_bounds(
                    self.crop_temp.bottomRight() + diff, self.crop_mode_move
                )
                self.crop_temp.setBottomRight(new)
            if self.crop_mode_move == self.crop_handle_topright:
                new = self.ensure_point_within_crop_bounds(
                    self.crop_temp.topRight() + diff, self.crop_mode_move
                )
                self.crop_temp.setTopRight(new)
            if self.crop_mode_move == self.crop_edge_top:
                new = self.ensure_point_within_crop_bounds(
                    self.crop_temp.topLeft() + diff, self.crop_mode_move
                )
                self.crop_temp.setTop(new.y())
            if self.crop_mode_move == self.crop_edge_left:
                new = self.ensure_point_within_crop_bounds(
                    self.crop_temp.topLeft() + diff, self.crop_mode_move
                )
                self.crop_temp.setLeft(new.x())
            if self.crop_mode_move == self.crop_edge_bottom:
                new = self.ensure_point_within_crop_bounds(
                    self.crop_temp.bottomLeft() + diff, self.crop_mode_move
                )
                self.crop_temp.setBottom(new.y())
            if self.crop_mode_move == self.crop_edge_right:
                new = self.ensure_point_within_crop_bounds(
                    self.crop_temp.topRight() + diff, self.crop_mode_move
                )
                self.crop_temp.setRight(new.x())
            self.update()
            self.crop_mode_event_start = event.pos()
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(
        self, event: QtWidgets.QGraphicsSceneMouseEvent | None
    ) -> None:
        assert event is not None
        if self.crop_mode:
            self.crop_mode_move = None
            self.crop_mode_event_start = None
            event.accept()
        else:
            super().mouseReleaseEvent(event)


@register_item
class ZeeTextItem(ZeeItemMixin, QtWidgets.QGraphicsTextItem):
    """Class for markdown text added by the user."""

    TYPE = "text"

    STYLESHEET = """
        body { color: %s; }
        h1, h2, h3, h4, h5, h6 { margin: 4px 0; }
        code { background: rgba(255,255,255,0.1); padding: 1px 3px; }
        pre { background: rgba(255,255,255,0.1); padding: 4px; }
        a { color: #6aeae7; }
    """

    def __init__(self, text: str | None = None, **kwargs: Any) -> None:
        super().__init__()
        self.save_id: str = uuid.uuid4().hex
        self.created_at: float = time.time()
        self.is_image = False
        self.init_selectable()
        self.is_editable = True
        self.edit_mode: bool = False
        self._markdown: str = text or "Text"
        self._render_markdown()
        logger.debug(f"Initialized {self}")

    def _render_markdown(self) -> None:
        """Render stored markdown to HTML for display."""
        text_color = "rgb(%d,%d,%d)" % COLORS["Scene:Text"]
        css = self.STYLESHEET % text_color
        html = mistune.html(self._markdown)
        self.setHtml(f"<style>{css}</style>{html}")

    def set_markdown(self, text: str) -> None:
        """Set markdown source and re-render."""
        self._markdown = text
        self._render_markdown()

    @classmethod
    def create_from_data(cls, **kwargs: Any) -> ZeeTextItem:
        data: dict[str, Any] = kwargs.get("data", {})
        item = cls(**data)
        return item

    @classmethod
    def from_snapshot(cls, snap: ItemSnapshot) -> ZeeTextItem:
        """Create a ZeeTextItem from a loaded snapshot."""
        item = cls(snap.data.get("text"))
        item.save_id = snap.save_id
        item.created_at = snap.created_at
        item.setPos(snap.x, snap.y)
        item.setZValue(snap.z)
        item.setScale(snap.scale)
        item.setRotation(snap.rotation)
        if snap.flip != item.flip():
            item.do_flip()
        return item

    def __str__(self) -> str:
        txt = self._markdown[:40]
        return f'Text "{txt}"'

    def get_extra_save_data(self) -> dict[str, Any]:
        return {"text": self._markdown}

    def contains(self, point: QtCore.QPointF) -> bool:
        return self.boundingRect().contains(point)

    def paint(
        self,
        painter: QtGui.QPainter | None,
        option: QtWidgets.QStyleOptionGraphicsItem | None,
        widget: QtWidgets.QWidget | None = None,
    ) -> None:
        assert painter is not None
        painter.setPen(Qt.PenStyle.NoPen)
        color = QtGui.QColor(0, 0, 0)
        color.setAlpha(40)
        brush = QtGui.QBrush(color)
        painter.setBrush(brush)
        painter.drawRect(QtWidgets.QGraphicsTextItem.boundingRect(self))
        if option is not None:
            option.state = QtWidgets.QStyle.StateFlag.State_Enabled
        super().paint(painter, option, widget)
        self.paint_selectable(painter, option, widget)

    def create_copy(self) -> ZeeTextItem:
        item = ZeeTextItem(self._markdown)
        item.setPos(self.pos())
        item.setZValue(self.zValue())
        item.setScale(self.scale())
        item.setRotation(self.rotation())
        if self.flip() == -1:
            item.do_flip()
        return item

    def enter_edit_mode(self) -> None:
        logger.debug(f"Entering edit mode on {self}")
        self.edit_mode = True
        self.old_text = self._markdown
        self.setPlainText(self._markdown)
        self.setDefaultTextColor(QtGui.QColor(*COLORS["Scene:Text"]))
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self.require_scene().edit_item = self

    def exit_edit_mode(self, commit: bool = True) -> None:
        logger.debug(f"Exiting edit mode on {self}")
        self.edit_mode = False
        # reset selection:
        self.setTextCursor(QtGui.QTextCursor(self.document()))
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        scene = self.require_scene()
        scene.edit_item = None
        if commit:
            new_text = self.toPlainText()
            self._markdown = new_text
            self._render_markdown()
            scene.undo_stack.push(commands.ChangeText(self, new_text, self.old_text))
            if not new_text.strip():
                logger.debug("Removing empty text item")
                scene.undo_stack.push(commands.DeleteItems(scene, [self]))
        else:
            self._markdown = self.old_text
            self._render_markdown()

    def has_selection_handles(self) -> bool:
        return super().has_selection_handles() and not self.edit_mode

    def keyPressEvent(self, event: QtGui.QKeyEvent | None) -> None:
        assert event is not None
        if (
            event.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return)
            and event.modifiers() == Qt.KeyboardModifier.ShiftModifier
        ):
            self.exit_edit_mode()
            event.accept()
            return
        if (
            event.key() == Qt.Key.Key_Escape
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
        ):
            self.exit_edit_mode(commit=False)
            event.accept()
            return
        super().keyPressEvent(event)

    def copy_to_clipboard(self, clipboard: QtGui.QClipboard) -> None:
        clipboard.setText(self._markdown)


@register_item
class ZeeErrorItem(ZeeItemMixin, QtWidgets.QGraphicsTextItem):
    """Class for displaying error messages when an item can't be loaded
    from a zref file.

    This item will be displayed instead of the original item. It won't
    save to zref files. The original item will be preserved in the zref
    file, unless this item gets deleted by the user, or a new zref file
    is saved.
    """

    TYPE = "error"

    def __init__(self, text: str | None = None, **kwargs: Any) -> None:
        super().__init__(text or "Text")
        self.save_id: str = uuid.uuid4().hex
        self.created_at: float = time.time()
        logger.debug(f"Initialized {self}")
        self.is_image = False
        self.init_selectable()
        self.is_editable = False
        self.setDefaultTextColor(QtGui.QColor(*COLORS["Scene:Text"]))

    def snapshot(self) -> ErrorItemSnapshot:
        """Error items just preserve the original DB row."""
        return ErrorItemSnapshot(save_id=self.save_id)

    @classmethod
    def create_from_data(cls, **kwargs: Any) -> ZeeErrorItem:
        data: dict[str, Any] = kwargs.get("data", {})
        item = cls(**data)
        return item

    def __str__(self) -> str:
        txt = self.toPlainText()[:40]
        return f'Error "{txt}"'

    def contains(self, point: QtCore.QPointF) -> bool:
        return self.boundingRect().contains(point)

    def paint(
        self,
        painter: QtGui.QPainter | None,
        option: QtWidgets.QStyleOptionGraphicsItem | None,
        widget: QtWidgets.QWidget | None = None,
    ) -> None:
        assert painter is not None
        painter.setPen(Qt.PenStyle.NoPen)
        color = QtGui.QColor(200, 0, 0)
        brush = QtGui.QBrush(color)
        painter.setBrush(brush)
        painter.drawRect(QtWidgets.QGraphicsTextItem.boundingRect(self))
        if option is not None:
            option.state = QtWidgets.QStyle.StateFlag.State_Enabled
        super().paint(painter, option, widget)
        self.paint_selectable(painter, option, widget)

    def update_from_data(self, **kwargs: Any) -> None:
        self.save_id = kwargs.get("save_id", self.save_id)
        self.setPos(kwargs.get("x", self.pos().x()), kwargs.get("y", self.pos().y()))
        self.setZValue(kwargs.get("z", self.zValue()))
        self.setScale(kwargs.get("scale", self.scale()))
        self.setRotation(kwargs.get("rotation", self.rotation()))

    def create_copy(self) -> ZeeErrorItem:
        item = ZeeErrorItem(self.toPlainText())
        item.setPos(self.pos())
        item.setZValue(self.zValue())
        item.setScale(self.scale())
        item.setRotation(self.rotation())
        return item

    def flip(self, *args: Any, **kwargs: Any) -> float:
        """Returns the flip value (1 or -1)"""
        # Never display error messages flipped
        return 1

    def do_flip(self, *args: Any, **kwargs: Any) -> None:
        """Flips the item."""
        # Never flip error messages
        pass

    def copy_to_clipboard(self, clipboard: QtGui.QClipboard) -> None:
        clipboard.setText(self.toPlainText())
