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

from __future__ import annotations

import hashlib
import logging
import sqlite3
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from beeref.config import BeeSettings
from beeref.fileio.schema import APPLICATION_ID, SCHEMA, USER_VERSION

if TYPE_CHECKING:
    from beeref.fileio import ThreadedIO

logger = logging.getLogger(__name__)


def derive_swp_path(original: Path) -> Path:
    """Derive the .swp path in the recovery dir for a given original file."""
    recovery_dir = Path(BeeSettings().get_recovery_dir())
    path_hash = hashlib.sha256(str(original).encode()).hexdigest()[:8]
    return recovery_dir / f"{original.stem}_{path_hash}.bee.swp"


def derive_untitled_swp_path() -> Path:
    """Create a .swp path for a new unsaved scene."""
    recovery_dir = Path(BeeSettings().get_recovery_dir())
    suffix = uuid.uuid4().hex[:8]
    return recovery_dir / f"untitled_{suffix}.bee.swp"


def copy_with_progress(
    src_path: Path, dst_path: Path, worker: ThreadedIO | None = None
) -> None:
    """Copy a file with optional progress reporting via worker signals."""
    total = src_path.stat().st_size
    if worker:
        worker.begin_processing.emit(100)
    with open(src_path, "rb") as src, open(dst_path, "wb") as dst:
        copied = 0
        while chunk := src.read(1024 * 1024):
            dst.write(chunk)
            copied += len(chunk)
            if worker:
                worker.progress.emit(int(copied / total * 100))


def create_scratch_file(
    original: Path | None, worker: ThreadedIO | None = None
) -> Path:
    """Create a scratch file in the recovery dir.

    If original is a path, copies the file with progress reporting.
    If original is None, creates an empty database with the schema.
    Returns the path to the scratch file.
    """
    if original:
        swp = derive_swp_path(original)
        copy_with_progress(original, swp, worker=worker)
        logger.info(f"Created scratch file: {swp}")
    else:
        swp = derive_untitled_swp_path()
        conn = sqlite3.connect(swp)
        cursor = conn.cursor()
        cursor.execute("PRAGMA application_id=%s" % APPLICATION_ID)
        cursor.execute("PRAGMA user_version=%s" % USER_VERSION)
        cursor.execute("PRAGMA foreign_keys=ON")
        for sql in SCHEMA:
            cursor.execute(sql)
        conn.commit()
        conn.close()
        logger.info(f"Created empty scratch file: {swp}")

    return swp


def delete_scratch_file(swp_path: Path) -> None:
    """Delete a scratch file if it exists."""
    if swp_path.exists():
        swp_path.unlink()
        logger.info(f"Deleted scratch file: {swp_path}")


def list_recovery_files() -> list[Path]:
    """List all .swp files in the recovery directory."""
    recovery_dir = Path(BeeSettings().get_recovery_dir())
    if not recovery_dir.exists():
        return []
    return [p for p in recovery_dir.iterdir() if p.name.endswith(".bee.swp")]
