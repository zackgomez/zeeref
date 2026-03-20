import time
import uuid
from io import BytesIO

from PIL import Image

USER_VERSION = 6
APPLICATION_ID = 2060242126


SCHEMA = [
    """
    CREATE TABLE images (
        id TEXT PRIMARY KEY,
        width INTEGER NOT NULL,
        height INTEGER NOT NULL,
        format TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE tiles (
        image_id TEXT NOT NULL,
        level INTEGER NOT NULL,
        col INTEGER NOT NULL,
        row INTEGER NOT NULL,
        data BLOB NOT NULL,
        PRIMARY KEY (image_id, level, col, row),
        FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE items (
        id TEXT PRIMARY KEY,
        type TEXT NOT NULL,
        x REAL DEFAULT 0,
        y REAL DEFAULT 0,
        z REAL DEFAULT 0,
        scale REAL DEFAULT 1,
        rotation REAL DEFAULT 0,
        flip INTEGER DEFAULT 1,
        data JSON,
        image_id TEXT,
        created_at REAL,
        FOREIGN KEY (image_id) REFERENCES images(id)
    )
    """,
]


def _populate_image_dimensions(io):
    """Read image headers from sqlar blobs to populate width/height."""
    rows = io.fetchall("SELECT item_id, data FROM sqlar")
    for item_id, blob in rows:
        try:
            img = Image.open(BytesIO(blob))
            w, h = img.size
            io.ex("UPDATE items SET width=?, height=? WHERE id=?", (w, h, item_id))
        except Exception:
            pass


def _migrate_to_uuid_ids(io):
    """Migrate integer IDs to UUID text IDs with created_at timestamps."""
    io.ex(
        """CREATE TABLE items_new (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            x REAL DEFAULT 0,
            y REAL DEFAULT 0,
            z REAL DEFAULT 0,
            scale REAL DEFAULT 1,
            rotation REAL DEFAULT 0,
            flip INTEGER DEFAULT 1,
            data JSON,
            width INTEGER,
            height INTEGER,
            created_at REAL
        )"""
    )
    io.ex(
        """CREATE TABLE sqlar_new (
            name TEXT PRIMARY KEY,
            item_id TEXT NOT NULL UNIQUE,
            mode INT,
            mtime INT default current_timestamp,
            sz INT,
            data BLOB,
            FOREIGN KEY (item_id)
              REFERENCES items_new (id)
                 ON DELETE CASCADE
                 ON UPDATE NO ACTION
        )"""
    )
    base_time = time.time()
    rows = io.fetchall(
        "SELECT id, type, x, y, z, scale, rotation, flip, data, width, height FROM items ORDER BY id"
    )
    for i, row in enumerate(rows):
        old_id = row[0]
        new_id = uuid.uuid4().hex
        created_at = base_time + i * 0.001  # preserve insertion order
        io.ex(
            "INSERT INTO items_new (id, type, x, y, z, scale, rotation, flip, data, width, height, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (new_id, *row[1:], created_at),
        )
        io.ex(
            "INSERT INTO sqlar_new SELECT name, ?, mode, mtime, sz, data FROM sqlar WHERE item_id = ?",
            (new_id, old_id),
        )
    io.ex("DROP TABLE sqlar")
    io.ex("DROP TABLE items")
    io.ex("ALTER TABLE items_new RENAME TO items")
    io.ex("ALTER TABLE sqlar_new RENAME TO sqlar")


def _migrate_to_images_and_tiles(io):
    """Replace sqlar with images + tiles tables, move width/height to images."""
    io.ex(
        """CREATE TABLE images (
            id TEXT PRIMARY KEY,
            width INTEGER NOT NULL,
            height INTEGER NOT NULL,
            format TEXT NOT NULL
        )"""
    )
    io.ex(
        """CREATE TABLE tiles (
            image_id TEXT NOT NULL,
            level INTEGER NOT NULL,
            col INTEGER NOT NULL,
            row INTEGER NOT NULL,
            data BLOB NOT NULL,
            PRIMARY KEY (image_id, level, col, row),
            FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE
        )"""
    )

    io.ex("ALTER TABLE items ADD COLUMN image_id TEXT")

    rows = io.fetchall(
        "SELECT sqlar.item_id, sqlar.data, sqlar.name, items.width, items.height "
        "FROM sqlar JOIN items ON sqlar.item_id = items.id"
    )
    for item_id, blob, name, width, height in rows:
        image_id = uuid.uuid4().hex
        # Determine format from sqlar name or Pillow
        fmt = "png"
        if name and name.lower().endswith((".jpg", ".jpeg")):
            fmt = "jpeg"
        elif blob:
            try:
                img = Image.open(BytesIO(blob))
                fmt = (img.format or "PNG").lower()
                if width is None or height is None:
                    width, height = img.size
            except Exception:
                pass
        io.ex(
            "INSERT INTO images (id, width, height, format) VALUES (?, ?, ?, ?)",
            (image_id, width or 0, height or 0, fmt),
        )
        io.ex(
            "INSERT INTO tiles (image_id, level, col, row, data) VALUES (?, 0, 0, 0, ?)",
            (image_id, blob),
        )
        io.ex("UPDATE items SET image_id=? WHERE id=?", (image_id, item_id))

    # Rebuild items table without width/height, with image_id
    io.ex(
        """CREATE TABLE items_new (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            x REAL DEFAULT 0,
            y REAL DEFAULT 0,
            z REAL DEFAULT 0,
            scale REAL DEFAULT 1,
            rotation REAL DEFAULT 0,
            flip INTEGER DEFAULT 1,
            data JSON,
            image_id TEXT,
            created_at REAL,
            FOREIGN KEY (image_id) REFERENCES images(id)
        )"""
    )
    io.ex(
        "INSERT INTO items_new (id, type, x, y, z, scale, rotation, flip, data, image_id, created_at) "
        "SELECT id, type, x, y, z, scale, rotation, flip, data, image_id, created_at FROM items"
    )
    io.ex("DROP TABLE sqlar")
    io.ex("DROP TABLE items")
    io.ex("ALTER TABLE items_new RENAME TO items")


def _migrate_to_tile_pyramids(io):
    """Generate tile pyramids for images stored as a single blob.

    v5 stores each image as one tile at (image_id, 0, 0, 0).
    v6 chops level 0 into 512x512 grid tiles and adds downsampled levels.
    """
    from zeeref.fileio.tiling import encode_tile, generate_tiles, pick_format

    rows = io.fetchall(
        "SELECT image_id, data FROM tiles WHERE level = 0 AND col = 0 AND row = 0"
    )
    for image_id, blob in rows:
        if blob is None:
            continue
        try:
            pil_img = Image.open(BytesIO(blob))
            pil_img.load()
        except Exception:
            continue
        fmt = pick_format(pil_img)
        # Update format in images table
        io.ex("UPDATE images SET format = ? WHERE id = ?", (fmt, image_id))
        # Delete the single legacy tile
        io.ex(
            "DELETE FROM tiles WHERE image_id = ?",
            (image_id,),
        )
        # Insert full pyramid
        for tile_pil, level, col, row in generate_tiles(pil_img):
            io.ex(
                "INSERT INTO tiles (image_id, level, col, row, data) "
                "VALUES (?, ?, ?, ?, ?)",
                (image_id, level, col, row, encode_tile(tile_pil, fmt)),
            )


MIGRATIONS = {
    2: [
        lambda io: io.ex("ALTER TABLE items ADD COLUMN data JSON"),
        lambda io: io.ex("UPDATE items SET data = json_object('filename', filename)"),
    ],
    3: [
        lambda io: io.ex("ALTER TABLE items ADD COLUMN width INTEGER"),
        lambda io: io.ex("ALTER TABLE items ADD COLUMN height INTEGER"),
        _populate_image_dimensions,
    ],
    4: [
        _migrate_to_uuid_ids,
    ],
    5: [
        _migrate_to_images_and_tiles,
    ],
    6: [
        _migrate_to_tile_pyramids,
    ],
}
