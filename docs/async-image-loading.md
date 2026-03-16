# Placeholder / Async Image Loading

## Context

BeeRef currently loads all image blobs eagerly on file open — a 50-image file must decode all 50 JPEGs + generate all mip chains before the user sees anything. This replaces that with: (1) instant metadata-only open with placeholder items, (2) viewport-driven async blob loading in the background.

## Threading Model

Three threads:

1. **Main thread** (Qt GUI) — Creates QPixmaps, updates the scene, handles user input. Receives loaded PIL images from the image loader via signal/slot.
2. **ThreadedIO** (existing QThread, short-lived) — Runs `read_metadata()` to read the items table only. Creates placeholder items, queues them. Finishes quickly (no blobs to decode).
3. **ImageLoader** (new QThread, long-lived) — Starts after metadata load completes. Opens its own SQLite connection (can't share across threads). Pulls save_ids from a queue, fetches blobs, decodes with Pillow, generates mip chain as PIL images, emits `item_loaded` signal back to main thread.

```
ThreadedIO          Main Thread              ImageLoader
    |                    |                        |
 read metadata           |                        |
 queue placeholders      |                        |
 emit finished -------> fit_scene()               |
    (done)               build placeholder dict   |
                         start loader ----------> opens own SQLite conn
                         check viewport --------> request_load(id1, id2...)
                         |                        fetch blob, PIL decode
                         |                        emit item_loaded ----+
                         _on_blob_loaded() <------+
                         create QPixmap
                         attach to item
                         check viewport again --> ...
```

## Files to modify

1. `beeref/fileio/sql.py` — New `read_metadata()` method
2. `beeref/items.py` — Placeholder state, paint, load transition
3. `beeref/fileio/__init__.py` — `load_bee_metadata()`, `ImageLoader` thread
4. `beeref/view.py` — Viewport observer, load triggering, action disabling, cleanup
5. `beeref/scene.py` — Minor tweak for undo-delete re-registration

## Implementation

### 1. `sql.py` — Metadata-only read

New `read_metadata()`: queries `SELECT ... FROM items` (no sqlar JOIN). For pixmap items, creates placeholders via `BeePixmapItem.create_placeholder(width, height)`. Stashes filename on `scene._bee_filename` for the image loader to open its own connection. No `msleep(10)` — metadata is fast.

### 2. `items.py` — BeePixmapItem placeholder support

**`create_placeholder(width, height)` classmethod**: Uses `__new__` + explicit `QGraphicsPixmapItem.__init__` with null pixmap. Sets `_placeholder = True`, `_placeholder_width/height`, `_crop` directly. Initializes `_mip_chain = []`, `_grayscale = False`, etc.

**`paint()`**: When `_placeholder`, draw filled rect (gray, alpha ~30) + outline (gray, alpha ~80, cosmetic pen) + `paint_selectable`. Return early.

**`bounding_rect_unselected()`**: When `_placeholder`, return `QRectF(0, 0, _placeholder_width, _placeholder_height)`. Existing crop path handles cropped placeholders since `create_from_data` updates `_crop` and `_placeholder_width/height` from the JSON crop.

**`create_from_data()`**: For placeholders, set `item._grayscale` directly (not through property setter which accesses pixmap). Update `_placeholder_width/height` if crop is present.

**`load_pixmap_from_pil(pil_img, mip_pils)`**: Main-thread method called when background load completes. Saves crop, calls `super().setPixmap()` (bypasses our override to avoid `reset_crop` + `_generate_mips`), builds mip chain from PIL mips via `_pil_to_qpixmap`, restores crop, clears `_placeholder`, applies deferred grayscale, calls `prepareGeometryChange()` + `update()`.

**`has_selection_handles()`**: Return `False` when placeholder — disables scale/rotate/flip handles, keeps selection outline.

**Guards**: `sample_color_at()`, `__str__()`, `color_gamut` — return early/safe values for placeholders.

### 3. `fileio/__init__.py` — Loader infrastructure

**`load_bee_metadata(filename, scene, worker=None)`**: Creates `SQLiteIO`, calls `read_metadata()`, stores `scene._bee_filename = filename`.

**`ImageLoader(QThread)`**:
- Signal: `item_loaded = pyqtSignal(int, object, object)` — (save_id, pil_img, mip_pils)
- Constructor takes `filename`, opens its own SQLite connection in `run()` (thread safety)
- `request_load(save_id)`: thread-safe, deduplicating (set + Queue)
- `run()`: loop reading from queue, for each: fetch blob, `Image.open(BytesIO(blob))`, `.load()`, generate mip PIL chain (LANCZOS), emit `item_loaded`
- `stop()`: sets flag, waits for thread to finish

### 4. `view.py` — Viewport observer + orchestration

**`open_from_file()`**: Uses `load_bee_metadata` instead of `load_bee`.

**`on_loading_finished()`**: After `fit_scene()`, builds `_placeholder_items` dict (save_id -> item), starts `ImageLoader(filename)`, connects `item_loaded` -> `_on_blob_loaded`, calls `_check_viewport_and_load()`.

**`_check_viewport_and_load()`**: Gets viewport rect in scene coords, inflates by 50% margin, iterates `_placeholder_items`, calls `request_load` for items whose `sceneBoundingRect()` intersects the buffered rect. Called live — no debounce.

**Hooks**: Override `scrollContentsBy(dx, dy)`, append to `scale()` and `resizeEvent()` — all call `_check_viewport_and_load()`.

**`_on_blob_loaded(save_id, pil_img, mip_pils)`**: Pops from `_placeholder_items`, calls `item.load_pixmap_from_pil(...)`. When dict is empty, stops loader. Calls `on_selection_changed()` to refresh action state.

**`on_selection_changed()`**: After existing logic, check if any selected item has `_placeholder = True`. If so, disable: `change_opacity`, `grayscale`, `flip_*`, `reset_*`, and the `active_when_single_image` group (crop, color gamut).

**`clear_scene()`**: Stop blob loader, clean up `_placeholder_items` and `_bee_filename` before existing cleanup.

### 5. `scene.py` — Undo-delete re-registration

In `addItem()`: if item has `_placeholder = True` and view has `_image_loader`, re-register in `_placeholder_items` and call `_check_viewport_and_load()`. Handles undo of delete.

## Deferred (not in this impl)

- Export with placeholder items (force-load-all before export)
- Save-As with placeholder items (same approach)
- Grayscale mip chain
- LRU / memory-limit paging

## Verification

1. `uv run --extra test python -m pytest -x -q` — all tests pass
2. `uv run beeref` -> open a .bee file -> should open instantly with gray placeholders
3. Images load as they enter viewport (visible pop-in)
4. Pan around — images ahead of viewport preload (50% margin)
5. Select a placeholder -> crop/flip/rotate/opacity actions grayed out
6. Select a loaded item -> all actions available
7. Delete a placeholder, undo -> placeholder reappears and loads when visible
8. Open a new file / new scene -> no crashes, clean teardown
