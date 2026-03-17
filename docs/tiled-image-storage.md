# Tiled Image Storage

## Context

ZeeRef stores each image as a single blob in `sqlar`. This doesn't scale for very large images (30k x 30k) — the entire blob must be decoded to display any portion. Tiling breaks images into a grid of small chunks with pre-computed mip levels, so only visible tiles at the appropriate zoom level need to be loaded.

This design unifies small and large images under one storage scheme: every image is tiles, small ones are just one tile.

## Schema

```sql
CREATE TABLE tiles (
    item_id INTEGER NOT NULL,
    level INTEGER NOT NULL,     -- 0 = full res, 1 = 1/2, 2 = 1/4...
    col INTEGER NOT NULL,
    row INTEGER NOT NULL,
    data BLOB,                  -- small JPEG/PNG (e.g., 512x512)
    PRIMARY KEY (item_id, level, col, row),
    FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
)
```

`sqlar` is dropped (or kept read-only for migration/extraction compatibility).

Grid dimensions are derived from the item's `width`/`height` (already in the `items` table) and the tile size constant — no extra metadata needed:

```python
TILE_SIZE = 512
# At level N:
cols = ceil((width >> N) / TILE_SIZE)
rows = ceil((height >> N) / TILE_SIZE)
```

## What gets stored

**Small image** (800x600): one row in `tiles` — `(item_id, level=0, col=0, row=0, data=<full JPEG>)`. No pyramid needed.

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
    WHERE item_id=? AND level=?
      AND col BETWEEN ? AND ?
      AND row BETWEEN ? AND ?
```

For small images this always returns one tile at `(0, 0, 0)`.

## Integration with async loading

The `ImageLoader` thread becomes a tile loader:

```
Viewport changes
  → compute visible items
  → for each visible item:
      compute needed (level, col, row) tuples
      subtract already-loaded tiles
      request missing tiles from ImageLoader
  → ImageLoader fetches tile blobs from SQLite
  → emits (item_id, level, col, row, pil_tile)
  → main thread converts to QPixmap, composites into display
```

Placeholder → loaded is now granular: items transition tile-by-tile. At zoom-out you might only ever load level 5 (one tile). Zoom in and individual full-res tiles load on demand.

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

For a small image with one tile, this is one `drawPixmap` call — same as today.

## Import (tiling at ingest)

When a new image is added:

```python
pil_img = Image.open(path)
level = 0
while True:
    w, h = pil_img.size
    for row in range(ceil(h / TILE_SIZE)):
        for col in range(ceil(w / TILE_SIZE)):
            box = (col*TILE_SIZE, row*TILE_SIZE,
                   min((col+1)*TILE_SIZE, w),
                   min((row+1)*TILE_SIZE, h))
            tile = pil_img.crop(box)
            write_tile(item_id, level, col, row, tile_to_jpeg(tile))
    if max(w, h) <= TILE_SIZE:
        break
    pil_img = pil_img.resize((w//2, h//2), LANCZOS)
    level += 1
```

Small images: one iteration, one tile. Large images: full pyramid.

## What this replaces

- `sqlar` table → `tiles` table
- In-memory mip chain (`_mip_chain` on ZeePixmapItem) → tiles loaded on demand from DB
- `_generate_mips()` at load time → mips pre-stored at import/save time
- Single-blob `pixmap_from_bytes` → tile-based loading

## Migration

Schema migration from sqlar to tiles:

1. For each `sqlar` row: read blob, tile it (most images → one tile at level 0), write to `tiles`
2. Optionally generate mip levels for existing images during migration
3. Drop `sqlar` (or keep for `sqlite3 -Ax` extraction compatibility)

## Implementation order

This builds on the scratch file and async loading work:

1. **Placeholder + async loading** (`async-image-loading.md`) — viewport-driven blob loading
2. **Scratch file** (`scratch-file-architecture.md`) — working copy, drain, crash recovery
3. **Tiled storage** (this doc) — replaces sqlar, unifies mips, enables large images
