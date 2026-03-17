# This file is part of BeeRef.
#
# BeeRef is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# BeeRef is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with BeeRef.  If not, see <https://www.gnu.org/licenses/>.

"""Scratch file (working copy) management for crash recovery and saving."""

import hashlib
import logging
import os
import uuid

from beeref.config import BeeSettings
from beeref.fileio.schema import SCHEMA

logger = logging.getLogger(__name__)


def derive_swp_path(original: str) -> str:
    """Derive the .swp path in the recovery dir for a given original file."""
    recovery_dir = BeeSettings().get_recovery_dir()
    stem = os.path.splitext(os.path.basename(original))[0]
    path_hash = hashlib.sha256(original.encode()).hexdigest()[:8]
    return os.path.join(recovery_dir, f"{stem}_{path_hash}.bee.swp")


def derive_untitled_swp_path() -> str:
    """Create a .swp path for a new unsaved scene."""
    recovery_dir = BeeSettings().get_recovery_dir()
    suffix = uuid.uuid4().hex[:8]
    return os.path.join(recovery_dir, f"untitled_{suffix}.bee.swp")


def create_scratch_file(original: str | None, worker=None) -> str:
    """Create a scratch file in the recovery dir.

    If original is a path, copies the file with progress reporting.
    If original is None, creates an empty database with the schema.
    Returns the path to the scratch file.
    """
    if original:
        swp = derive_swp_path(original)
        total = os.path.getsize(original)
        if worker:
            worker.begin_processing.emit(100)
        with open(original, "rb") as src, open(swp, "wb") as dst:
            copied = 0
            while chunk := src.read(1024 * 1024):
                dst.write(chunk)
                copied += len(chunk)
                if worker:
                    worker.progress.emit(int(copied / total * 100))
        logger.info(f"Created scratch file: {swp}")
    else:
        import sqlite3

        swp = derive_untitled_swp_path()
        conn = sqlite3.connect(swp)
        cursor = conn.cursor()
        for sql in SCHEMA:
            cursor.execute(sql)
        conn.commit()
        conn.close()
        logger.info(f"Created empty scratch file: {swp}")

    return swp


def delete_scratch_file(swp_path: str) -> None:
    """Delete a scratch file if it exists."""
    if os.path.exists(swp_path):
        os.remove(swp_path)
        logger.info(f"Deleted scratch file: {swp_path}")


def list_recovery_files() -> list[str]:
    """List all .swp files in the recovery directory."""
    recovery_dir = BeeSettings().get_recovery_dir()
    if not os.path.exists(recovery_dir):
        return []
    return [
        os.path.join(recovery_dir, f)
        for f in os.listdir(recovery_dir)
        if f.endswith(".bee.swp")
    ]
