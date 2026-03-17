import math
import os.path
from pathlib import Path

import httpretty
import pytest

from PyQt6 import QtCore, QtGui

from zeeref.fileio.image import load_pil_image, load_image


def test_load_pil_image_without_path(qapp):
    img = load_pil_image(None)
    assert img.isNull() is True


def test_load_pil_image_not_a_file(qapp):
    img = load_pil_image("foo")
    assert img.isNull() is True


@pytest.mark.parametrize(
    "path,expected",
    [
        ("test3x3.png", "test3x3.png"),
        ("test3x3_orientation1.jpg", "test3x3.jpg"),
        ("test3x3_orientation2.jpg", "test3x3.jpg"),
        ("test3x3_orientation3.jpg", "test3x3.jpg"),
        ("test3x3_orientation4.jpg", "test3x3.jpg"),
        ("test3x3_orientation5.jpg", "test3x3.jpg"),
        ("test3x3_orientation6.jpg", "test3x3.jpg"),
        ("test3x3_orientation7.jpg", "test3x3.jpg"),
        ("test3x3_orientation8.jpg", "test3x3.jpg"),
    ],
)
def test_load_pil_image_exif_orientation(path, expected, qapp):
    def get_fname(p):
        root = os.path.dirname(__file__)
        return os.path.join(root, "..", "assets", p)

    img = load_pil_image(get_fname(path))
    assert img.isNull() is False
    expected = QtGui.QImage(get_fname(expected))
    assert expected.isNull() is False

    # The JPEG format isn't pixel perfect, so we have to check whether
    # pixels are approximately the same:
    for x in range(3):
        for y in range(3):
            col_img = img.pixelColor(x, y).getRgb()
            col_expected = expected.pixelColor(x, y).getRgb()
            diff = [(col_img[i] - col_expected[i]) ** 2 for i in range(4)]
            assert math.sqrt(sum(diff)) < 3


def test_load_image_loads_from_filename(view, imgfilename3x3):
    img, filename = load_image(Path(imgfilename3x3))
    assert img.isNull() is False
    assert filename == imgfilename3x3


def test_load_image_loads_from_nonexisting_filename(view, imgfilename3x3):
    img, filename = load_image(Path("foo.png"))
    assert img.isNull() is True
    assert filename == "foo.png"


def test_load_image_loads_from_existing_local_url(view, imgfilename3x3):
    url = QtCore.QUrl.fromLocalFile(imgfilename3x3)
    img, filename = load_image(url)
    assert img.isNull() is False
    assert filename == imgfilename3x3


@httpretty.activate
def test_load_image_loads_from_existing_web_url(view, imgdata3x3):
    url = "http://example.com/foo.png"
    httpretty.register_uri(
        httpretty.GET,
        url,
        body=imgdata3x3,
    )
    img, filename = load_image(QtCore.QUrl(url))
    assert img.isNull() is False
    assert filename == url


@httpretty.activate
def test_load_image_loads_from_existing_web_url_non_ascii(view, imgdata3x3):
    url = "http://example.com/föö.png"
    httpretty.register_uri(
        httpretty.GET,
        url,
        body=imgdata3x3,
    )
    img, filename = load_image(QtCore.QUrl(url))
    assert img.isNull() is False
    assert filename == "http://example.com/f%C3%B6%C3%B6.png"


@httpretty.activate
def test_load_image_loads_from_web_url_errors(view, imgfilename3x3):
    url = "http://example.com/foo.png"
    httpretty.register_uri(
        httpretty.GET,
        url,
        status=500,
    )
    img, filename = load_image(QtCore.QUrl(url))
    assert img.isNull() is True
    assert filename == url
