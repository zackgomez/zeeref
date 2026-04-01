from PyQt6 import QtWidgets

from zeeref.view import ZeeGraphicsView


def test_welcome_overlay_has_recent_header(qapp):
    parent = QtWidgets.QMainWindow()
    view = ZeeGraphicsView(qapp, parent)
    overlay = view.welcome_overlay
    assert overlay.recent_header is not None
