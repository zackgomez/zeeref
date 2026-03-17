from PyQt6 import QtGui

from zeeref.assets import ZeeAssets


def test_singleton(view):
    assert ZeeAssets() is ZeeAssets()
    assert ZeeAssets().logo is ZeeAssets().logo


def test_has_logo(view):
    assert isinstance(ZeeAssets().logo, QtGui.QIcon)
