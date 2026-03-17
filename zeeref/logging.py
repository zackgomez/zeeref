# This file is part of ZeeRef.
#
# ZeeRef is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# ZeeRef is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with ZeeRef.  If not, see <https://www.gnu.org/licenses/>.

from __future__ import annotations

import logging
import logging.handlers
import os.path
from typing import Any, cast

from PyQt6 import QtCore


TRACE = 5


class ZeeLogger(logging.Logger):
    def __init__(self, name: str, level: int = logging.NOTSET) -> None:
        super().__init__(name, level)
        logging.addLevelName(TRACE, "TRACE")

    def trace(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self.log(TRACE, msg, *args, **kwargs)


def getLogger(name: str) -> ZeeLogger:
    return cast(ZeeLogger, logging.getLogger(name))


logging.setLoggerClass(ZeeLogger)


class ZeeRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """RotatingFileHandler that creates log directory if necessary."""

    def __init__(self, filename, **kwargs):
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        super().__init__(filename, **kwargs)


qtlogger = logging.getLogger("Qt")


def qt_message_handler(mode, context, message):
    logfuncs = {
        QtCore.QtMsgType.QtDebugMsg: qtlogger.debug,
        QtCore.QtMsgType.QtInfoMsg: qtlogger.info,
        QtCore.QtMsgType.QtWarningMsg: qtlogger.warning,
        QtCore.QtMsgType.QtCriticalMsg: qtlogger.critical,
        QtCore.QtMsgType.QtFatalMsg: qtlogger.fatal,
    }
    if context and (context.file or context.line or context.function):
        message = (
            f"{message}: File {context.file}, line {context.line}, "
            f"in {context.function}"
        )

    logfuncs[mode](message)
