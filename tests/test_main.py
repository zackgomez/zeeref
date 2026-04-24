from pathlib import Path
from unittest.mock import patch, MagicMock

from PyQt6 import QtCore

from zeeref.__main__ import ZeeRefMainWindow, main
from zeeref.assets import ZeeAssets
from zeeref.view import ZeeGraphicsView


@patch("PyQt6.QtWidgets.QWidget.show")
def test_zeeref_mainwindow_init(show_mock, qapp):
    window = ZeeRefMainWindow(qapp)
    assert window.windowTitle() == "ZeeRef"
    assert ZeeAssets().logo == ZeeAssets().logo
    assert window.windowIcon()
    assert window.contentsMargins() == QtCore.QMargins(0, 0, 0, 0)
    assert isinstance(window.view, ZeeGraphicsView)
    show_mock.assert_called()


@patch("zeeref.view.ZeeGraphicsView.open_from_file")
def test_zeerefapplication_fileopenevent(open_mock, qapp, main_window):
    event = MagicMock()
    event.type.return_value = QtCore.QEvent.Type.FileOpen
    event.file.return_value = "test.zref"
    assert qapp.event(event) is True
    open_mock.assert_called_once_with(Path("test.zref"))


@patch("zeeref.__main__.ZeeRefApplication")
@patch("zeeref.__main__.CommandlineArgs")
@patch("zeeref.config.ZeeSettings.on_startup")
def test_main(startup_mock, args_mock, app_mock, qapp):
    app_mock.return_value = qapp
    args_mock.return_value.filename = None
    args_mock.return_value.loglevel = "WARN"
    args_mock.return_value.debug_raise_error = ""
    args_mock.return_value.session = None

    with patch.object(qapp, "exec") as exec_mock:
        main()
        exec_mock.assert_called_once_with()

    args_mock.assert_called_once_with(with_check=True)
    startup_mock.assert_called()
