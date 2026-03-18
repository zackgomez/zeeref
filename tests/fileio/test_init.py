import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from PyQt6 import QtCore, QtGui

from zeeref import fileio
from zeeref import commands
from zeeref.types.snapshot import IOResult
from zeeref.items import ZeePixmapItem
from ..utils import queue2list


def test_save_zref_via_swp(scene, imgfilename3x3):
    from zeeref.fileio.scratch import create_scratch_file

    scene._scratch_file = create_scratch_file(None)
    item = ZeePixmapItem(QtGui.QImage(imgfilename3x3))
    scene.addItem(item)
    snapshots = scene.snapshot_for_save()
    swp_path = scene._scratch_file
    assert swp_path is not None
    with tempfile.TemporaryDirectory() as dirname:
        fname = Path(dirname) / "test.zref"
        fileio.save_zref(fname, snapshots, swp_path)
        assert fname.exists()


@patch("zeeref.fileio.sql.SQLiteIO.read")
def test_load_zref(read_mock):
    with tempfile.TemporaryDirectory() as dirname:
        fname = Path(dirname) / "test.zref"
        fname.touch()
        fileio.load_zref(fname, MagicMock())
        read_mock.assert_called_once()


def test_load_images_loads(scene, imgfilename3x3):
    scene.undo_stack = MagicMock()
    worker = MagicMock(canceled=False)
    fileio.load_images([imgfilename3x3], QtCore.QPointF(5, 6), scene, worker)
    worker.begin_processing.emit.assert_called_once_with(1)
    worker.progress.emit.assert_called_once_with(0)
    worker.finished.emit.assert_called_once_with(IOResult(filename=None, errors=[]))
    itemdata = queue2list(scene.items_to_add)
    assert len(itemdata) == 1
    item = itemdata[0][0]["item"]
    args = scene.undo_stack.push.call_args_list[0][0]
    cmd = args[0]
    assert isinstance(cmd, commands.InsertItems)
    assert cmd.items == [item]
    assert cmd.scene == scene
    assert cmd.ignore_first_redo is True
    assert item.pos() == QtCore.QPointF(3.5, 4.5)


def test_load_images_canceled(scene, imgfilename3x3):
    scene.undo_stack = MagicMock()
    worker = MagicMock(canceled=True)
    fileio.load_images(
        [imgfilename3x3, imgfilename3x3], QtCore.QPointF(5, 6), scene, worker
    )
    worker.begin_processing.emit.assert_called_once_with(2)
    worker.progress.emit.assert_called_once_with(0)
    worker.finished.emit.assert_called_once_with(IOResult(filename=None, errors=[]))
    itemdata = queue2list(scene.items_to_add)
    assert len(itemdata) == 1
    item = itemdata[0][0]["item"]
    args = scene.undo_stack.push.call_args_list[0][0]
    cmd = args[0]
    assert isinstance(cmd, commands.InsertItems)
    assert cmd.items == [item]
    assert cmd.scene == scene
    assert cmd.ignore_first_redo is True
    assert item.pos() == QtCore.QPointF(3.5, 4.5)


def test_load_images_error(scene, imgfilename3x3):
    scene.undo_stack = MagicMock()
    worker = MagicMock(canceled=False)
    fileio.load_images(["foo.jpg", imgfilename3x3], QtCore.QPointF(5, 6), scene, worker)
    worker.begin_processing.emit.assert_called_once_with(2)
    worker.progress.emit.assert_any_call(0)
    worker.progress.emit.assert_any_call(1)
    worker.finished.emit.assert_called_once_with(
        IOResult(filename=None, errors=["foo.jpg"])
    )
    itemdata = queue2list(scene.items_to_add)
    assert len(itemdata) == 1
    item = itemdata[0][0]["item"]
    args = scene.undo_stack.push.call_args_list[0][0]
    cmd = args[0]
    assert isinstance(cmd, commands.InsertItems)
    assert cmd.items == [item]
    assert cmd.scene == scene
    assert cmd.ignore_first_redo is True
    assert item.pos() == QtCore.QPointF(3.5, 4.5)
