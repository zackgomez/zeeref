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

from collections.abc import Callable
from typing import Any

from PyQt6 import QtCore

from zeeref.types.snapshot import IOResult


class ThreadedIO(QtCore.QThread):
    """Dedicated thread for loading and saving."""

    progress = QtCore.pyqtSignal(int)
    finished = QtCore.pyqtSignal(IOResult)
    begin_processing = QtCore.pyqtSignal(int)
    user_input_required = QtCore.pyqtSignal(str)

    def __init__(self, func: Callable[..., None], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.func: Callable[..., None] = func
        self.args: tuple[Any, ...] = args
        self.kwargs: dict[str, Any] = kwargs
        self.kwargs["worker"] = self
        self.canceled: bool = False

    def run(self) -> None:
        self.func(*self.args, **self.kwargs)

    def on_canceled(self) -> None:
        self.canceled = True
