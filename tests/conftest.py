import os
import os.path
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
import uuid

from unittest.mock import MagicMock, patch

from PyQt6 import QtGui, QtWidgets


def pytest_configure(config):
    # Ignore logging configuration for ZeeRef during test runs. This
    # avoids logging to the regular log file and spamming test output
    # with debug messages.
    #
    # This needs to be done before the application code is even loaded since
    # logging configuration happens on module level
    import logging.config

    logging.config.dictConfig = MagicMock


@pytest.fixture(autouse=True)
def no_modal_dialogs():
    """Prevent any modal dialogs from blocking tests.

    Tests that need to test dialog behavior should mock QMessageBox themselves.
    """
    with patch(
        "PyQt6.QtWidgets.QMessageBox.question",
        return_value=QtWidgets.QMessageBox.StandardButton.Yes,
    ):
        yield


@pytest.fixture(autouse=True)
def reset_zeeref_actions():
    from zeeref.actions.actions import actions

    for key in list(actions.keys()):
        if key.startswith("recent_files_"):
            actions.pop(key)


@pytest.fixture(autouse=True)
def commandline_args():
    config_patcher = patch("zeeref.view.commandline_args")
    config_mock = config_patcher.start()
    config_mock.filenames = []
    yield config_mock
    config_patcher.stop()


@pytest.fixture(autouse=True)
def settings(tmp_path):
    from zeeref.config import ZeeSettings

    dir_patcher = patch(
        "zeeref.config.ZeeSettings.get_settings_dir", return_value=str(tmp_path)
    )
    dir_patcher.start()
    settings = ZeeSettings()
    os.makedirs(os.path.dirname(settings.fileName()), exist_ok=True)
    yield settings
    settings.clear()
    dir_patcher.stop()


@pytest.fixture(autouse=True)
def kbsettings(settings):
    from zeeref.config import KeyboardSettings

    kbsettings = KeyboardSettings()
    for actions in (kbsettings.MOUSEWHEEL_ACTIONS, kbsettings.MOUSE_ACTIONS):
        for action in actions.values():
            action.__dict__.pop("kb_settings", None)
    yield kbsettings
    kbsettings.clear()
    for actions in (kbsettings.MOUSEWHEEL_ACTIONS, kbsettings.MOUSE_ACTIONS):
        for action in actions.values():
            action.__dict__.pop("kb_settings", None)


@pytest.fixture
def main_window(qtbot):
    from zeeref.__main__ import ZeeRefMainWindow

    app = QtWidgets.QApplication.instance()
    main = ZeeRefMainWindow(app)
    qtbot.addWidget(main)
    yield main
    # Bypass unsaved changes dialog during teardown so qtbot can close
    # the window without blocking. Tests that need to test the
    # confirmation behavior patch it themselves via @patch decorators.
    main.view.get_confirmation_unsaved_changes = lambda msg: True


@pytest.fixture
def view(main_window):
    yield main_window.view


@pytest.fixture
def imgfilename3x3():
    yield str(Path(__file__).parent / "assets" / "test3x3.png")


@pytest.fixture
def imgdata3x3(imgfilename3x3):
    with open(imgfilename3x3, "rb") as f:
        imgdata3x3 = f.read()
    yield imgdata3x3


@pytest.fixture
def tmpfile(tmp_path):
    yield tmp_path / str(uuid.uuid4())


@pytest.fixture
def scene(qapp):
    from zeeref.scene import ZeeGraphicsScene

    yield ZeeGraphicsScene(QtGui.QUndoStack())


@pytest.fixture
def item():
    from zeeref.items import ZeePixmapItem

    yield ZeePixmapItem(QtGui.QImage(10, 10, QtGui.QImage.Format.Format_RGB32))


@pytest.fixture(scope="session")
def qapp():
    from zeeref.__main__ import ZeeRefApplication

    yield ZeeRefApplication([])
