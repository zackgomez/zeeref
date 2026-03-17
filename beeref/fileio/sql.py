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

"""BeeRef's native file format is using SQLite. Embedded files are
stored in an sqlar table so that they can be extracted using sqlite's
archive command line option.

For more info, see:

https://www.sqlite.org/appfileformat.html
https://www.sqlite.org/sqlar.html
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from beeref import constants
from beeref.logging import getLogger
from .errors import BeeFileIOError
from .schema import SCHEMA, USER_VERSION, MIGRATIONS, APPLICATION_ID
from .snapshot import ErrorItemSnapshot, ItemSnapshot, PixmapItemSnapshot

if TYPE_CHECKING:
    from beeref.fileio import ThreadedIO


logger = getLogger(__name__)


def is_bee_file(path: Path) -> bool:
    """Check whether the file at the given path is a bee file."""

    return path.suffix == ".bee"


def handle_sqlite_errors[T: Callable[..., Any]](func: T) -> T:
    def wrapper(self: SQLiteIO, *args: Any, **kwargs: Any) -> Any:
        try:
            return func(self, *args, **kwargs)
        except Exception as e:
            logger.exception(f"Error while reading/writing {self.filename}")
            try:
                if hasattr(self, "_connection") and self._connection.in_transaction:
                    self.ex("ROLLBACK")
                    logger.debug("Transaction rolled back")
            except sqlite3.Error:
                pass
            self._close_connection()
            raise BeeFileIOError(msg=str(e), filename=self.filename) from e

    # wrapper has a different signature than func (added error handling),
    # but we preserve the decorated function's identity for callers.
    return wrapper  # type: ignore[return-value]


class SQLiteIO:
    def __init__(
        self,
        filename: Path,
        create_new: bool = False,
        readonly: bool = False,
        worker: ThreadedIO | None = None,
    ) -> None:
        self.create_new: bool = create_new
        self.filename: Path = filename
        self.readonly: bool = readonly
        self.worker: ThreadedIO | None = worker
        self.retry: bool = False

    def __del__(self) -> None:
        self._close_connection()

    def _close_connection(self) -> None:
        if hasattr(self, "_connection"):
            self._connection.close()
            delattr(self, "_connection")
        if hasattr(self, "_cursor"):
            delattr(self, "_cursor")
        if hasattr(self, "_tmpdir"):
            self._tmpdir.cleanup()
            delattr(self, "_tmpdir")

    def _establish_connection(self) -> None:
        if self.create_new and not self.readonly and os.path.exists(self.filename):
            os.remove(self.filename)

        uri = self.filename.resolve().as_uri()
        if self.readonly:
            uri = f"{uri}?mode=rw"
        self._connection = sqlite3.connect(uri, uri=True)
        self._cursor = self.connection.cursor()
        if not self.create_new:
            try:
                self._migrate()
            except Exception:
                # Updating a file failed; try creating it from scratch instead
                logger.exception("Error migrating bee file")
                self.create_new = True
                self._establish_connection()

    def _migrate(self) -> None:
        """Migrate database if necessary."""

        version = self.fetchone("PRAGMA user_version")[0]
        logger.debug(f"Found bee file version: {version}")
        if version >= USER_VERSION:
            logger.debug("Version ok; no migrations necessary")
            return

        if self.readonly:
            try:
                # See whether file is writable so we can migrate it directly
                self.ex("PRAGMA application_id=%s" % APPLICATION_ID)
            except sqlite3.Error:
                logger.debug("File not writable; use temporary copy instead")
                self._connection.close()
                self._tmpdir = tempfile.TemporaryDirectory(prefix=constants.APPNAME)
                tmpname = os.path.join(self._tmpdir.name, "mig.bee")
                shutil.copyfile(self.filename, tmpname)
                self._connection = sqlite3.connect(tmpname)
                self._cursor = self.connection.cursor()

        self.ex("BEGIN TRANSACTION")
        for i in range(version, USER_VERSION):
            logger.debug(f"Migrating from version {i} to {i + 1}...")
            for migration in MIGRATIONS[i + 1]:
                migration(self)
        self.write_meta()
        self.connection.commit()
        logger.debug("Migration finished")

    @property
    def connection(self) -> sqlite3.Connection:
        if not hasattr(self, "_connection"):
            self._establish_connection()
        return self._connection

    @property
    def cursor(self) -> sqlite3.Cursor:
        if not hasattr(self, "_cursor"):
            self._establish_connection()
        return self._cursor

    def ex(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        return self.cursor.execute(*args, **kwargs)

    def exmany(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        return self.cursor.executemany(*args, **kwargs)

    def fetchone(self, *args: Any, **kwargs: Any) -> Any:
        self.ex(*args, **kwargs)
        return self.cursor.fetchone()

    def fetchall(self, *args: Any, **kwargs: Any) -> list[Any]:
        self.ex(*args, **kwargs)
        return self.cursor.fetchall()

    def write_meta(self) -> None:
        self.ex("PRAGMA application_id=%s" % APPLICATION_ID)
        self.ex("PRAGMA user_version=%s" % USER_VERSION)
        self.ex("PRAGMA foreign_keys=ON")

    def create_schema_on_new(self) -> None:
        if self.create_new:
            self.write_meta()
            for schema in SCHEMA:
                self.ex(schema)

    @handle_sqlite_errors
    def read(self) -> list[ItemSnapshot]:
        rows = self.fetchall(
            "SELECT items.id, type, x, y, z, scale, rotation, flip, "
            "items.data, sqlar.data, items.created_at "
            "FROM sqlar JOIN items on sqlar.item_id = items.id"
        )
        # Avoid OUTER JOIN for performance reasons; fetch text items
        # separately instead
        rows.extend(
            self.fetchall(
                "SELECT items.id, type, x, y, z, scale, rotation, flip, "
                "items.data, null as data, items.created_at "
                "FROM items "
                'WHERE items.type = "text"'
            )
        )
        if self.worker:
            self.worker.begin_processing.emit(len(rows))

        snapshots: list[ItemSnapshot] = []
        for i, row in enumerate(rows):
            save_id: str = row[0]
            item_type: str = row[1]
            x: float = row[2]
            y: float = row[3]
            z: float = row[4]
            scale: float = row[5]
            rotation: float = row[6]
            flip: float = row[7]
            data: dict[str, Any] = json.loads(row[8])
            created_at: float = row[10] or 0.0

            if item_type == "pixmap":
                snapshots.append(
                    PixmapItemSnapshot(
                        save_id=save_id,
                        type=item_type,
                        x=x,
                        y=y,
                        z=z,
                        scale=scale,
                        rotation=rotation,
                        flip=flip,
                        data=data,
                        created_at=created_at,
                        width=0,
                        height=0,
                        export_filename="",
                        pixmap_bytes=row[9],
                    )
                )
            else:
                snapshots.append(
                    ItemSnapshot(
                        save_id=save_id,
                        type=item_type,
                        x=x,
                        y=y,
                        z=z,
                        scale=scale,
                        rotation=rotation,
                        flip=flip,
                        data=data,
                        created_at=created_at,
                    )
                )

            if self.worker:
                logger.trace(f"Emit progress: {i}")
                self.worker.progress.emit(i)
                if self.worker.canceled:
                    return snapshots
        return snapshots

    @handle_sqlite_errors
    def write(self, snapshots: list[ItemSnapshot], compact: bool = False) -> list[str]:
        if self.readonly:
            raise sqlite3.OperationalError("Attempt to write to a readonly database")
        try:
            self.create_schema_on_new()
            return self.write_data(snapshots, compact=compact)
        except Exception:
            if self.retry:
                raise
            else:
                self.retry = True
                logger.exception(f"Updating to existing file {self.filename} failed")
                self.create_new = True
                self._close_connection()
                return self.write(snapshots, compact=compact)

    def write_data(
        self, snapshots: list[ItemSnapshot], compact: bool = False
    ) -> list[str]:
        existing_ids = {row[0] for row in self.fetchall("SELECT id from ITEMS")}
        to_delete = set(existing_ids)

        if self.worker:
            self.worker.begin_processing.emit(len(snapshots))
        newly_saved: list[str] = []
        for i, snap in enumerate(snapshots):
            if isinstance(snap, ErrorItemSnapshot):
                to_delete.discard(snap.save_id)
                continue
            logger.debug(f"Saving {snap.type} with id {snap.save_id}")
            if snap.save_id in existing_ids:
                self._update_snapshot(snap)
                to_delete.discard(snap.save_id)
            else:
                self._insert_snapshot(snap)
                newly_saved.append(snap.save_id)
            if self.worker:
                self.worker.progress.emit(i)
                if self.worker.canceled:
                    break
        if compact:
            self.delete_items(to_delete)
            self.ex("VACUUM")
        self.connection.commit()
        return newly_saved

    def delete_items(self, to_delete: set[str]) -> None:
        items = [(pk,) for pk in to_delete]
        self.exmany("DELETE FROM items WHERE id=?", items)
        self.exmany("DELETE FROM sqlar WHERE item_id=?", items)
        self.connection.commit()

    def _insert_snapshot(self, snap: ItemSnapshot) -> None:
        """Insert a new item from a snapshot."""
        width = snap.width if isinstance(snap, PixmapItemSnapshot) else None
        height = snap.height if isinstance(snap, PixmapItemSnapshot) else None
        self.ex(
            "INSERT INTO items (id, type, x, y, z, scale, rotation, flip, "
            "data, width, height, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                snap.save_id,
                snap.type,
                snap.x,
                snap.y,
                snap.z,
                snap.scale,
                snap.rotation,
                snap.flip,
                json.dumps(snap.data),
                width,
                height,
                snap.created_at,
            ),
        )

        if isinstance(snap, PixmapItemSnapshot) and snap.pixmap_bytes:
            self.ex(
                "INSERT INTO sqlar (item_id, name, mode, sz, data) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    snap.save_id,
                    snap.export_filename,
                    0o644,
                    len(snap.pixmap_bytes),
                    snap.pixmap_bytes,
                ),
            )
        self.connection.commit()

    def _update_snapshot(self, snap: ItemSnapshot) -> None:
        """Update an existing item's metadata from a snapshot."""
        self.ex(
            "UPDATE items SET x=?, y=?, z=?, scale=?, rotation=?, flip=?, "
            "data=? "
            "WHERE id=?",
            (
                snap.x,
                snap.y,
                snap.z,
                snap.scale,
                snap.rotation,
                snap.flip,
                json.dumps(snap.data),
                snap.save_id,
            ),
        )
        self.connection.commit()
