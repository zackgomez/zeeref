# Tiled Image Storage

## Context

ZeeRef stores each image as a single blob in `sqlar`. This doesn't scale for very large images (30k x 30k) — the entire blob must be decoded to display any portion. Tiling breaks images into a grid of small chunks with pre-computed mip levels, so only visible tiles at the appropriate zoom level need to be loaded.

This design unifies small and large images under one storage scheme: every image is tiles, small ones are just one tile.

## Schema (v5)

Single migration from v4. Introduces `images` (metadata) and `tiles` (blob storage), drops `sqlar`. Items reference images via FK. Width/height move from `items` to `images` (where they belong — intrinsic image dimensions, not item properties). Initially every image is stored as a single tile at `(level=0, col=0, row=0)` — actual tiling comes later as a code change, not a schema change.

```sql
CREATE TABLE images (
    id TEXT PRIMARY KEY,        -- UUID4 hex
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    format TEXT NOT NULL         -- 'png' or 'jpeg'
)

CREATE TABLE tiles (
    image_id TEXT NOT NULL,
    level INTEGER NOT NULL,     -- 0 = full res, 1 = 1/2, 2 = 1/4...
    col INTEGER NOT NULL,
    row INTEGER NOT NULL,
    data BLOB NOT NULL,
    PRIMARY KEY (image_id, level, col, row),
    FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE
)

-- items gains image_id FK, loses width/height (moved to images)
ALTER TABLE items ADD COLUMN image_id TEXT REFERENCES images(id)
ALTER TABLE items DROP COLUMN width
ALTER TABLE items DROP COLUMN height
```

`sqlar` is dropped.

**v5 migration steps:**
1. Create `images` and `tiles` tables
2. Add `image_id` column to `items`
3. For each `sqlar` row: generate UUID, read width/height/format from Pillow, INSERT into `images`, insert single tile at `(uuid, 0, 0, 0, blob)`, UPDATE the item's `image_id`
4. Drop `sqlar`
5. Drop `width` and `height` columns from `items`

Each sqlar row gets its own `images` row. Dedup (merging identical blobs) can be added later with a content hash column on `images`.

**Snapshot changes:**
- `PixmapItemSnapshot` gains `image_id: str` (UUID4 hex)
- `PixmapItemSnapshot.width`/`height` now come from `images` table (JOIN), not `items`
- `pixmap_bytes` semantics stay the same (`None` when blob already in DB)
- `SQLiteIO.read()` JOINs `items` → `images` → `tiles` (single tile per image initially)
- `SQLiteIO.write()` inserts into `images` + `tiles`

## What gets stored (initially)

Every image is one tile at level 0. The schema supports multi-tile pyramids but the code doesn't generate them yet.

**Small image** (800x600): `images` row + one `tiles` row at `(uuid, 0, 0, 0)`.

**Large image** (30000x30000): same — one `tiles` row. Pyramid generation is a future code change.

## What gets stored (with tiling enabled, future)

**Large image** (30000x30000):

| Level | Resolution | Grid | Tiles |
|-------|-----------|------|-------|
| 0 | 30000x30000 | 59x59 | 3481 |
| 1 | 15000x15000 | 30x30 | 900 |
| 2 | 7500x7500 | 15x15 | 225 |
| 3 | 3750x3750 | 8x8 | 64 |
| 4 | 1875x1875 | 4x4 | 16 |
| 5 | 937x937 | 2x2 | 4 |
| 6 | 468x468 | 1x1 | 1 |

At any given time, only tiles intersecting the viewport at the current zoom level are loaded — typically 6-12 tiles.

Grid dimensions derived from `images.width`/`images.height`:

```python
TILE_SIZE = 512
# At level N:
cols = ceil((width >> N) / TILE_SIZE)
rows = ceil((height >> N) / TILE_SIZE)
```

## Loading path

Single code path for all images:

```python
def load_visible_tiles(item, viewport_rect, view_scale):
    # Pick mip level closest to (but not below) display resolution
    level = max(0, floor(-log2(view_scale)))
    level = min(level, item.max_level)

    # Convert viewport rect to tile coords at this level
    scale = 1 / (1 << level)
    col_min = floor(viewport_rect.left() * scale / TILE_SIZE)
    col_max = floor(viewport_rect.right() * scale / TILE_SIZE)
    row_min = floor(viewport_rect.top() * scale / TILE_SIZE)
    row_max = floor(viewport_rect.bottom() * scale / TILE_SIZE)

    # Fetch only needed tiles
    SELECT data, col, row FROM tiles
    WHERE image_id=? AND level=?
      AND col BETWEEN ? AND ?
      AND row BETWEEN ? AND ?
```

For single-tile images (all images initially) this always returns one tile at `(0, 0, 0)`.

## Integration with async loading

The `ImageLoader` thread becomes a tile loader:

```
Viewport changes
  → compute visible items
  → for each visible item:
      compute needed (image_id, level, col, row) tuples
      subtract already-loaded tiles
      request missing tiles from ImageLoader
  → ImageLoader fetches tile blobs from SQLite
  → emits (image_id, level, col, row, pil_tile)
  → main thread converts to QPixmap, composites into display
```

Placeholder → loaded is now granular: items transition tile-by-tile. At zoom-out you might only ever load level 5 (one tile). Zoom in and individual full-res tiles load on demand.

Because tiles are keyed by `image_id` not `item_id`, duplicate items sharing the same image share loaded tiles in memory too.

## Paint

```python
def paint(self, painter, option, widget):
    level = pick_level(effective_scale)
    scale = 1 / (1 << level)

    for (col, row), tile_pixmap in self._loaded_tiles[level].items():
        x = col * TILE_SIZE / scale
        y = row * TILE_SIZE / scale
        size = TILE_SIZE / scale
        painter.drawPixmap(QRectF(x, y, size, size), tile_pixmap,
                           QRectF(0, 0, TILE_SIZE, TILE_SIZE))
```

For a single-tile image, this is one `drawPixmap` call — same as today.

## Import (tiling at ingest)

### Initial (single tile)

```python
pil_img = Image.open(path)
image_id = uuid4().hex

insert_image(image_id, width, height, format)
insert_tile(image_id, level=0, col=0, row=0, data=encode(pil_img))
item.image_id = image_id
```

### Future (pyramid tiling)

```python
pil_img = Image.open(path)
image_id = uuid4().hex

insert_image(image_id, width, height, format)
level = 0
while True:
    w, h = pil_img.size
    for row in range(ceil(h / TILE_SIZE)):
        for col in range(ceil(w / TILE_SIZE)):
            box = (col*TILE_SIZE, row*TILE_SIZE,
                   min((col+1)*TILE_SIZE, w),
                   min((row+1)*TILE_SIZE, h))
            tile = pil_img.crop(box)
            insert_tile(image_id, level, col, row, tile_to_jpeg(tile))
    if max(w, h) <= TILE_SIZE:
        break
    pil_img = pil_img.resize((w//2, h//2), LANCZOS)
    level += 1
```

Small images: one iteration, one tile. Large images: full pyramid.

## What this replaces

- `sqlar` table → `images` + `tiles` tables
- In-memory mip chain (`_mip_chain` on ZeePixmapItem) → tiles loaded on demand from DB (future)
- `_generate_mips()` at load time → mips pre-stored at import/save time (future)
- Single-blob `pixmap_from_bytes` → tile-based loading (future)
- Per-item blob ownership → shared images (dedup-ready with future hash column)

## Orphan cleanup

When items are deleted and the file is compacted (during Save), images with no remaining items referencing them should be cleaned up. Tiles cascade automatically via FK.

```sql
DELETE FROM images WHERE id NOT IN (SELECT image_id FROM items WHERE image_id IS NOT NULL)
```

This runs in the compact step of `save_bee()` alongside the existing stale item deletion, before VACUUM.

During drain (append-only .swp writes), orphaned images accumulate — this is by design, same as stale item rows. Cleanup only happens on Save.

## Migration (v4 → v5)

1. Create `images` and `tiles` tables
2. Add `image_id` column to `items`
3. For each sqlar row: generate UUID, read width/height/format via Pillow, INSERT into `images`, insert tile at `(uuid, 0, 0, 0, blob)`, UPDATE item's `image_id`
4. Drop `sqlar`
5. Rebuild `items` table without `width`/`height` columns (SQLite doesn't support DROP COLUMN before 3.35, so use the create-new/copy/rename pattern like the v4 migration)

## Implementation order

1. **Scratch file** (`scratch-file-architecture.md`) — working copy, drain, crash recovery ✅
2. **v5 schema migration** — `images` + `tiles` tables, decouple items from blobs (next)
3. **Placeholder + async loading** (`async-image-loading.md`) — viewport-driven blob/tile loading
4. **Pyramid tiling** — generate multi-level tile pyramids at ingest for large images
