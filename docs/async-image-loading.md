# Placeholder / Async Image Loading

## Context

ZeeRef currently loads all image blobs eagerly on file open — a 50-image file must decode all 50 JPEGs + generate all mip chains before the user sees anything. This replaces that with: (1) instant metadata-only open with placeholder items, (2) viewport-driven async blob loading in the background.

## Current architecture (what exists today)

The load path is snapshot-based:

1. `view.open_from_file(filename)` spawns `ThreadedIO(fileio.load_bee, ...)`
2. `load_bee()` creates a scratch file (.swp), then `SQLiteIO(swp).read()` returns `list[ItemSnapshot]` — this reads **all rows including blob data** from sqlar
3. `on_loading_finished()` iterates snapshots, calls `create_item_from_snapshot(snap)` for each (which calls `ZeePixmapItem.from_snapshot()` — decodes blob, generates mips), adds items to scene

The single `self.worker` slot on the view serializes all background IO (load, save, drain). A drain timer fires every 60s, writing scene state to the .swp via `drain_bee()`.

Key types (in `zeeref/types/snapshot.py`):
- `ItemSnapshot` — base, has `save_id: str` (UUID4 hex), `type`, position, transform, `data: dict`, `created_at: float`
- `PixmapItemSnapshot(ItemSnapshot)` — adds `width`, `height`, `export_filename`, `pixmap_bytes: bytes | None`, `pixmap_format: str | None`
- `LoadResult(IOResult)` — has `snapshots: list[ItemSnapshot]`, `scratch_file: Path | None`

## Threading Model

Three threads:

1. **Main thread** (Qt GUI) — Creates QPixmaps, updates the scene, handles user input. Receives loaded PIL images from the image loader via signal/slot.
2. **ThreadedIO** (existing QThread in `zeeref/fileio/thread.py`, short-lived) — Runs `read_metadata()` to read the items table only. Returns metadata-only snapshots. Finishes quickly (no blobs to decode).
3. **ImageLoader** (new QThread, long-lived) — Starts after metadata load completes. Opens its own SQLite connection to the .swp (can't share across threads). Pulls save_ids from a queue, fetches blobs, decodes with Pillow, generates mip chain as PIL images, emits `item_loaded` signal back to main thread.

```
ThreadedIO          Main Thread              ImageLoader
    |                    |                        |
 read_metadata()        |                        |
 return snapshots       |                        |
 emit finished -------> create placeholder items  |
    (done)              fit_scene()               |
                        build placeholder dict    |
                        start loader ----------> opens own SQLite conn to .swp
                        check viewport --------> request_load(id1, id2...)
                        |                        fetch blob, PIL decode
                        |                        emit item_loaded ----+
                        _on_blob_loaded() <------+
                        create QPixmap
                        attach to item
                        check viewport again --> ...
```

## Files to modify

1. `zeeref/fileio/sql.py` — New `read_metadata()` method on `SQLiteIO`
2. `zeeref/items.py` — Placeholder state on `ZeePixmapItem`, paint, load transition
3. `zeeref/fileio/io.py` — `load_bee_metadata()` function, `ImageLoader` thread class
4. `zeeref/view.py` — Viewport observer, load triggering, action disabling, cleanup
5. `zeeref/scene.py` — Minor tweak for undo-delete re-registration

## Implementation

### 1. `sql.py` — Metadata-only read

New `read_metadata()` method on `SQLiteIO`: queries `SELECT ... FROM items` (no sqlar JOIN). Returns `list[ItemSnapshot]` where pixmap items are `PixmapItemSnapshot` with `pixmap_bytes=None` (blob not loaded). The existing `read()` method stays for other uses.

No `msleep(10)` — metadata is fast. Worker progress signals work the same way as `read()`.

### 2. `items.py` — ZeePixmapItem placeholder support

**`from_snapshot()` changes**: When `snap.pixmap_bytes is None`, create a placeholder instead of decoding. Set `_placeholder = True`, store `snap.width`/`snap.height` for bounding rect. Skip `_generate_mips()`. Set `_blob_saved = True` (blob is in the .swp). All other metadata (position, transform, crop, grayscale flag, etc.) is applied normally from the snapshot data dict.

**New `_placeholder: bool` attribute**: Default `False`. Guards throughout the class:

- **`paint()`**: When `_placeholder`, draw filled rect (gray, alpha ~30) + outline (gray, alpha ~80, cosmetic pen) + `paint_selectable`. Return early.
- **`bounding_rect_unselected()`**: When `_placeholder`, return `QRectF(0, 0, width, height)`. Crop path works since width/height come from the snapshot.
- **`has_selection_handles()`**: Return `False` when placeholder — disables scale/rotate/flip handles, keeps selection outline.
- **`snapshot()`**: Works as-is — `_blob_saved` is `True` so `pixmap_bytes` is `None`, metadata is all set.
- **Guards**: `sample_color_at()`, `__str__()`, `color_gamut` — return early/safe values for placeholders.

**New `load_pixmap_from_pil(pil_img, mip_pils)` method**: Main-thread method called when background load completes. Saves crop, calls `super().setPixmap()` (bypasses our override to avoid `reset_crop` + `_generate_mips`), builds mip chain from PIL mips via `_pil_to_qpixmap`, restores crop, clears `_placeholder`, applies deferred grayscale, calls `prepareGeometryChange()` + `update()`.

### 3. `fileio/io.py` — Loader infrastructure

**`load_bee_metadata(filename: Path, scene: ZeeGraphicsScene, worker: ThreadedIO | None = None) -> None`**: Like `load_bee()` but calls `io.read_metadata()` instead of `io.read()`. Same scratch file creation, same `LoadResult` emission. The .swp path is stored on `scene._scratch_file` as today.

**`ImageLoader(QtCore.QThread)`**: New class in `fileio/io.py`:
- Signal: `item_loaded = pyqtSignal(str, object, object)` — `(save_id, pil_img, mip_pils)`. Note: `save_id` is `str` (UUID hex), not `int`.
- Constructor takes `swp_path: Path` (reads from the .swp, not the original file)
- `request_load(save_id: str)`: thread-safe, deduplicating (set + Queue)
- `run()`: opens its own `SQLiteIO(swp_path, readonly=True)`. Loop reads from queue, for each: fetch blob from sqlar by `item_id`, `Image.open(BytesIO(blob))`, `.load()`, generate mip PIL chain (LANCZOS), emit `item_loaded`
- `stop()`: sets flag, puts sentinel on queue, waits for thread to finish

### 4. `view.py` — Viewport observer + orchestration

**`open_from_file()`**: Uses `load_bee_metadata` instead of `load_bee`. Everything else (progress dialog, worker setup) stays the same.

**`on_loading_finished()`**: After creating items from snapshots and calling `fit_scene()`, builds `_placeholder_items: dict[str, ZeePixmapItem]` (save_id -> item) from items where `_placeholder is True`. Starts `ImageLoader(scene._scratch_file)`, connects `item_loaded` -> `_on_blob_loaded`, calls `_check_viewport_and_load()`.

**`_check_viewport_and_load()`**: Gets viewport rect in scene coords, inflates by 50% margin, iterates `_placeholder_items`, calls `loader.request_load(save_id)` for items whose `sceneBoundingRect()` intersects the buffered rect. Called live — no debounce needed.

**Hooks**: Override `scrollContentsBy(dx, dy)`, append to `scale()` and `resizeEvent()` — all call `_check_viewport_and_load()`.

**`_on_blob_loaded(save_id, pil_img, mip_pils)`**: Pops from `_placeholder_items`, calls `item.load_pixmap_from_pil(...)`. When dict is empty, stops loader. Calls `on_selection_changed()` to refresh action state.

**`on_selection_changed()`**: After existing logic, check if any selected item has `_placeholder is True`. If so, disable: `change_opacity`, `grayscale`, `flip_*`, `reset_*`, and the `active_when_single_image` group (crop, color gamut).

**`clear_scene()`**: Stop `ImageLoader` if running, clean up `_placeholder_items` before existing cleanup.

**Drain coordination**: The `ImageLoader` reads from the .swp with its own read-only `SQLiteIO`. The drain timer writes to the .swp via `ThreadedIO`. SQLite handles concurrent reader + writer in WAL mode. No coordination needed beyond what exists. If WAL is not enabled, the drain's short write lock won't block the loader's reads for any meaningful duration.

### 5. `scene.py` — Undo-delete re-registration

In `addItem()`: if item has `_placeholder is True` and view has `_image_loader`, re-register in `_placeholder_items` and call `_check_viewport_and_load()`. Handles undo of delete restoring a placeholder.

## Deferred (not in this impl)

- Export with placeholder items (force-load-all before export)
- Save-As with placeholder items (same approach)
- Grayscale mip chain (currently deferred until blob loads)
- LRU / memory-limit paging (evict loaded blobs back to placeholder)

## Verification

1. `uv run python -m pytest tests/ -q -o "addopts="` — all tests pass
2. `uv run zeeref` -> open a .zref file -> should open instantly with gray placeholders
3. Images load as they enter viewport (visible pop-in)
4. Pan around — images ahead of viewport preload (50% margin)
5. Select a placeholder -> crop/flip/rotate/opacity actions grayed out
6. Select a loaded item -> all actions available
7. Delete a placeholder, undo -> placeholder reappears and loads when visible
8. Open a new file / new scene -> no crashes, clean teardown
9. Drain timer continues working — placeholder items drain correctly (`_blob_saved = True`, no blob re-encode)
