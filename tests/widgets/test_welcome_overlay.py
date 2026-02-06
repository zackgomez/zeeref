from PyQt6 import QtCore, QtWidgets
from PyQt6.QtCore import Qt

from beeref.view import BeeGraphicsView
from beeref.widgets.welcome_overlay import WelcomeOverlay


def test_welcome_overlay_is_transparent_for_mouse_events(qapp):
    parent = QtWidgets.QMainWindow()
    view = BeeGraphicsView(qapp, parent)
    overlay = WelcomeOverlay(view)
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
