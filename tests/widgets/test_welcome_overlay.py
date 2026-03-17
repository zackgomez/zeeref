from PyQt6 import QtWidgets
from PyQt6.QtCore import Qt

from zeeref.view import ZeeGraphicsView
from zeeref.widgets.welcome_overlay import WelcomeOverlay


def test_welcome_overlay_is_transparent_for_mouse_events(qapp):
    parent = QtWidgets.QMainWindow()
    view = ZeeGraphicsView(qapp, parent)
    overlay = WelcomeOverlay(view)
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
