import os
import stat
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from PyQt6 import QtGui, QtCore

from zeeref.items import ZeePixmapItem, ZeeTextItem
from zeeref.fileio.errors import ZeeFileIOError
from zeeref.fileio.export import SceneToSVGExporter
from zeeref.types.snapshot import IOResult


def test_scene_to_svg_exporter_get_user_input(scene):
    item1 = ZeePixmapItem(QtGui.QImage(100, 100, QtGui.QImage.Format.Format_RGB32))
    item1.setPos(QtCore.QPointF(0, 0))
    scene.addItem(item1)

    item2 = ZeePixmapItem(QtGui.QImage(100, 100, QtGui.QImage.Format.Format_RGB32))
    item1.setPos(QtCore.QPointF(200, 0))
    scene.addItem(item2)

    assert scene.sceneRect().size().toSize() == QtCore.QSize(300, 100)
    exporter = SceneToSVGExporter(scene)
    value = exporter.get_user_input(None)
    assert value is True
    assert exporter.size == QtCore.QSize(318, 118)


def test_scene_to_svg_exporter_render_pixmap_items(scene):
    item1 = ZeePixmapItem(QtGui.QImage(100, 110, QtGui.QImage.Format.Format_RGB32))
    item1.setPos(QtCore.QPointF(20, 30))
    scene.addItem(item1)

    item2 = ZeePixmapItem(QtGui.QImage(70, 77, QtGui.QImage.Format.Format_RGB32))
    item2.setPos(QtCore.QPointF(50, 50))
    item2.setZValue(-1)
    scene.addItem(item2)

    exporter = SceneToSVGExporter(scene)
    exporter.size = QtCore.QSize(200, 400)
    exporter.margin = 5
    svg = exporter.render_to_svg()

    assert svg.tag == "svg"
    assert svg.get("width") == "200"
    assert svg.get("height") == "400"
    assert svg.get("xmlns") == "http://www.w3.org/2000/svg"
    assert svg.get("xmlns:xlink") == "http://www.w3.org/1999/xlink"
    assert len(svg) == 2

    element = svg[0]  # item2
    assert element.tag == "image"
    assert element.get("xlink:href").startswith("data:image/png;base64,iVBOR")
    assert element.get("width") == "70.0"
    assert element.get("height") == "77.0"
    assert element.get("image-rendering") == "optimizeQuality"
    assert element.get("transform") == "rotate(0.0 35.0 25.0)"
    assert element.get("x") == "35.0"
    assert element.get("y") == "25.0"
    assert element.get("opacity") == "1.0"

    element = svg[1]  # item1
    assert element.tag == "image"
    assert element.get("width") == "100.0"
    assert element.get("height") == "110.0"
    assert element.get("image-rendering") == "optimizeQuality"
    assert element.get("transform") == "rotate(0.0 5.0 5.0)"
    assert element.get("x") == "5.0"
    assert element.get("y") == "5.0"
    assert element.get("opacity") == "1.0"


def test_scene_to_svg_exporter_render_pixmap_with_crop(scene):
    item = ZeePixmapItem(QtGui.QImage(100, 110, QtGui.QImage.Format.Format_RGB32))
    item.setPos(QtCore.QPointF(20, 30))
    item.crop = QtCore.QRectF(20, 25, 30, 33)
    scene.addItem(item)
    exporter = SceneToSVGExporter(scene)
    exporter.size = QtCore.QSize(200, 400)
    exporter.margin = 5
    svg = exporter.render_to_svg()

    assert len(svg) == 1

    element = svg[0]
    assert element.tag == "image"
    assert element.get("width") == "30.0"
    assert element.get("height") == "33.0"
    assert element.get("transform") == "rotate(0.0 -15.0 -20.0)"
    assert element.get("x") == "5.0"
    assert element.get("y") == "5.0"


def test_scene_to_svg_exporter_render_pixmap_with_rotation(scene):
    item = ZeePixmapItem(QtGui.QImage(100, 110, QtGui.QImage.Format.Format_RGB32))
    item.setPos(QtCore.QPointF(20, 30))
    item.setRotation(90)
    scene.addItem(item)
    exporter = SceneToSVGExporter(scene)
    exporter.size = QtCore.QSize(200, 400)
    exporter.margin = 5
    svg = exporter.render_to_svg()

    assert len(svg) == 1

    element = svg[0]
    assert element.tag == "image"
    assert element.get("transform") == "rotate(90.0 115.0 5.0)"
    assert element.get("x") == "115.0"
    assert element.get("y") == "5.0"


def test_scene_to_svg_exporter_render_pixmap_with_opacity(scene):
    item = ZeePixmapItem(QtGui.QImage(100, 110, QtGui.QImage.Format.Format_RGB32))
    item.setPos(QtCore.QPointF(20, 30))
    item.setOpacity(0.75)
    scene.addItem(item)
    exporter = SceneToSVGExporter(scene)
    exporter.size = QtCore.QSize(200, 400)
    exporter.margin = 5
    svg = exporter.render_to_svg()

    assert len(svg) == 1

    element = svg[0]
    assert element.tag == "image"
    assert element.get("opacity") == "0.75"


def test_scene_to_svg_exporter_render_pixmap_with_flip(scene):
    item = ZeePixmapItem(QtGui.QImage(100, 110, QtGui.QImage.Format.Format_RGB32))
    item.setPos(QtCore.QPointF(20, 30))
    item.do_flip()
    scene.addItem(item)
    exporter = SceneToSVGExporter(scene)
    exporter.size = QtCore.QSize(200, 400)
    exporter.margin = 5
    svg = exporter.render_to_svg()

    assert len(svg) == 1

    element = svg[0]
    assert element.tag == "image"
    assert element.get("transform") == (
        "translate(105.0 5.0) scale(-1.0 1)"
        " translate(-105.0 -5.0) rotate(0.0 105.0 5.0)"
    )
    assert element.get("x") == "105.0"
    assert element.get("y") == "5.0"


def test_scene_to_svg_exporter_render_text(scene):
    item = ZeeTextItem("foo")
    item.setPos(QtCore.QPointF(20, 30))
    scene.addItem(item)
    exporter = SceneToSVGExporter(scene)
    exporter.size = QtCore.QSize(200, 400)
    exporter.margin = 5
    svg = exporter.render_to_svg()

    assert len(svg) == 1

    element = svg[0]
    assert element.tag == "text"
    assert element.text == "foo"
    assert element.get("dominant-baseline") == "hanging"
    assert "font-family" in element.get("style")
    assert element.get("transform") == "rotate(0.0 5.0 5.0)"
    assert element.get("x") == "5.0"
    assert element.get("y") == "5.0"


def test_scene_to_svg_exporter_export_when_file_not_writeable(scene, tmp_path):
    filename = tmp_path / "foo.svg"
    with open(filename, "w") as f:
        f.write("foo")
    os.chmod(filename, stat.S_IREAD)
    item = ZeeTextItem("foo")
    scene.addItem(item)
    exporter = SceneToSVGExporter(scene)
    exporter.size = QtCore.QSize(100, 120)

    with pytest.raises(ZeeFileIOError) as e:
        exporter.export(filename)
        assert e.filename == filename


def test_scene_to_svg_exporter_render_with_worker(scene):
    item = ZeeTextItem("foo")
    item.setPos(QtCore.QPointF(20, 30))
    scene.addItem(item)
    exporter = SceneToSVGExporter(scene)
    exporter.size = QtCore.QSize(200, 400)
    exporter.margin = 5

    worker = MagicMock(canceled=False)
    svg = exporter.render_to_svg(worker=worker)
    assert len(svg) == 1
    worker.progress.emit.assert_called_once_with(0)


def test_scene_to_svg_exporter_render_with_worker_canceled(scene):
    item = ZeeTextItem("foo")
    item.setPos(QtCore.QPointF(20, 30))
    scene.addItem(item)
    exporter = SceneToSVGExporter(scene)
    exporter.size = QtCore.QSize(200, 400)
    exporter.margin = 5

    worker = MagicMock(canceled=True)
    svg = exporter.render_to_svg(worker=worker)
    assert svg is None


def test_scene_to_svg_exporter_export_writes_svg(scene, tmp_path):
    filename = tmp_path / "foo.svg"
    item = ZeeTextItem("foo")
    scene.addItem(item)
    exporter = SceneToSVGExporter(scene)
    exporter.size = QtCore.QSize(100, 120)
    exporter.export(filename)

    with open(filename, "rb") as f:
        assert f.read().startswith(b"<?xml")


def test_scene_to_svg_exporter_export_with_worker(scene, tmp_path):
    filename = tmp_path / "foo.svg"
    item = ZeeTextItem("foo")
    scene.addItem(item)
    exporter = SceneToSVGExporter(scene)
    exporter.size = QtCore.QSize(100, 120)
    worker = MagicMock(canceled=False)
    exporter.export(filename, worker)

    worker.begin_processing.emit.assert_called_once_with(1)
    worker.progress.emit.assert_called_once_with(0)
    worker.finished.emit.assert_called_once_with(
        IOResult(filename=Path(filename), errors=[])
    )
    with open(filename, "rb") as f:
        assert f.read().startswith(b"<?xml")


def test_scene_to_svg_exporter_export_with_worker_canceled(scene, tmp_path):
    filename = tmp_path / "foo.svg"
    item = ZeeTextItem("foo")
    scene.addItem(item)
    exporter = SceneToSVGExporter(scene)
    exporter.size = QtCore.QSize(100, 120)
    worker = MagicMock(canceled=True)
    exporter.export(filename, worker)

    worker.begin_processing.emit.assert_called_once_with(1)
    worker.progress.emit.assert_called_once_with(0)
    worker.finished.emit.assert_called_once_with(
        IOResult(filename=Path(filename), errors=[])
    )
    os.path.exists(filename) is False


def test_scene_to_svg_exporter_export_when_file_not_writeable_with_worker(
    scene, tmp_path
):
    filename = tmp_path / "foo.svg"
    with open(filename, "w") as f:
        f.write("foo")
    os.chmod(filename, stat.S_IREAD)
    item = ZeeTextItem("foo")
    scene.addItem(item)
    exporter = SceneToSVGExporter(scene)
    exporter.size = QtCore.QSize(100, 120)
    worker = MagicMock(canceled=False)

    exporter.export(filename, worker=worker)
    worker.begin_processing.emit.assert_called_once_with(1)
    worker.progress.emit.assert_called_once_with(0)
    worker.finished.emit.assert_called_once()
    result = worker.finished.emit.call_args.args[0]
    assert isinstance(result, IOResult)
    assert result.filename == filename
    assert len(result.errors) == 1
