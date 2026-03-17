import json
import os
import os.path
import stat
from unittest.mock import MagicMock, patch

from PyQt6 import QtCore, QtGui
import pytest

from beeref.fileio import schema, is_bee_file
from beeref.fileio.errors import BeeFileIOError
from beeref.fileio.snapshot import ItemSnapshot, PixmapItemSnapshot
from beeref.fileio.sql import SQLiteIO
from beeref.items import (
    BeePixmapItem,
    BeeTextItem,
    BeeErrorItem,
    create_item_from_snapshot,
)


@pytest.mark.parametrize(
    "filename,expected",
    [
        (os.path.join("foo", "bar.bee"), True),
        (os.path.join("foo", "bar.png"), False),
        (os.path.join("foo", "bar"), False),
    ],
)
def test_is_bee_file(filename, expected):
    assert is_bee_file(filename) is expected


def test_sqliteio_migrate_does_nothing_when_version_ok(tmpfile):
    io = SQLiteIO(tmpfile, create_new=True)
    io.ex("PRAGMA user_version=%s" % schema.USER_VERSION)
    io.connection.commit()
    del io
    with patch("beeref.fileio.sql.SQLiteIO.ex") as ex_mock:
        SQLiteIO(tmpfile)
        ex_mock.assert_not_called()


@patch("beeref.fileio.sql.USER_VERSION", 3)
@patch(
    "beeref.fileio.sql.MIGRATIONS",
    {
        2: [
            lambda io: io.ex("CREATE TABLE foo (col1 INT)"),
            lambda io: io.ex("CREATE TABLE bar (baz INT)"),
        ],
        3: [lambda io: io.ex("ALTER TABLE foo ADD COLUMN col2 TEXT")],
    },
)
def test_sqliteio_migrate_migrates(tmpfile):
    io = SQLiteIO(tmpfile, create_new=True)
    io.ex("PRAGMA user_version=1")
    io.connection.commit()
    del io
    io = SQLiteIO(tmpfile)
    io.ex('INSERT INTO foo (col1, col2) VALUES (22, "hello world")')
    io.ex("INSERT INTO bar (baz) VALUES (55)")
    result = io.fetchone("PRAGMA user_version")
    assert result[0] == 3


@patch("beeref.fileio.sql.USER_VERSION", 3)
@patch(
    "beeref.fileio.sql.MIGRATIONS",
    {
        2: [
            lambda io: io.ex("CREATE TABLE foo (col1 INT)"),
            lambda io: io.ex("CREATE TABLE bar (baz INT)"),
        ],
        3: [lambda io: io.ex("ALTER TABLE foo ADD COLUMN col2 TEXT")],
    },
)
def test_sqliteio_migrate_migrates_when_file_not_writable(tmpfile):
    io = SQLiteIO(tmpfile, create_new=True)
    io.ex("PRAGMA user_version=1")
    io.connection.commit()
    del io
    os.chmod(tmpfile, stat.S_IREAD)
    with pytest.raises(PermissionError):
        open(tmpfile, "w")
    io = SQLiteIO(tmpfile, readonly=True)
    io.ex('INSERT INTO foo (col1, col2) VALUES (22, "hello world")')
    io.ex("INSERT INTO bar (baz) VALUES (55)")
    result = io.fetchone("PRAGMA user_version")
    assert result[0] == 3
    newdir = io._tmpdir.name
    del io
    assert os.path.exists(newdir) is False


def test_all_migrations(tmpfile):
    io = SQLiteIO(tmpfile, create_new=True)

    # Set up version 1 bee file
    io.ex("PRAGMA user_version=1")
    io.ex("""
        CREATE TABLE items (
          id INTEGER PRIMARY KEY,
          type TEXT NOT NULL,
          x REAL DEFAULT 0,
          y REAL DEFAULT 0,
          z REAL DEFAULT 0,
          scale REAL DEFAULT 1,
          rotation REAL DEFAULT 0,
          flip INTEGER DEFAULT 1,
          filename TEXT)""")
    io.ex("""
        CREATE TABLE sqlar (
            name TEXT PRIMARY KEY,
            item_id INTEGER NOT NULL,
            mode INT,
            mtime INT default current_timestamp,
            sz INT,
            data BLOB,
            FOREIGN KEY (item_id)
              REFERENCES items (id)
                 ON DELETE CASCADE
                 ON UPDATE NO ACTION)""")
    io.ex(
        "INSERT INTO items "
        "(type, x, y, z, scale, rotation, flip, filename) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ",
        ("pixmap", 22.2, 33.3, 0.22, 3.4, 45, -1, "bee.png"),
    )
    io.ex("INSERT INTO sqlar (item_id, data) VALUES (?, ?)", (1, b"bla"))
    io.connection.commit()
    del io

    io = SQLiteIO(tmpfile, create_new=False)
    result = io.fetchone("PRAGMA user_version")
    assert result[0] == schema.USER_VERSION
    result = io.fetchone(
        "SELECT x, y, items.data, sqlar.data FROM items "
        "LEFT OUTER JOIN sqlar on sqlar.item_id = items.id"
    )
    assert result[0] == 22.2
    assert result[1] == 33.3
    assert json.loads(result[2]) == {"filename": "bee.png"}
    assert result[3] == b"bla"


def test_sqliteio_write_meta_application_id(tmpfile):
    io = SQLiteIO(tmpfile, create_new=True)
    io.write_meta()
    result = io.fetchone("PRAGMA application_id")
    assert result[0] == schema.APPLICATION_ID


def test_sqliteio_write_meta_user_version(tmpfile):
    io = SQLiteIO(tmpfile, create_new=True)
    io.write_meta()
    result = io.fetchone("PRAGMA user_version")
    assert result[0] == schema.USER_VERSION


def test_sqliteio_write_meta_foreign_keys(tmpfile):
    io = SQLiteIO(tmpfile, create_new=True)
    io.write_meta()
    result = io.fetchone("PRAGMA foreign_keys")
    assert result[0] == 1


def test_sqliteio_create_schema_on_new_when_create_new(tmpfile):
    io = SQLiteIO(tmpfile, create_new=True)
    io.create_schema_on_new()
    result = io.fetchone(
        "SELECT COUNT(*) FROM sqlite_master "
        'WHERE type="table" AND name NOT LIKE "sqlite_%"'
    )
    assert result[0] == 2


@patch("beeref.fileio.sql.SQLiteIO._migrate")
def test_sqliteio_create_schema_on_new_when_not_create_new(migrate_mock, tmpfile):
    io = SQLiteIO(tmpfile, create_new=False)
    io.create_schema_on_new()
    result = io.fetchone(
        "SELECT COUNT(*) FROM sqlite_master "
        'WHERE type="table" AND name NOT LIKE "sqlite_%"'
    )
    assert result[0] == 0


def test_sqliteio_readonly_doesnt_allow_write(view, tmpfile):
    with open(tmpfile, "w") as f:
        f.write("foobar")
    io = SQLiteIO(tmpfile, readonly=True)

    with pytest.raises(BeeFileIOError) as exinfo:
        io.write(view.scene.snapshot_for_save())

    assert exinfo.value.filename == tmpfile
    with open(tmpfile, "r") as f:
        f.read() == "foobar"


def test_sqliteio_write_calls_create_schema_on_new(tmpfile, view):
    io = SQLiteIO(tmpfile, create_new=True)
    with patch.object(io, "create_schema_on_new") as crmock:
        with patch.object(io, "fetchall"):
            with patch.object(io, "exmany"):
                io.write(view.scene.snapshot_for_save())
                crmock.assert_called_once()


def test_sqliteio_write_calls_write_meta(tmpfile, view):
    io = SQLiteIO(tmpfile, create_new=True)
    with patch.object(io, "write_meta") as metamock:
        with patch.object(io, "fetchall"):
            with patch.object(io, "exmany"):
                io.write(view.scene.snapshot_for_save())
                metamock.assert_called_once()


def test_sqliteio_write_inserts_new_text_item(tmpfile, view):
    item = BeeTextItem(text="foo bar")
    view.scene.addItem(item)
    item.setScale(1.3)
    item.setPos(44, 55)
    item.setZValue(0.22)
    item.setRotation(33)
    item.do_flip()
    io = SQLiteIO(tmpfile, create_new=True)
    io.write(view.scene.snapshot_for_save())

    assert io.fetchone("SELECT id FROM items WHERE id = ?", (item.save_id,))
    result = io.fetchone(
        "SELECT x, y, z, scale, rotation, flip, items.data, type, "
        "sqlar.data, sqlar.name "
        "FROM items "
        "LEFT OUTER JOIN sqlar on sqlar.item_id = items.id"
    )
    assert result[0] == 44.0
    assert result[1] == 55.0
    assert result[2] == 0.22
    assert result[3] == 1.3
    assert result[4] == 33
    assert result[5] == -1
    assert json.loads(result[6]) == {"text": "foo bar"}
    assert result[7] == "text"
    assert result[8] is None
    assert result[9] is None


def test_sqliteio_write_inserts_new_pixmap_item_png(tmpfile, view):
    item = BeePixmapItem(QtGui.QImage(), filename="bee.jpg")
    view.scene.addItem(item)
    item.setOpacity(0.66)
    item.setScale(1.3)
    item.setPos(44, 55)
    item.setZValue(0.22)
    item.setRotation(33)
    item.do_flip()
    item.crop = QtCore.QRectF(5, 5, 100, 80)
    item.pixmap_to_bytes = MagicMock(return_value=(b"abc", "png"))
    io = SQLiteIO(tmpfile, create_new=True)
    io.write(view.scene.snapshot_for_save())

    assert io.fetchone("SELECT id FROM items WHERE id = ?", (item.save_id,))
    result = io.fetchone(
        "SELECT x, y, z, scale, rotation, flip, items.data, type, "
        "sqlar.data, sqlar.name "
        "FROM items "
        "INNER JOIN sqlar on sqlar.item_id = items.id"
    )
    assert result[0] == 44.0
    assert result[1] == 55.0
    assert result[2] == 0.22
    assert result[3] == 1.3
    assert result[4] == 33
    assert result[5] == -1
    assert json.loads(result[6]) == {
        "filename": "bee.jpg",
        "crop": [5, 5, 100, 80],
        "opacity": 0.66,
    }
    assert result[7] == "pixmap"
    assert result[8] == b"abc"
    assert result[9] == f"{item.save_id[:8]}-bee.png"


def test_sqliteio_write_inserts_new_pixmap_item_jpg(tmpfile, view, imgfilename3x3):
    item = BeePixmapItem(QtGui.QImage(imgfilename3x3), filename="bee.jpg")
    view.scene.addItem(item)
    with patch.object(item, "get_imgformat", return_value="jpg"):
        io = SQLiteIO(tmpfile, create_new=True)
        io.write(view.scene.snapshot_for_save())

    assert io.fetchone("SELECT id FROM items WHERE id = ?", (item.save_id,))
    result = io.fetchone(
        "SELECT type, sqlar.data, sqlar.name "
        "FROM items "
        "INNER JOIN sqlar on sqlar.item_id = items.id"
    )
    assert result[0] == "pixmap"
    assert result[1].startswith(b"\xff\xd8\xff")  # JPEG magic bytes
    assert result[2] == f"{item.save_id[:8]}-bee.jpg"


def test_sqliteio_write_inserts_new_pixmap_item_without_filename(tmpfile, view, item):
    view.scene.addItem(item)
    io = SQLiteIO(tmpfile, create_new=True)
    io.write(view.scene.snapshot_for_save())

    assert io.fetchone("SELECT id FROM items WHERE id = ?", (item.save_id,))
    result = io.fetchone(
        "SELECT items.data, sqlar.name FROM items "
        "INNER JOIN sqlar on sqlar.item_id = items.id"
    )
    assert json.loads(result[0])["filename"] is None
    assert result[1] == f"{item.save_id[:8]}.png"


def test_sqliteio_write_updates_existing_text_item(tmpfile, view):
    item = BeeTextItem(text="foo bar")
    view.scene.addItem(item)
    item.setScale(1.3)
    item.setPos(44, 55)
    item.setZValue(0.22)
    item.setRotation(33)
    io = SQLiteIO(tmpfile, create_new=True)
    io.write(view.scene.snapshot_for_save())
    assert io.fetchone("SELECT COUNT(*) from items") == (1,)

    item.setScale(0.7)
    item.setPos(20, 30)
    item.setZValue(0.33)
    item.setRotation(100)
    item.do_flip()
    item.set_markdown("updated")
    io.create_new = False
    io.write(view.scene.snapshot_for_save())

    assert io.fetchone("SELECT COUNT(*) from items") == (1,)
    result = io.fetchone(
        "SELECT x, y, z, scale, rotation, flip, items.data, sqlar.data "
        "FROM items "
        "LEFT OUTER JOIN sqlar on sqlar.item_id = items.id"
    )
    assert result[0] == 20
    assert result[1] == 30
    assert result[2] == 0.33
    assert result[3] == 0.7
    assert result[4] == 100
    assert result[5] == -1
    assert json.loads(result[6]) == {"text": "updated"}
    assert result[7] is None


def test_sqliteio_write_updates_existing_pixmap_item(tmpfile, view, imgfilename3x3):
    item = BeePixmapItem(QtGui.QImage(imgfilename3x3), filename="bee.png")
    view.scene.addItem(item)
    item.setScale(1.3)
    item.setPos(44, 55)
    item.setZValue(0.22)
    item.setRotation(33)
    item.setOpacity(0.2)
    item.crop = QtCore.QRectF(5, 5, 80, 100)
    io = SQLiteIO(tmpfile, create_new=True)
    io.write(view.scene.snapshot_for_save())
    assert io.fetchone("SELECT COUNT(*) from items") == (1,)

    # Mark as saved so second write only updates metadata
    item._blob_saved = True
    item.setScale(0.7)
    item.setPos(20, 30)
    item.setZValue(0.33)
    item.setRotation(100)
    item.setOpacity(0.75)
    item.do_flip()
    item.crop = QtCore.QRectF(1, 2, 30, 40)
    item.filename = "new.png"
    io.create_new = False
    io.write(view.scene.snapshot_for_save())

    assert io.fetchone("SELECT COUNT(*) from items") == (1,)
    result = io.fetchone(
        "SELECT x, y, z, scale, rotation, flip, items.data, sqlar.data "
        "FROM items "
        "INNER JOIN sqlar on sqlar.item_id = items.id"
    )
    assert result[0] == 20
    assert result[1] == 30
    assert result[2] == 0.33
    assert result[3] == 0.7
    assert result[4] == 100
    assert result[5] == -1
    assert json.loads(result[6]) == {
        "filename": "new.png",
        "crop": [1, 2, 30, 40],
        "opacity": 0.75,
    }
    # Blob unchanged from first write
    assert result[7].startswith(b"\x89PNG")


def test_sqliteio_write_keeps_pixmap_item_of_error_item(tmpfile, view, imgfilename3x3):
    item = BeePixmapItem(QtGui.QImage(imgfilename3x3), filename="bee.png")
    view.scene.addItem(item)
    item.setScale(1.3)
    item.setPos(44, 55)
    item.setZValue(0.22)
    item.setRotation(33)
    item.setOpacity(0.2)
    item.crop = QtCore.QRectF(5, 5, 80, 100)
    io = SQLiteIO(tmpfile, create_new=True)
    io.write(view.scene.snapshot_for_save())
    saved_id = item.save_id
    view.scene.removeItem(item)
    assert io.fetchone("SELECT COUNT(*) from items") == (1,)

    err_item = BeeErrorItem("errormsg")
    err_item.save_id = saved_id
    err_item.setScale(0.7)
    err_item.setPos(20, 30)
    err_item.setZValue(0.33)
    err_item.setRotation(100)
    view.scene.addItem(err_item)
    io.create_new = False
    io.write(view.scene.snapshot_for_save())

    assert io.fetchone("SELECT COUNT(*) from items") == (1,)
    result = io.fetchone(
        "SELECT x, y, z, scale, rotation, flip, items.data, sqlar.data "
        "FROM items "
        "INNER JOIN sqlar on sqlar.item_id = items.id"
    )
    assert result[0] == 44
    assert result[1] == 55
    assert result[2] == 0.22
    assert result[3] == 1.3
    assert result[4] == 33
    assert result[5] == 1
    assert json.loads(result[6]) == {
        "filename": "bee.png",
        "crop": [5, 5, 80, 100],
        "opacity": 0.2,
    }
    assert result[7].startswith(b"\x89PNG")


def test_sqliteio_doesnt_write_error_item_to_new_file(tmpfile, view):
    err_item = BeeErrorItem("errormsg")
    err_item.save_id = "a" * 32
    view.scene.addItem(err_item)
    io = SQLiteIO(tmpfile, create_new=True)
    io.create_new = True
    io.write(view.scene.snapshot_for_save())
    assert io.fetchone("SELECT COUNT(*) from items") == (0,)


def test_sqliteio_write_removes_nonexisting_text_item(tmpfile, view):
    item = BeeTextItem("foo bar")
    item.setScale(1.3)
    item.setPos(44, 55)
    view.scene.addItem(item)
    io = SQLiteIO(tmpfile, create_new=True)
    io.write(view.scene.snapshot_for_save())

    view.scene.removeItem(item)
    io.create_new = False
    io.write(view.scene.snapshot_for_save())

    assert io.fetchone("SELECT COUNT(*) from items") == (0,)
    assert io.fetchone("SELECT COUNT(*) from sqlar") == (0,)


def test_sqliteio_write_removes_nonexisting_pixmap_item(tmpfile, view, imgfilename3x3):
    item = BeePixmapItem(QtGui.QImage(imgfilename3x3), filename="bee.png")
    item.setScale(1.3)
    item.setPos(44, 55)
    view.scene.addItem(item)
    io = SQLiteIO(tmpfile, create_new=True)
    io.write(view.scene.snapshot_for_save())
    assert io.fetchone("SELECT COUNT(*) from items") == (1,)
    assert io.fetchone("SELECT COUNT(*) from sqlar") == (1,)

    view.scene.removeItem(item)

    io = SQLiteIO(tmpfile, create_new=False)
    io.create_new = False
    io.write(view.scene.snapshot_for_save())

    assert io.fetchone("SELECT COUNT(*) from items") == (0,)
    assert io.fetchone("SELECT COUNT(*) from sqlar") == (0,)


def test_sqliteio_write_update_recovers_from_borked_file(view, tmpfile):
    item = BeePixmapItem(QtGui.QImage(), filename="bee.png")
    view.scene.addItem(item)

    with open(tmpfile, "w") as f:
        f.write("foobar")

    io = SQLiteIO(tmpfile, create_new=False)
    io.write(view.scene.snapshot_for_save())
    result = io.fetchone("SELECT COUNT(*) FROM items")
    assert result[0] == 1


def test_sqliteio_write_update_recovers_from_nonexisting_file(view, tmpfile):
    item = BeePixmapItem(QtGui.QImage(), filename="bee.png")
    view.scene.addItem(item)
    io = SQLiteIO(tmpfile, create_new=False)
    io.write(view.scene.snapshot_for_save())
    result = io.fetchone("SELECT COUNT(*) FROM items")
    assert result[0] == 1


def test_sqliteio_write_updates_progress(tmpfile, view):
    worker = MagicMock(canceled=False)
    io = SQLiteIO(tmpfile, create_new=True, worker=worker)
    item = BeePixmapItem(QtGui.QImage())
    view.scene.addItem(item)
    io.write(view.scene.snapshot_for_save())
    worker.begin_processing.emit.assert_called_once_with(1)
    worker.progress.emit.assert_called_once_with(0)


def test_sqliteio_write_canceled(tmpfile, view):
    worker = MagicMock(canceled=True)
    io = SQLiteIO(tmpfile, create_new=True, worker=worker)
    item = BeePixmapItem(QtGui.QImage())
    view.scene.addItem(item)
    item = BeePixmapItem(QtGui.QImage())
    view.scene.addItem(item)
    io.write(view.scene.snapshot_for_save())
    worker.begin_processing.emit.assert_called_once_with(2)
    worker.progress.emit.assert_called_once_with(0)


def test_sqliteio_read_reads_readonly_text_item(tmpfile, view):
    io = SQLiteIO(tmpfile, create_new=True)
    io.create_schema_on_new()
    text_id = "a" * 32
    io.ex(
        "INSERT INTO items "
        "(id, type, x, y, z, scale, rotation, flip, data) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ",
        (
            text_id,
            "text",
            22.2,
            33.3,
            0.22,
            3.4,
            45,
            -1,
            json.dumps({"text": "foo bar"}),
        ),
    )
    io.connection.commit()
    del io

    io = SQLiteIO(tmpfile, readonly=True)
    snapshots = io.read()
    assert len(snapshots) == 1
    snap = snapshots[0]
    assert isinstance(snap, ItemSnapshot)
    assert snap.save_id == text_id
    assert snap.x == 22.2
    assert snap.y == 33.3
    assert snap.z == 0.22
    assert snap.scale == 3.4
    assert snap.rotation == 45
    assert snap.flip == -1
    assert snap.data["text"] == "foo bar"


def test_sqliteio_read_reads_readonly_pixmap_item(tmpfile, view, imgdata3x3):
    io = SQLiteIO(tmpfile, create_new=True)
    io.create_schema_on_new()
    pixmap_id = "b" * 32
    io.ex(
        "INSERT INTO items "
        "(id, type, x, y, z, scale, rotation, flip, data) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ",
        (
            pixmap_id,
            "pixmap",
            22.2,
            33.3,
            0.22,
            3.4,
            45,
            -1,
            json.dumps({"filename": "bee.png"}),
        ),
    )
    io.ex("INSERT INTO sqlar (item_id, data) VALUES (?, ?)", (pixmap_id, imgdata3x3))
    io.connection.commit()
    del io

    io = SQLiteIO(tmpfile, readonly=True)
    snapshots = io.read()
    assert len(snapshots) == 1
    snap = snapshots[0]
    assert isinstance(snap, PixmapItemSnapshot)
    assert snap.save_id == pixmap_id
    assert snap.x == 22.2
    assert snap.y == 33.3
    assert snap.z == 0.22
    assert snap.scale == 3.4
    assert snap.rotation == 45
    assert snap.flip == -1
    assert snap.data["filename"] == "bee.png"
    assert snap.pixmap_bytes is not None
    assert len(snap.pixmap_bytes) > 0


def test_sqliteio_read_reads_readonly_pixmap_item_error(tmpfile, view):
    io = SQLiteIO(tmpfile, create_new=True)
    io.create_schema_on_new()
    err_id = "c" * 32
    io.ex(
        "INSERT INTO items "
        "(id, type, x, y, z, scale, rotation, flip, data) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ",
        (
            err_id,
            "pixmap",
            22.2,
            33.3,
            0.22,
            3.4,
            45,
            -1,
            json.dumps({"filename": "bee.png"}),
        ),
    )
    io.ex("INSERT INTO sqlar (item_id, data) VALUES (?, ?)", (err_id, b"not an image"))
    io.connection.commit()
    del io

    io = SQLiteIO(tmpfile, readonly=True)
    snapshots = io.read()
    assert len(snapshots) == 1
    snap = snapshots[0]
    assert isinstance(snap, PixmapItemSnapshot)
    assert snap.pixmap_bytes == b"not an image"

    # Corrupt blob should produce an error item
    item = create_item_from_snapshot(snap)
    assert isinstance(item, BeeErrorItem)
    assert item.save_id == err_id


def test_sqliteio_read_updates_progress(tmpfile, view):
    worker = MagicMock(canceled=False)
    io = SQLiteIO(tmpfile, create_new=True, worker=worker)

    io.create_schema_on_new()
    prog_id = "d" * 32
    io.ex(
        "INSERT INTO items (id, type, x, y, z, scale, data) VALUES (?, ?, ?, ?, ?, ?, ?) ",
        (prog_id, "pixmap", 0, 0, 0, 1, json.dumps({"filename": "bee.png"})),
    )
    io.ex("INSERT INTO sqlar (item_id, data) VALUES (?, ?)", (prog_id, b""))
    io.connection.commit()

    snapshots = io.read()
    assert len(snapshots) == 1
    worker.begin_processing.emit.assert_called_once_with(1)
    worker.progress.emit.assert_called_once_with(0)


def test_sqliteio_read_canceled(tmpfile, view):
    worker = MagicMock(canceled=True)
    io = SQLiteIO(tmpfile, create_new=True, worker=worker)
    io.create_schema_on_new()
    cancel_id1 = "e" * 32
    cancel_id2 = "f" * 32
    io.ex(
        "INSERT INTO items (id, type, x, y, z, scale, data) VALUES (?, ?, ?, ?, ?, ?, ?) ",
        (cancel_id1, "pixmap", 0, 0, 0, 1, json.dumps({"filename": "bee.png"})),
    )
    io.ex("INSERT INTO sqlar (item_id, data) VALUES (?, ?)", (cancel_id1, b""))
    io.ex(
        "INSERT INTO items (id, type, x, y, z, scale, data) VALUES (?, ?, ?, ?, ?, ?, ?) ",
        (cancel_id2, "pixmap", 50, 50, 0, 1, json.dumps({"filename": "bee2.png"})),
    )
    io.ex("INSERT INTO sqlar (item_id, data) VALUES (?, ?)", (cancel_id2, b""))
    io.connection.commit()

    snapshots = io.read()
    # Canceled after first item, so only one snapshot
    assert len(snapshots) == 1
    worker.begin_processing.emit.assert_called_once_with(2)
    worker.progress.emit.assert_called_once_with(0)


def test_sqliteio_read_raises_error_when_file_borked(view, tmpfile):
    with open(tmpfile, "w") as f:
        f.write("foobar")

    io = SQLiteIO(tmpfile, readonly=True)
    with pytest.raises(BeeFileIOError) as exinfo:
        io.read()
    assert exinfo.value.filename == tmpfile


def test_sqliteio_read_raises_error_when_file_borked_with_worker(tmpfile):
    with open(tmpfile, "w") as f:
        f.write("foobar")

    worker = MagicMock()
    io = SQLiteIO(tmpfile, readonly=True, worker=worker)
    with pytest.raises(BeeFileIOError) as exinfo:
        io.read()
    assert exinfo.value.filename == tmpfile


def test_sqliteio_read_raises_error_when_file_empty(view, tmpfile):
    io = SQLiteIO(tmpfile, readonly=True)
    with pytest.raises(BeeFileIOError) as exinfo:
        io.read()
    assert exinfo.value.filename == tmpfile

    # should not create a file on reading!
    assert os.path.isfile(tmpfile) is False
