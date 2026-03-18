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

from zeeref.fileio.errors import ZeeFileIOError
from zeeref.fileio.io import drain_zref, load_zref, load_images, save_zref
from zeeref.fileio.scratch import (
    create_scratch_file,
    delete_scratch_file,
    derive_swp_path,
    list_recovery_files,
)
from zeeref.types.snapshot import IOResult, LoadResult, SaveResult
from zeeref.fileio.sql import is_zref_file
from zeeref.fileio.thread import ThreadedIO

__all__ = [
    "ZeeFileIOError",
    "IOResult",
    "LoadResult",
    "SaveResult",
    "ThreadedIO",
    "create_scratch_file",
    "delete_scratch_file",
    "derive_swp_path",
    "drain_zref",
    "is_zref_file",
    "list_recovery_files",
    "load_zref",
    "load_images",
    "save_zref",
]
