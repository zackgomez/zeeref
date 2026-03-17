import os
import stat
from unittest.mock import MagicMock
import pytest

from PyQt6 import QtGui

from zeeref.items import ZeePixmapItem
from zeeref.scene import ZeeGraphicsScene
from zeeref.fileio.errors import ZeeFileIOError
from zeeref.fileio.export import ImagesToDirectoryExporter
from zeeref.types.snapshot import IOResult


@pytest.fixture
def readonly_dir(tmp_path):
    yield tmp_path
    tmp_path.chmod(stat.S_IRWXU)


def _export_filename(item):
    """Helper to get the expected export filename for an item."""
    return f"{item.save_id[:8]}.png"


def test_images_to_directory_exporter_export_writes_images(
    scene,
    tmp_path,
    imgdata3x3,
    imgfilename3x3,
):
    item1 = ZeePixmapItem(QtGui.QImage(imgfilename3x3))
    scene.addItem(item1)
    item2 = ZeePixmapItem(QtGui.QImage(imgfilename3x3))
    scene.addItem(item2)
    exporter = ImagesToDirectoryExporter(scene, tmp_path)
    exporter.export()

    with open(os.path.join(tmp_path, _export_filename(item1)), "rb") as f:
        assert f.read().startswith(b"\x89PNG")
    with open(os.path.join(tmp_path, _export_filename(item2)), "rb") as f:
        assert f.read().startswith(b"\x89PNG")


def test_images_to_directory_exporter_export_file_exists_no_user_input(
    scene,
    tmp_path,
    imgdata3x3,
    imgfilename3x3,
):
    # items_by_type returns items in reverse insertion order (descending stacking)
    # so exporter.items = [item2, item1]
    item1 = ZeePixmapItem(QtGui.QImage(imgfilename3x3))
    scene.addItem(item1)
    item2 = ZeePixmapItem(QtGui.QImage(imgfilename3x3))
    scene.addItem(item2)

    # Pre-create file matching item1 (at index 1 in exporter's list)
    with open(os.path.join(tmp_path, _export_filename(item1)), "w") as f:
        assert f.write("foo")

    exporter = ImagesToDirectoryExporter(scene, tmp_path)
    exporter.export()

    # item2 (index 0) was written successfully
    with open(os.path.join(tmp_path, _export_filename(item2)), "rb") as f:
        assert f.read().startswith(b"\x89PNG")
    # item1 (index 1) file still has original content
    with open(os.path.join(tmp_path, _export_filename(item1)), "r") as f:
        assert f.read() == "foo"

    assert exporter.start_from == 1


def test_images_to_directory_exporter_export_file_exists_skip(
    scene,
    tmp_path,
    imgdata3x3,
    imgfilename3x3,
):
    # exporter.items = [item3, item2, item1] (reverse insertion order)
    item1 = ZeePixmapItem(QtGui.QImage(imgfilename3x3))
    scene.addItem(item1)
    item2 = ZeePixmapItem(QtGui.QImage(imgfilename3x3))
    scene.addItem(item2)
    item3 = ZeePixmapItem(QtGui.QImage(imgfilename3x3))
    scene.addItem(item3)

    # Pre-create files for item2 (index 1) and item1 (index 2)
    with open(os.path.join(tmp_path, _export_filename(item2)), "w") as f:
        assert f.write("foo")
    with open(os.path.join(tmp_path, _export_filename(item1)), "w") as f:
        assert f.write("bar")

    exporter = ImagesToDirectoryExporter(scene, tmp_path)
    exporter.handle_existing = "skip"
    exporter.export()

    # item3 (index 0) was written
    with open(os.path.join(tmp_path, _export_filename(item3)), "rb") as f:
        assert f.read().startswith(b"\x89PNG")
    # item2 (index 1) skipped
    with open(os.path.join(tmp_path, _export_filename(item2)), "r") as f:
        assert f.read() == "foo"
    # item1 (index 2) file still has original
    with open(os.path.join(tmp_path, _export_filename(item1)), "r") as f:
        assert f.read() == "bar"

    # 'skip' handles one file then resets to None; stops at next existing
    assert exporter.start_from == 2
    assert exporter.handle_existing is None


def test_images_to_directory_exporter_export_file_exists_skip_all(
    scene,
    tmp_path,
    imgdata3x3,
    imgfilename3x3,
):
    # exporter.items = [item3, item2, item1]
    item1 = ZeePixmapItem(QtGui.QImage(imgfilename3x3))
    scene.addItem(item1)
    item2 = ZeePixmapItem(QtGui.QImage(imgfilename3x3))
    scene.addItem(item2)
    item3 = ZeePixmapItem(QtGui.QImage(imgfilename3x3))
    scene.addItem(item3)

    with open(os.path.join(tmp_path, _export_filename(item2)), "w") as f:
        assert f.write("foo")
    with open(os.path.join(tmp_path, _export_filename(item1)), "w") as f:
        assert f.write("bar")

    exporter = ImagesToDirectoryExporter(scene, tmp_path)
    exporter.handle_existing = "skip_all"
    exporter.export()

    with open(os.path.join(tmp_path, _export_filename(item3)), "rb") as f:
        assert f.read().startswith(b"\x89PNG")
    with open(os.path.join(tmp_path, _export_filename(item2)), "r") as f:
        assert f.read() == "foo"
    with open(os.path.join(tmp_path, _export_filename(item1)), "r") as f:
        assert f.read() == "bar"

    assert exporter.handle_existing == "skip_all"


def test_images_to_directory_exporter_export_file_exists_overwrite(
    scene,
    tmp_path,
    imgdata3x3,
    imgfilename3x3,
):
    # exporter.items = [item3, item2, item1]
    item1 = ZeePixmapItem(QtGui.QImage(imgfilename3x3))
    scene.addItem(item1)
    item2 = ZeePixmapItem(QtGui.QImage(imgfilename3x3))
    scene.addItem(item2)
    item3 = ZeePixmapItem(QtGui.QImage(imgfilename3x3))
    scene.addItem(item3)

    with open(os.path.join(tmp_path, _export_filename(item2)), "w") as f:
        assert f.write("foo")
    with open(os.path.join(tmp_path, _export_filename(item1)), "w") as f:
        assert f.write("bar")

    exporter = ImagesToDirectoryExporter(scene, tmp_path)
    exporter.handle_existing = "overwrite"
    exporter.export()

    # item3 (index 0) written
    with open(os.path.join(tmp_path, _export_filename(item3)), "rb") as f:
        assert f.read().startswith(b"\x89PNG")
    # item2 (index 1) overwritten
    with open(os.path.join(tmp_path, _export_filename(item2)), "rb") as f:
        assert f.read().startswith(b"\x89PNG")
    # item1 (index 2) still 'bar' (overwrite handles one, then resets)
    with open(os.path.join(tmp_path, _export_filename(item1)), "r") as f:
        assert f.read() == "bar"

    assert exporter.start_from == 2
    assert exporter.handle_existing is None


def test_images_to_directory_exporter_export_file_exists_overwrite_all(
    scene,
    tmp_path,
    imgdata3x3,
    imgfilename3x3,
):
    # exporter.items = [item3, item2, item1]
    item1 = ZeePixmapItem(QtGui.QImage(imgfilename3x3))
    scene.addItem(item1)
    item2 = ZeePixmapItem(QtGui.QImage(imgfilename3x3))
    scene.addItem(item2)
    item3 = ZeePixmapItem(QtGui.QImage(imgfilename3x3))
    scene.addItem(item3)

    with open(os.path.join(tmp_path, _export_filename(item2)), "w") as f:
        assert f.write("foo")
    with open(os.path.join(tmp_path, _export_filename(item1)), "w") as f:
        assert f.write("bar")

    exporter = ImagesToDirectoryExporter(scene, tmp_path)
    exporter.handle_existing = "overwrite_all"
    exporter.export()

    with open(os.path.join(tmp_path, _export_filename(item3)), "rb") as f:
        assert f.read().startswith(b"\x89PNG")
    with open(os.path.join(tmp_path, _export_filename(item2)), "rb") as f:
        assert f.read().startswith(b"\x89PNG")
    with open(os.path.join(tmp_path, _export_filename(item1)), "rb") as f:
        assert f.read().startswith(b"\x89PNG")

    assert exporter.handle_existing == "overwrite_all"


def test_images_to_directory_exporter_export_with_worker(
    scene,
    tmp_path,
    imgdata3x3,
    imgfilename3x3,
):
    item = ZeePixmapItem(QtGui.QImage(imgfilename3x3))
    scene.addItem(item)
    worker = MagicMock(canceled=False)
    exporter = ImagesToDirectoryExporter(scene, tmp_path)
    exporter.export(worker)

    with open(os.path.join(tmp_path, _export_filename(item)), "rb") as f:
        assert f.read().startswith(b"\x89PNG")

    worker.begin_processing.emit.assert_called_once_with(1)
    worker.progress.emit.assert_called_with(0)
    worker.finished.emit.assert_called_once_with(IOResult(filename=tmp_path, errors=[]))


def test_images_to_directory_exporter_export_with_worker_when_canceled(
    scene,
    tmp_path,
    imgdata3x3,
    imgfilename3x3,
):
    item = ZeePixmapItem(QtGui.QImage(imgfilename3x3))
    scene.addItem(item)
    worker = MagicMock(canceled=True)
    exporter = ImagesToDirectoryExporter(scene, tmp_path)
    exporter.export(worker)

    assert os.path.exists(os.path.join(tmp_path, _export_filename(item))) is False

    worker.begin_processing.emit.assert_called_once_with(1)
    worker.progress.emit.assert_called_once_with(0)
    worker.finished.emit.assert_called_once_with(IOResult(filename=tmp_path, errors=[]))


def test_images_to_directory_exporter_export_with_worker_when_file_exists(
    scene,
    tmp_path,
    imgdata3x3,
    imgfilename3x3,
):
    item = ZeePixmapItem(QtGui.QImage(imgfilename3x3))
    scene.addItem(item)

    imgfilename = os.path.join(tmp_path, _export_filename(item))
    with open(imgfilename, "w") as f:
        assert f.write("foo")

    worker = MagicMock(canceled=False)
    exporter = ImagesToDirectoryExporter(scene, tmp_path)
    exporter.export(worker)

    with open(imgfilename, "r") as f:
        assert f.read() == "foo"

    worker.begin_processing.emit.assert_called_once_with(1)
    worker.progress.emit.assert_called_with(0)
    worker.user_input_required.emit.assert_called_once_with(imgfilename)


def test_images_to_directory_exporter_export_when_dir_not_writeable(
    readonly_dir,
    imgfilename3x3,
):
    scene = ZeeGraphicsScene(QtGui.QUndoStack())
    item = ZeePixmapItem(QtGui.QImage(imgfilename3x3))
    scene.addItem(item)

    os.chmod(readonly_dir, stat.S_IREAD)
    exporter = ImagesToDirectoryExporter(scene, readonly_dir)

    with pytest.raises(ZeeFileIOError) as e:
        exporter.export()
        assert e.filename == readonly_dir


def test_images_to_directory_exporter_export_when_dir_not_writeable_w_worker(
    readonly_dir,
    imgfilename3x3,
):
    scene = ZeeGraphicsScene(QtGui.QUndoStack())
    item = ZeePixmapItem(QtGui.QImage(imgfilename3x3))
    scene.addItem(item)

    os.chmod(readonly_dir, stat.S_IREAD)
    exporter = ImagesToDirectoryExporter(scene, readonly_dir)
    worker = MagicMock(canceled=False)

    exporter.export(worker)
    worker.begin_processing.emit.assert_called_once_with(1)
    worker.finished.emit.assert_called_once()
    result = worker.finished.emit.call_args.args[0]
    assert isinstance(result, IOResult)
    assert result.filename == readonly_dir
    assert len(result.errors) == 1


def test_images_to_directory_exporter_export_when_img_not_writeable(
    scene,
    tmp_path,
    imgdata3x3,
    imgfilename3x3,
):

    item = ZeePixmapItem(QtGui.QImage(imgfilename3x3))
    scene.addItem(item)

    imgfilename = tmp_path / _export_filename(item)
    with open(imgfilename, "w") as f:
        assert f.write("foo")
    os.chmod(imgfilename, stat.S_IREAD)

    exporter = ImagesToDirectoryExporter(scene, tmp_path)
    exporter.handle_existing = "overwrite_all"

    with pytest.raises(ZeeFileIOError) as e:
        exporter.export()
        assert e.filename == readonly_dir


def test_images_to_directory_exporter_export_when_img_not_writeable_w_worker(
    scene,
    tmp_path,
    imgdata3x3,
    imgfilename3x3,
):

    item = ZeePixmapItem(QtGui.QImage(imgfilename3x3))
    scene.addItem(item)

    imgfilename = tmp_path / _export_filename(item)
    with open(imgfilename, "w") as f:
        assert f.write("foo")
    os.chmod(imgfilename, stat.S_IREAD)

    exporter = ImagesToDirectoryExporter(scene, tmp_path)
    exporter.handle_existing = "overwrite_all"
    worker = MagicMock(canceled=False)

    exporter.export(worker)
    worker.begin_processing.emit.assert_called_once_with(1)
    worker.finished.emit.assert_called_once()
    result = worker.finished.emit.call_args.args[0]
    assert isinstance(result, IOResult)
    assert result.filename == imgfilename
    assert len(result.errors) == 1
