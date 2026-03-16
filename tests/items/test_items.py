from PyQt6 import QtGui

from beeref.items import sort_by_filename, BeePixmapItem, BeeTextItem


def test_sort_by_filename(view):
    item1 = BeePixmapItem(QtGui.QImage())
    item1.filename = None
    item1.created_at = 3.0

    item2 = BeePixmapItem(QtGui.QImage())
    item2.filename = "foo.png"

    item3 = BeePixmapItem(QtGui.QImage())
    item3.filename = None
    item3.created_at = 2.0

    item4 = BeePixmapItem(QtGui.QImage())
    item4.filename = "bar.png"

    item5 = BeePixmapItem(QtGui.QImage())
    item5.filename = None
    item5.created_at = 1.0

    result = sort_by_filename([item1, item2, item3, item4, item5])
    # First: items with filename, sorted by filename (bar.png < foo.png)
    # Then: items without filename, sorted by created_at (1 < 2 < 3)
    assert result == [item4, item2, item5, item3, item1]


def test_sort_by_filename_when_only_by_filename(view):
    item1 = BeePixmapItem(QtGui.QImage())
    item1.filename = "foo.png"
    item2 = BeePixmapItem(QtGui.QImage())
    item2.filename = "bar.png"
    assert sort_by_filename([item1, item2]) == [item2, item1]


def test_sort_by_filename_when_only_by_created_at(view):
    item1 = BeePixmapItem(QtGui.QImage())
    item1.filename = None
    item1.created_at = 2.0
    item2 = BeePixmapItem(QtGui.QImage())
    item2.filename = None
    item2.created_at = 1.0
    assert sort_by_filename([item1, item2]) == [item2, item1]


def test_sort_by_filename_deals_with_text_items(view):
    item1 = BeeTextItem("Foo")
    item2 = BeeTextItem("Bar")
    assert len(sort_by_filename([item1, item2])) == 2
