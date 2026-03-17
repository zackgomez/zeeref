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

from __future__ import annotations

from functools import partial
import os
import os.path
from pathlib import Path
from collections.abc import Sequence
from typing import Any, Callable, cast

from beeref.logging import getLogger

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt

from beeref.actions import ActionsMixin
from beeref import commands
from beeref.config import CommandlineArgs, BeeSettings, KeyboardSettings
from beeref import constants
from beeref import fileio
from beeref.fileio.errors import IMG_LOADING_ERROR_MSG
from beeref.fileio.export import exporter_registry, ImagesToDirectoryExporter
from beeref import widgets
from beeref.items import BeePixmapItem, BeeTextItem, create_item_from_snapshot
from beeref.main_controls import MainControlsMixin
from beeref.scene import BeeGraphicsScene
from beeref.utils import get_file_extension_from_format, qcolor_to_hex


commandline_args = CommandlineArgs()
logger = getLogger(__name__)


class BeeGraphicsView(MainControlsMixin, QtWidgets.QGraphicsView, ActionsMixin):
    scene: BeeGraphicsScene
    parent: QtWidgets.QMainWindow

    PAN_MODE = 1
    ZOOM_MODE = 2
    SAMPLE_COLOR_MODE = 3

    def __init__(
        self, app: QtWidgets.QApplication, parent: QtWidgets.QMainWindow | None = None
    ) -> None:
        super().__init__(parent)
        self.app: QtWidgets.QApplication = app
        assert parent is not None
        self.parent: QtWidgets.QMainWindow = parent
        self.settings: BeeSettings = BeeSettings()
        self.keyboard_settings: KeyboardSettings = KeyboardSettings()
        self.welcome_overlay: widgets.welcome_overlay.WelcomeOverlay = (
            widgets.welcome_overlay.WelcomeOverlay(self)
        )

        canvas_color = self.settings.valueOrDefault("View/canvas_color")
        self.setBackgroundBrush(QtGui.QBrush(QtGui.QColor(canvas_color)))

        def on_canvas_color_changed(color: str) -> None:
            self.setBackgroundBrush(QtGui.QBrush(QtGui.QColor(color)))
            self.welcome_overlay.update_background_color()

        BeeSettings.FIELDS["View/canvas_color"]["post_save_callback"] = (
            on_canvas_color_changed
        )
        self.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.undo_stack: QtGui.QUndoStack = QtGui.QUndoStack(self)
        self.undo_stack.setUndoLimit(100)
        self.undo_stack.canRedoChanged.connect(self.on_can_redo_changed)
        self.undo_stack.canUndoChanged.connect(self.on_can_undo_changed)
        self.undo_stack.cleanChanged.connect(self.on_undo_clean_changed)
        self.undo_stack.indexChanged.connect(self.on_undo_index_changed)

        self.filename = None
        self.worker: fileio.ThreadedIO | None = None
        self._drain_dirty: bool = False
        self.previous_transform: dict[str, Any] | None = None
        self.active_mode: int | None = None
        self.event_start: QtCore.QPointF = QtCore.QPointF()
        self.event_anchor: QtCore.QPointF = QtCore.QPointF()
        self.event_inverted: bool = False
        self.progress: widgets.BeeProgressDialog | None = None
        self.exporter: ImagesToDirectoryExporter | None = None

        self.scene: BeeGraphicsScene = BeeGraphicsScene(self.undo_stack)
        self.scene.changed.connect(self.on_scene_changed)
        self.scene.selectionChanged.connect(self.on_selection_changed)
        self.scene.cursor_changed.connect(self.on_cursor_changed)
        self.scene.cursor_cleared.connect(self.on_cursor_cleared)
        self.setScene(self.scene)

        # Context menu and actions
        self.build_menu_and_actions()
        self.control_target: BeeGraphicsView = self
        self.init_main_controls(main_window=parent)

        # Create empty .swp for untitled scene
        self.scene._scratch_file = fileio.create_scratch_file(None)

        # Drain timer — periodically write scene state to .swp
        self.drain_timer: QtCore.QTimer = QtCore.QTimer(self)
        self.drain_timer.setInterval(60_000)
        self.drain_timer.timeout.connect(self.drain_tick)
        self.drain_timer.start()

        # Load files given via command line
        if commandline_args.filenames:
            fn = Path(commandline_args.filenames[0])
            if fn.suffix == ".bee":
                self.open_from_file(fn)
            else:
                self.do_insert_images(commandline_args.filenames)

        self.update_window_title()

    @property
    def filename(self) -> Path | None:
        return self._filename

    @filename.setter
    def filename(self, value: Path | None) -> None:
        self._filename = value
        self.update_window_title()
        if value:
            self.settings.update_recent_files(str(value))
            self.update_menu_and_actions()

    def require_viewport(self) -> QtWidgets.QWidget:
        vp = self.viewport()
        assert vp is not None
        return vp

    def cancel_active_modes(self) -> None:
        self.scene.cancel_active_modes()
        self.cancel_sample_color_mode()
        self.active_mode = None

    def cancel_sample_color_mode(self) -> None:
        logger.debug("Cancel sample color mode")
        self.active_mode = None
        self.require_viewport().unsetCursor()
        if hasattr(self, "sample_color_widget"):
            self.sample_color_widget.hide()
            del self.sample_color_widget
        if self.scene.has_multi_selection():
            self.scene.multi_select_item.bring_to_front()

    def update_window_title(self) -> None:
        clean = self.undo_stack.isClean()
        if clean and not self.filename:
            title = constants.APPNAME
        else:
            name = self.filename.name if self.filename else "[Untitled]"
            marker = "" if clean else "*"
            title = f"{name}{marker} - {constants.APPNAME}"
        self.parent.setWindowTitle(title)

    def on_scene_changed(self, region: list[QtCore.QRectF]) -> None:
        if not self.scene.items():
            logger.debug("No items in scene")
            self.setTransform(QtGui.QTransform())
            self.welcome_overlay.setFocus()
            self.clearFocus()
            self.welcome_overlay.show()
            self.actiongroup_set_enabled("active_when_items_in_scene", False)
        else:
            self.setFocus()
            self.welcome_overlay.clearFocus()
            self.welcome_overlay.hide()
            self.actiongroup_set_enabled("active_when_items_in_scene", True)
        self.recalc_scene_rect()

    def on_can_redo_changed(self, can_redo: bool) -> None:
        self.actiongroup_set_enabled("active_when_can_redo", can_redo)

    def on_can_undo_changed(self, can_undo: bool) -> None:
        self.actiongroup_set_enabled("active_when_can_undo", can_undo)

    def on_undo_clean_changed(self, clean: bool) -> None:
        self.update_window_title()

    def on_undo_index_changed(self, index: int) -> None:
        self._drain_dirty = True

    def drain_tick(self) -> None:
        """Periodic drain: write scene state to the .swp file."""
        if not self._drain_dirty:
            return
        if not self.scene._scratch_file:
            return
        if self.worker is not None and self.worker.isRunning():
            return
        self._drain_dirty = False
        snapshots = self.scene.snapshot_for_save()
        self.worker = fileio.ThreadedIO(
            fileio.drain_bee,
            self.scene._scratch_file,
            snapshots,
        )
        self.worker.finished.connect(self.on_drain_finished)
        self.worker.start()

    def on_drain_finished(self, result: fileio.IOResult) -> None:
        """Handle drain completion — mark newly-saved blobs."""
        if result.errors:
            logger.warning("Drain failed: %s", result.errors)
            return
        assert isinstance(result, fileio.SaveResult)
        for item in self.scene.user_items():
            if isinstance(item, BeePixmapItem) and item.save_id in result.newly_saved:
                item._blob_saved = True

    def on_context_menu(self, point: QtCore.QPoint) -> None:
        global_point = self.mapToGlobal(point)
        exec_menu = cast(Any, self.context_menu.exec)
        exec_menu(global_point)

    def get_supported_image_formats(self, cls: type[Any]) -> str:
        formats: list[str] = []

        for f in cls.supportedImageFormats():
            string = f"*.{f.data().decode()}"
            formats.extend((string, string.upper()))
        return " ".join(formats)

    def get_view_center(self) -> QtCore.QPoint:
        return QtCore.QPoint(
            round(self.size().width() / 2), round(self.size().height() / 2)
        )

    def clear_scene(self) -> None:
        logger.debug("Clearing scene...")
        self.cancel_active_modes()
        self._drain_dirty = False
        if self.scene._scratch_file:
            fileio.delete_scratch_file(self.scene._scratch_file)
            self.scene._scratch_file = None
        self.scene.clear()
        self.undo_stack.clear()
        self.filename = None
        self.setTransform(QtGui.QTransform())

    def reset_previous_transform(self, toggle_item: Any = None) -> None:
        if (
            self.previous_transform
            and self.previous_transform["toggle_item"] != toggle_item
        ):
            self.previous_transform = None

    def fit_rect(self, rect: QtCore.QRectF, toggle_item: Any = None) -> None:
        if toggle_item and self.previous_transform:
            logger.debug("Fit view: Reset to previous")
            self.setTransform(self.previous_transform["transform"])
            self.centerOn(self.previous_transform["center"])
            self.previous_transform = None
            return
        if toggle_item:
            self.previous_transform = {
                "toggle_item": toggle_item,
                "transform": QtGui.QTransform(self.transform()),
                "center": self.mapToScene(self.get_view_center()),
            }
        else:
            self.previous_transform = None

        logger.debug(f"Fit view: {rect}")
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        self.recalc_scene_rect()
        # It seems to be more reliable when we fit a second time
        # Sometimes a changing scene rect can mess up the fitting
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        logger.trace("Fit view done")

    def get_confirmation_unsaved_changes(self, msg: str) -> bool:
        confirm = self.settings.valueOrDefault("Save/confirm_close_unsaved")
        if confirm and not self.undo_stack.isClean():
            answer = QtWidgets.QMessageBox.question(
                self,
                "Discard unsaved changes?",
                msg,
                QtWidgets.QMessageBox.StandardButton.Yes
                | QtWidgets.QMessageBox.StandardButton.Cancel,
            )
            return answer == QtWidgets.QMessageBox.StandardButton.Yes

        return True

    def on_action_new_scene(self) -> None:
        confirm = self.get_confirmation_unsaved_changes(
            "There are unsaved changes. Are you sure you want to open a new scene?"
        )
        if confirm:
            self.clear_scene()
            self.scene._scratch_file = fileio.create_scratch_file(None)

    def on_action_fit_scene(self) -> None:
        self.fit_rect(self.scene.itemsBoundingRect())

    def on_action_fit_selection(self) -> None:
        self.fit_rect(self.scene.itemsBoundingRect(selection_only=True))

    def on_action_fullscreen(self, checked: bool) -> None:
        if checked:
            self.parent.showFullScreen()
        else:
            self.parent.showNormal()

    def on_action_always_on_top(self, checked: bool) -> None:
        self.parent.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on=checked)
        self.parent.destroy()
        self.parent.create()
        self.parent.show()

    def on_action_show_titlebar(self, checked: bool) -> None:
        self.parent.setWindowFlag(Qt.WindowType.FramelessWindowHint, on=not checked)
        self.parent.destroy()
        self.parent.create()
        self.parent.show()

    def on_action_move_window(self) -> None:
        self.on_action_movewin_mode()

    def on_action_undo(self) -> None:
        logger.debug("Undo: %s" % self.undo_stack.undoText())
        self.cancel_active_modes()
        self.undo_stack.undo()

    def on_action_redo(self) -> None:
        logger.debug("Redo: %s" % self.undo_stack.redoText())
        self.cancel_active_modes()
        self.undo_stack.redo()

    def on_action_select_all(self) -> None:
        self.scene.select_all_items()

    def on_action_deselect_all(self) -> None:
        self.scene.deselect_all_items()

    def on_action_delete_items(self) -> None:
        logger.debug("Deleting items...")
        self.cancel_active_modes()
        self.undo_stack.push(
            commands.DeleteItems(self.scene, self.scene.selectedItems(user_only=True))
        )

    def on_action_cut(self) -> None:
        logger.debug("Cutting items...")
        self.on_action_copy()
        self.undo_stack.push(
            commands.DeleteItems(self.scene, self.scene.selectedItems(user_only=True))
        )

    def on_action_raise_to_top(self) -> None:
        self.scene.raise_to_top()

    def on_action_lower_to_bottom(self) -> None:
        self.scene.lower_to_bottom()

    def on_action_normalize_height(self) -> None:
        self.scene.normalize_height()

    def on_action_normalize_width(self) -> None:
        self.scene.normalize_width()

    def on_action_normalize_size(self) -> None:
        self.scene.normalize_size()

    def on_action_arrange_horizontal(self) -> None:
        self.scene.arrange()

    def on_action_arrange_vertical(self) -> None:
        self.scene.arrange(vertical=True)

    def on_action_arrange_optimal(self) -> None:
        self.scene.arrange_optimal()

    def on_action_arrange_square(self) -> None:
        self.scene.arrange_square()

    def on_action_change_opacity(self) -> None:
        images = list(
            filter(lambda item: item.is_image, self.scene.selectedItems(user_only=True))
        )
        widgets.ChangeOpacityDialog(self, images, self.undo_stack)

    def on_action_crop(self) -> None:
        self.scene.crop_items()

    def on_action_flip_horizontally(self) -> None:
        self.scene.flip_items(vertical=False)

    def on_action_flip_vertically(self) -> None:
        self.scene.flip_items(vertical=True)

    def on_action_reset_scale(self) -> None:
        self.cancel_active_modes()
        self.undo_stack.push(
            commands.ResetScale(self.scene.selectedItems(user_only=True))
        )

    def on_action_reset_rotation(self) -> None:
        self.cancel_active_modes()
        self.undo_stack.push(
            commands.ResetRotation(self.scene.selectedItems(user_only=True))
        )

    def on_action_reset_flip(self) -> None:
        self.cancel_active_modes()
        self.undo_stack.push(
            commands.ResetFlip(self.scene.selectedItems(user_only=True))
        )

    def on_action_reset_crop(self) -> None:
        self.cancel_active_modes()
        self.undo_stack.push(
            commands.ResetCrop(self.scene.selectedItems(user_only=True))
        )

    def on_action_reset_transforms(self) -> None:
        self.cancel_active_modes()
        self.undo_stack.push(
            commands.ResetTransforms(self.scene.selectedItems(user_only=True))
        )

    def on_action_show_color_gamut(self) -> None:
        widgets.color_gamut.GamutDialog(self, self.scene.selectedItems()[0])

    def on_action_sample_color(self) -> None:
        self.cancel_active_modes()
        logger.debug("Entering sample color mode")
        self.require_viewport().setCursor(Qt.CursorShape.CrossCursor)
        self.active_mode = self.SAMPLE_COLOR_MODE

        if self.scene.has_multi_selection():
            # We don't want to sample the multi select item, so
            # temporarily send it to the back:
            self.scene.multi_select_item.lower_behind_selection()

        pos = self.mapFromGlobal(self.cursor().pos())
        self.sample_color_widget = widgets.SampleColorWidget(
            self, pos, self.scene.sample_color_at(self.mapToScene(pos))
        )

    def on_items_loaded(self, value: int) -> None:
        logger.debug("On items loaded: add queued items")
        self.scene.add_queued_items()

    def on_loading_finished(self, result: fileio.IOResult) -> None:
        if result.errors:
            QtWidgets.QMessageBox.warning(
                self,
                "Problem loading file",
                (
                    "<p>Problem loading file %s</p>"
                    "<p>Not accessible or not a proper bee file</p>"
                )
                % result.filename,
            )
            return
        assert isinstance(result, fileio.LoadResult)
        self.filename = result.filename
        for snap in result.snapshots:
            item = create_item_from_snapshot(snap)
            self.scene.addItem(item)
        self.on_action_fit_scene()

    def on_action_open_recent_file(self, filename: str) -> None:
        confirm = self.get_confirmation_unsaved_changes(
            "There are unsaved changes. Are you sure you want to open a new scene?"
        )
        if confirm:
            self.open_from_file(Path(filename))

    def open_from_file(self, filename: Path) -> None:
        logger.info(f"Opening file {filename}")
        self.clear_scene()
        self.worker = fileio.ThreadedIO(fileio.load_bee, filename, self.scene)
        self.worker.finished.connect(self.on_loading_finished)
        self.progress = widgets.BeeProgressDialog(
            f"Loading {filename}", worker=self.worker, parent=self
        )
        self.worker.start()

    def on_action_open(self) -> None:
        confirm = self.get_confirmation_unsaved_changes(
            "There are unsaved changes. Are you sure you want to open a new scene?"
        )
        if not confirm:
            return

        self.cancel_active_modes()
        filename, f = QtWidgets.QFileDialog.getOpenFileName(
            parent=self, caption="Open file", filter=f"{constants.APPNAME} File (*.bee)"
        )
        if filename:
            path = Path(filename).resolve()
            self.open_from_file(path)
            self.filename = path

    def on_saving_finished(self, result: fileio.IOResult) -> None:
        if result.errors:
            QtWidgets.QMessageBox.warning(
                self,
                "Problem saving file",
                ("<p>Problem saving file %s</p><p>File/directory not accessible</p>")
                % result.filename,
            )
            return
        assert isinstance(result, fileio.SaveResult)
        assert result.filename is not None
        old_filename = self.filename
        self.filename = result.filename
        self.undo_stack.setClean()
        # Mark newly-saved items so their blobs aren't re-encoded on next drain
        for item in self.scene.user_items():
            if isinstance(item, BeePixmapItem) and item.save_id in result.newly_saved:
                item._blob_saved = True
        # Rename .swp if target changed (Save-As or first save of untitled)
        if old_filename != result.filename and self.scene._scratch_file:
            new_swp = fileio.derive_swp_path(result.filename)
            if self.scene._scratch_file != new_swp:
                os.rename(self.scene._scratch_file, new_swp)
                self.scene._scratch_file = new_swp

    def do_save(self, filename: Path) -> None:
        if not fileio.is_bee_file(filename):
            filename = filename.with_suffix(".bee")
        assert self.scene._scratch_file is not None
        # Snapshot scene state on the main thread before handing to
        # the background thread — no Qt objects cross the boundary
        snapshots = self.scene.snapshot_for_save()
        self.worker = fileio.ThreadedIO(
            fileio.save_bee,
            filename,
            snapshots,
            self.scene._scratch_file,
        )
        self.worker.finished.connect(self.on_saving_finished)
        self.progress = widgets.BeeProgressDialog(
            f"Saving {filename}", worker=self.worker, parent=self
        )
        self.worker.start()

    def on_action_save_as(self) -> None:
        self.cancel_active_modes()
        directory = str(self.filename.parent) if self.filename else None
        filename, _ = QtWidgets.QFileDialog.getSaveFileName(
            parent=self,
            caption="Save file",
            directory=directory,
            filter=f"{constants.APPNAME} File (*.bee)",
        )
        if filename:
            self.do_save(Path(filename))

    def on_action_save(self) -> None:
        self.cancel_active_modes()
        if not self.filename:
            self.on_action_save_as()
        else:
            self.do_save(self.filename)

    def on_action_export_scene(self) -> None:
        directory = str(self.filename.parent) if self.filename else None
        filename, formatstr = QtWidgets.QFileDialog.getSaveFileName(
            parent=self,
            caption="Export Scene to Image",
            directory=directory,
            filter=";;".join(
                (
                    "Image Files (*.png *.jpg *.jpeg *.svg)",
                    "PNG (*.png)",
                    "JPEG (*.jpg *.jpeg)",
                    "SVG (*.svg)",
                )
            ),
        )

        if not filename:
            return

        path = Path(filename)
        if not path.suffix:
            ext = get_file_extension_from_format(formatstr)
            path = path.with_suffix(f".{ext}")
        logger.debug(f"Got export filename {path}")

        exporter_cls = exporter_registry[path.suffix]
        exporter = exporter_cls(self.scene)
        if not exporter.get_user_input(self):
            return

        self.worker = fileio.ThreadedIO(exporter.export, path)
        self.worker.finished.connect(self.on_export_finished)
        self.progress = widgets.BeeProgressDialog(
            f"Exporting {filename}", worker=self.worker, parent=self
        )
        self.worker.start()

    def on_export_finished(self, result: fileio.IOResult) -> None:
        if result.errors:
            err_msg = "</br>".join(result.errors)
            QtWidgets.QMessageBox.warning(
                self,
                "Problem writing file",
                f"<p>Problem writing file {result.filename}</p><p>{err_msg}</p>",
            )

    def on_action_export_images(self) -> None:
        directory = str(self.filename.parent) if self.filename else None
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            parent=self, caption="Export Images", directory=directory
        )

        if not directory:
            return

        logger.debug(f"Got export directory {directory}")
        self.exporter = ImagesToDirectoryExporter(self.scene, Path(directory))
        self.worker = fileio.ThreadedIO(self.exporter.export)
        self.worker.user_input_required.connect(self.on_export_images_file_exists)
        self.worker.finished.connect(self.on_export_finished)
        self.progress = widgets.BeeProgressDialog(
            f"Exporting to {directory}", worker=self.worker, parent=self
        )
        self.worker.start()

    def on_export_images_file_exists(self, filename: str) -> None:
        dlg = widgets.ExportImagesFileExistsDialog(self, filename)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            assert self.exporter is not None
            self.exporter.handle_existing = dlg.get_answer()
            directory = self.exporter.dirname
            assert self.worker is not None
            self.progress = widgets.BeeProgressDialog(
                f"Exporting to {directory}", worker=self.worker, parent=self
            )
            self.worker.start()

    def on_action_quit(self) -> None:
        confirm = self.get_confirmation_unsaved_changes(
            "There are unsaved changes. Are you sure you want to quit?"
        )
        if confirm:
            logger.info("User quit. Exiting...")
            self.app.quit()

    def on_action_settings(self) -> None:
        widgets.settings.SettingsDialog(self)

    def on_action_help(self) -> None:
        widgets.HelpDialog(self)

    def on_action_about(self) -> None:
        QtWidgets.QMessageBox.about(
            self,
            f"About {constants.APPNAME}",
            (
                f"<h2>{constants.APPNAME} {constants.VERSION}</h2>"
                f"<p>{constants.APPNAME_FULL}</p>"
                f"<p>{constants.COPYRIGHT}</p>"
                f'<p><a href="{constants.WEBSITE}">'
                f"Visit the {constants.APPNAME} website</a></p>"
            ),
        )

    def on_action_debuglog(self) -> None:
        widgets.DebugLogDialog(self)

    def on_insert_images_finished(
        self, new_scene: bool, result: fileio.IOResult
    ) -> None:
        """Callback for when loading of images is finished.

        :param new_scene: True if the scene was empty before, else False
        :param result: IOResult with errors list of filenames that couldn't be loaded
        """

        logger.debug("Insert images finished")
        if result.errors:
            errornames = [f"<li>{fn}</li>" for fn in result.errors]
            errornames = "<ul>%s</ul>" % "\n".join(errornames)
            num = len(result.errors)
            msg = f"{num} image(s) could not be opened.<br/>"
            QtWidgets.QMessageBox.warning(
                self, "Problem loading images", msg + IMG_LOADING_ERROR_MSG + errornames
            )
        self.scene.add_queued_items()
        self.scene.arrange_default()
        self.undo_stack.endMacro()
        if new_scene:
            self.on_action_fit_scene()

    def do_insert_images(
        self,
        filenames: Sequence[str | QtCore.QUrl],
        pos: QtCore.QPoint | None = None,
    ) -> None:
        if pos is None:
            pos = self.get_view_center()
        self.scene.deselect_all_items()
        self.undo_stack.beginMacro("Insert Images")
        self.worker = fileio.ThreadedIO(
            fileio.load_images, filenames, self.mapToScene(pos), self.scene
        )
        self.worker.progress.connect(self.on_items_loaded)
        self.worker.finished.connect(
            partial(self.on_insert_images_finished, not self.scene.items())
        )
        self.progress = widgets.BeeProgressDialog(
            "Loading images", worker=self.worker, parent=self
        )
        self.worker.start()

    def on_action_insert_images(self) -> None:
        self.cancel_active_modes()
        formats = self.get_supported_image_formats(QtGui.QImageReader)
        logger.debug(f"Supported image types for reading: {formats}")
        filenames, f = QtWidgets.QFileDialog.getOpenFileNames(
            parent=self,
            caption="Select one or more images to open",
            filter=f"Images ({formats})",
        )
        self.do_insert_images(filenames)

    def on_action_insert_text(self) -> None:
        self.cancel_active_modes()
        item = BeeTextItem()
        pos = self.mapToScene(self.mapFromGlobal(self.cursor().pos()))
        item.setScale(1 / self.get_scale())
        self.undo_stack.push(commands.InsertItems(self.scene, [item], pos))
        item.setSelected(True)
        item.enter_edit_mode()
        item.setFocus()
        cursor = item.textCursor()
        cursor.select(QtGui.QTextCursor.SelectionType.Document)
        item.setTextCursor(cursor)

    def on_action_copy(self) -> None:
        logger.debug("Copying to clipboard...")
        self.cancel_active_modes()
        clipboard = QtWidgets.QApplication.clipboard()
        assert clipboard is not None
        items = self.scene.selectedItems(user_only=True)

        # At the moment, we can only copy one image to the global
        # clipboard. (Later, we might create an image of the whole
        # selection for external copying.)
        items[0].copy_to_clipboard(clipboard)

        # However, we can copy all items to the internal clipboard:
        self.scene.copy_selection_to_internal_clipboard()

        # We set a marker for ourselves in the global clipboard so
        # that we know to look up the internal clipboard when pasting:
        mime = clipboard.mimeData()
        assert mime is not None
        mime.setData("beeref/items", QtCore.QByteArray.number(len(items)))

    def on_action_paste(self) -> None:
        self.cancel_active_modes()
        logger.debug("Pasting from clipboard...")
        clipboard = QtWidgets.QApplication.clipboard()
        assert clipboard is not None
        pos = self.mapToScene(self.mapFromGlobal(self.cursor().pos()))

        # See if we need to look up the internal clipboard:
        mime = clipboard.mimeData()
        assert mime is not None
        data = mime.data("beeref/items")
        logger.debug(f"Custom data in clipboard: {data}")
        if data and self.scene.internal_clipboard:
            # Checking that internal clipboard exists since the user
            # may have opened a new scene since copying.
            self.scene.paste_from_internal_clipboard(pos)
            return

        img = clipboard.image()
        if not img.isNull():
            item = BeePixmapItem(img)
            self.undo_stack.push(commands.InsertItems(self.scene, [item], pos))
            if len(self.scene.items()) == 1:
                # This is the first image in the scene
                self.on_action_fit_scene()
            return
        text = clipboard.text()
        if text:
            item = BeeTextItem(text)
            item.setScale(1 / self.get_scale())
            self.undo_stack.push(commands.InsertItems(self.scene, [item], pos))
            return

        msg = "No image data or text in clipboard or image too big"
        logger.info(msg)
        widgets.BeeNotification(self, msg)

    def on_selection_changed(self) -> None:
        logger.debug(
            "Currently selected items: %s",
            len(self.scene.selectedItems(user_only=True)),
        )
        self.actiongroup_set_enabled(
            "active_when_selection", self.scene.has_selection()
        )
        self.actiongroup_set_enabled(
            "active_when_single_image", self.scene.has_single_image_selection()
        )

        self.require_viewport().repaint()

    def on_cursor_changed(self, cursor: QtGui.QCursor) -> None:
        if self.active_mode is None:
            self.require_viewport().setCursor(cursor)

    def on_cursor_cleared(self) -> None:
        if self.active_mode is None:
            self.require_viewport().unsetCursor()

    def recalc_scene_rect(self) -> None:
        """Resize the scene rectangle so that it is always one view width
        wider than all items' bounding box at each side and one view
        width higher on top and bottom. This gives the impression of
        an infinite canvas."""

        if self.previous_transform:
            return
        logger.trace("Recalculating scene rectangle...")
        try:
            topleft = self.mapFromScene(self.scene.itemsBoundingRect().topLeft())
            topleft = self.mapToScene(
                QtCore.QPoint(
                    topleft.x() - self.size().width(),
                    topleft.y() - self.size().height(),
                )
            )
            bottomright = self.mapFromScene(
                self.scene.itemsBoundingRect().bottomRight()
            )
            bottomright = self.mapToScene(
                QtCore.QPoint(
                    bottomright.x() + self.size().width(),
                    bottomright.y() + self.size().height(),
                )
            )
            self.setSceneRect(QtCore.QRectF(topleft, bottomright))
        except OverflowError:
            logger.info("Maximum scene size reached")
        logger.trace("Done recalculating scene rectangle")

    def get_zoom_size(self, func: Callable[[float, float], float]) -> float:
        """Calculates the size of all items' bounding box in the view's
        coordinates.

        This helps ensure that we never zoom out too much (scene
        becomes so tiny that items become invisible) or zoom in too
        much (causing overflow errors).

        :param func: Function which takes the width and height as
            arguments and turns it into a number, for ex. ``min`` or ``max``.
        """

        topleft = self.mapFromScene(self.scene.itemsBoundingRect().topLeft())
        bottomright = self.mapFromScene(self.scene.itemsBoundingRect().bottomRight())
        return func(bottomright.x() - topleft.x(), bottomright.y() - topleft.y())

    def scale(self, *args: Any, **kwargs: Any) -> None:
        super().scale(*args, **kwargs)
        self.scene.on_view_scale_change()
        self.recalc_scene_rect()

    def get_scale(self) -> float:
        return self.transform().m11()

    def pan(self, delta: QtCore.QPointF | QtCore.QPoint) -> None:
        if not self.scene.items():
            logger.debug("No items in scene; ignore pan")
            return

        hscroll = self.horizontalScrollBar()
        assert hscroll is not None
        hscroll.setValue(int(hscroll.value() + delta.x()))
        vscroll = self.verticalScrollBar()
        assert vscroll is not None
        vscroll.setValue(int(vscroll.value() + delta.y()))

    def zoom(self, delta: float, anchor: QtCore.QPointF) -> None:
        if not self.scene.items():
            logger.debug("No items in scene; ignore zoom")
            return

        # We calculate where the anchor is before and after the zoom
        # and then move the view accordingly to keep the anchor fixed
        # We can't use QGraphicsView's AnchorUnderMouse since it
        # uses the current cursor position while we need the initial mouse
        # press position for zooming with Ctrl + Middle Drag
        anchor_point = QtCore.QPoint(round(anchor.x()), round(anchor.y()))
        ref_point = self.mapToScene(anchor_point)
        if delta == 0:
            return
        factor = 1 + abs(delta / 1000)
        if delta > 0:
            if self.get_zoom_size(max) < 10000000:
                self.scale(factor, factor)
            else:
                logger.debug("Maximum zoom size reached")
                return
        else:
            if self.get_zoom_size(min) > 50:
                self.scale(1 / factor, 1 / factor)
            else:
                logger.debug("Minimum zoom size reached")
                return

        self.pan(self.mapFromScene(ref_point) - anchor_point)
        self.reset_previous_transform()

    def wheelEvent(self, event: QtGui.QWheelEvent | None) -> None:
        assert event is not None
        action, inverted = self.keyboard_settings.mousewheel_action_for_event(event)

        delta = event.angleDelta().y()
        if inverted:
            delta = delta * -1

        if action == "zoom":
            self.zoom(delta, event.position())
            event.accept()
            return
        if action == "pan_horizontal":
            self.pan(QtCore.QPointF(0, 0.5 * delta))
            event.accept()
            return
        if action == "pan_vertical":
            self.pan(QtCore.QPointF(0.5 * delta, 0))
            event.accept()
            return

    def mousePressEvent(self, event: QtGui.QMouseEvent | None) -> None:
        assert event is not None
        if self.mousePressEventMainControls(event):
            return

        if self.active_mode == self.SAMPLE_COLOR_MODE:
            if event.button() == Qt.MouseButton.LeftButton:
                color = self.scene.sample_color_at(self.mapToScene(event.pos()))
                if color:
                    name = qcolor_to_hex(color)
                    clipboard = QtWidgets.QApplication.clipboard()
                    assert clipboard is not None
                    clipboard.setText(name)
                    self.scene.internal_clipboard = []
                    msg = f"Copied color to clipboard: {name}"
                    logger.debug(msg)
                    widgets.BeeNotification(self, msg)
                else:
                    logger.debug("No color found")
            self.cancel_sample_color_mode()
            event.accept()
            return

        action, inverted = self.keyboard_settings.mouse_action_for_event(event)

        if action == "zoom":
            self.active_mode = self.ZOOM_MODE
            self.event_start = event.position()
            self.event_anchor = event.position()
            self.event_inverted = inverted
            event.accept()
            return

        if action == "pan":
            logger.trace("Begin pan")
            self.active_mode = self.PAN_MODE
            self.event_start = event.position()
            self.require_viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            # ClosedHandCursor and OpenHandCursor don't work, but I
            # don't know if that's only on my system or a general
            # problem. It works with other cursors.
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent | None) -> None:
        assert event is not None
        if self.active_mode == self.PAN_MODE:
            self.reset_previous_transform()
            pos = event.position()
            self.pan(self.event_start - pos)
            self.event_start = pos
            event.accept()
            return

        if self.active_mode == self.ZOOM_MODE:
            self.reset_previous_transform()
            pos = event.position()
            delta = (self.event_start - pos).y()
            if self.event_inverted:
                delta *= -1
            self.event_start = pos
            self.zoom(delta * 20, self.event_anchor)
            event.accept()
            return

        if self.active_mode == self.SAMPLE_COLOR_MODE:
            self.sample_color_widget.update_sample(
                event.position(),
                self.scene.sample_color_at(self.mapToScene(event.pos())),
            )
            event.accept()
            return

        if self.mouseMoveEventMainControls(event):
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent | None) -> None:
        assert event is not None
        if self.active_mode == self.PAN_MODE:
            logger.trace("End pan")
            self.require_viewport().unsetCursor()
            self.active_mode = None
            event.accept()
            return
        if self.active_mode == self.ZOOM_MODE:
            self.active_mode = None
            event.accept()
            return
        if self.mouseReleaseEventMainControls(event):
            return
        super().mouseReleaseEvent(event)

    def resizeEvent(self, event: QtGui.QResizeEvent | None) -> None:
        super().resizeEvent(event)
        self.recalc_scene_rect()
        self.welcome_overlay.resize(self.size())

    def keyPressEvent(self, event: QtGui.QKeyEvent | None) -> None:
        assert event is not None
        if self.keyPressEventMainControls(event):
            return
        if self.active_mode == self.SAMPLE_COLOR_MODE:
            self.cancel_sample_color_mode()
            event.accept()
            return
        super().keyPressEvent(event)
