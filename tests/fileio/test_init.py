import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from PyQt6 import QtCore, QtGui

from beeref import fileio
from beeref import commands
from beeref.fileio.snapshot import IOResult
from beeref.items import BeePixmapItem
from ..utils import queue2list


def test_save_bee_via_swp(view, imgfilename3x3):
    item = BeePixmapItem(QtGui.QImage(imgfilename3x3))
    view.scene.addItem(item)
    snapshots = view.scene.snapshot_for_save()
    swp_path = view.scene._scratch_file
    assert swp_path is not None
    with tempfile.TemporaryDirectory() as dirname:
        fname = Path(dirname) / "test.bee"
        fileio.save_bee(fname, snapshots, swp_path)
        assert fname.exists()


@patch("beeref.fileio.sql.SQLiteIO.read")
def test_read_bee(read_mock):
    with tempfile.TemporaryDirectory() as dirname:
        fname = Path(dirname) / "test.bee"
        fname.touch()
        fileio.load_bee(fname, MagicMock())
        read_mock.assert_called_once()


def test_load_images_loads(view, imgfilename3x3):
    view.scene.undo_stack = MagicMock()
    worker = MagicMock(canceled=False)
    fileio.load_images([imgfilename3x3], QtCore.QPointF(5, 6), view.scene, worker)
    worker.begin_processing.emit.assert_called_once_with(1)
    worker.progress.emit.assert_called_once_with(0)
    worker.finished.emit.assert_called_once_with(IOResult(filename=None, errors=[]))
    itemdata = queue2list(view.scene.items_to_add)
    assert len(itemdata) == 1
    item = itemdata[0][0]["item"]
    args = view.scene.undo_stack.push.call_args_list[0][0]
    cmd = args[0]
    assert isinstance(cmd, commands.InsertItems)
    assert cmd.items == [item]
    assert cmd.scene == view.scene
    assert cmd.ignore_first_redo is True
    assert item.pos() == QtCore.QPointF(3.5, 4.5)


def test_load_images_canceled(view, imgfilename3x3):
    view.scene.undo_stack = MagicMock()
    worker = MagicMock(canceled=True)
    fileio.load_images(
        [imgfilename3x3, imgfilename3x3], QtCore.QPointF(5, 6), view.scene, worker
    )
    worker.begin_processing.emit.assert_called_once_with(2)
    worker.progress.emit.assert_called_once_with(0)
    worker.finished.emit.assert_called_once_with(IOResult(filename=None, errors=[]))
    itemdata = queue2list(view.scene.items_to_add)
    assert len(itemdata) == 1
    item = itemdata[0][0]["item"]
    args = view.scene.undo_stack.push.call_args_list[0][0]
    cmd = args[0]
    assert isinstance(cmd, commands.InsertItems)
    assert cmd.items == [item]
    assert cmd.scene == view.scene
    assert cmd.ignore_first_redo is True
    assert item.pos() == QtCore.QPointF(3.5, 4.5)


def test_load_images_error(view, imgfilename3x3):
    view.scene.undo_stack = MagicMock()
    worker = MagicMock(canceled=False)
    fileio.load_images(
        ["foo.jpg", imgfilename3x3], QtCore.QPointF(5, 6), view.scene, worker
    )
    worker.begin_processing.emit.assert_called_once_with(2)
    worker.progress.emit.assert_any_call(0)
    worker.progress.emit.assert_any_call(1)
    worker.finished.emit.assert_called_once_with(
        IOResult(filename=None, errors=["foo.jpg"])
    )
    itemdata = queue2list(view.scene.items_to_add)
    assert len(itemdata) == 1
    item = itemdata[0][0]["item"]
    args = view.scene.undo_stack.push.call_args_list[0][0]
    cmd = args[0]
    assert isinstance(cmd, commands.InsertItems)
    assert cmd.items == [item]
    assert cmd.scene == view.scene
    assert cmd.ignore_first_redo is True
    assert item.pos() == QtCore.QPointF(3.5, 4.5)
