# Scratch File Architecture

## Context

BeeRef keeps scene state in memory and persists to .bee files (SQLite databases). This doc describes the scratch file (working copy) pattern and the snapshot-based data model that enables thread-safe save/load.

## What's been built

### Scratch file on load

On open, the .bee file is copied to `~/.config/BeeRef/recovery/` and all IO happens against the copy. The original is untouched until Save. Implemented in `beeref/fileio/scratch.py`:

- `create_scratch_file(original)` — copies to recovery dir with progress reporting
- `derive_swp_path(original)` — deterministic name: `{stem}_{hash8}.bee.swp`
- `delete_scratch_file(swp)` — cleanup
- `list_recovery_files()` — scan for crash recovery

The recovery dir is created by `BeeSettings.get_recovery_dir()`. Working copies are deleted on clean exit (both `closeEvent` and `clear_scene`). Presence of a `.swp` file on startup indicates a crash.

Scene holds `_scratch_file: str | None` (initialized in `BeeGraphicsScene.__init__`).

### Snapshot-based save/load

`SQLiteIO` operates purely on data — no scene dependency. Key files: `beeref/fileio/snapshot.py`, `beeref/fileio/sql.py`.

#### Snapshot types (`beeref/fileio/snapshot.py`)

```python
@dataclass(frozen=True)
class ItemSnapshot:        # text items, base for all
    save_id, type, x, y, z, scale, rotation, flip, data, created_at

@dataclass(frozen=True)
class PixmapItemSnapshot(ItemSnapshot):  # image items
    width, height, export_filename
    pixmap_bytes: bytes | None  # None if blob already in DB
    pixmap_format: str | None

@dataclass(frozen=True)
class ErrorItemSnapshot:   # preserves broken item's DB row
    original_save_id: str
```

#### Signal result types (`beeref/fileio/snapshot.py`)

```python
@dataclass
class IOResult:            # base — filename + errors
class LoadResult(IOResult):  # + snapshots, scratch_file
class SaveResult(IOResult):  # + newly_saved (list of save_ids)
```

`ThreadedIO.finished` emits `IOResult`. Handlers use `isinstance` to narrow.

#### Save flow

1. **Main thread**: `scene.snapshot_for_save()` → `list[ItemSnapshot]` (calls `item.snapshot()` on each `user_items()`)
2. **Main thread**: passes snapshots to `ThreadedIO` which runs `save_bee()`
3. **Background thread**: `SQLiteIO.write(snapshots)` — pure data, no Qt objects
4. **Background thread**: emits `SaveResult` via `finished` signal
5. **Main thread**: `on_saving_finished` marks `_blob_saved = True` on newly-saved pixmap items

`_blob_saved` flag on `BeePixmapItem`: `False` at creation, `True` after first save or when loaded from DB. When `True`, `snapshot()` skips `pixmap_to_bytes()` (the blob is already in the DB).

#### Load flow

1. **Background thread**: `load_bee()` creates scratch file, `SQLiteIO.read()` returns `list[ItemSnapshot]`
2. **Background thread**: emits `LoadResult` via `finished` signal
3. **Main thread**: `on_loading_finished` calls `create_item_from_snapshot(snap)` for each snapshot, adds items to scene

`create_item_from_snapshot()` dispatches by `snap.type` to `cls.from_snapshot(snap)`. If the factory raises (e.g., corrupt blob), returns a `BeeErrorItem` preserving the original `save_id` for recovery.

#### Error item handling

`BeeErrorItem.snapshot()` returns `ErrorItemSnapshot(original_save_id=...)`. In `write_data`, this discards the ID from `to_delete` (preserving the original DB row) but doesn't insert or update anything.

### Schema (v4)

```sql
items: id TEXT PK, type, x, y, z, scale, rotation, flip, data JSON, width, height, created_at
sqlar: name TEXT PK, item_id TEXT UNIQUE FK, mode, mtime, sz, data BLOB
```

- `id` is UUID4 hex (32 chars), assigned at item creation time
- `created_at` is UTC unix timestamp, used for insertion-order sorting
- Migrations in `beeref/fileio/schema.py` (v1→v2→v3→v4)

### Key methods

| Method | Location | Purpose |
|--------|----------|---------|
| `item.snapshot()` | `items.py` | Snapshot item state, encode blob if unsaved |
| `cls.from_snapshot(snap)` | `items.py` | Create item from snapshot (main thread only) |
| `create_item_from_snapshot(snap)` | `items.py` | Dispatcher with error handling |
| `scene.user_items()` | `scene.py` | All `BeeItemMixin` instances (excludes internal Qt items) |
| `scene.snapshot_for_save()` | `scene.py` | Snapshot all user items |
| `SQLiteIO.read()` | `sql.py` | Returns `list[ItemSnapshot]` |
| `SQLiteIO.write(snapshots)` | `sql.py` | Returns `list[str]` (newly saved IDs) |
| `save_bee(filename, snapshots, ...)` | `fileio/__init__.py` | Emits `SaveResult` |
| `load_bee(filename, scene, ...)` | `fileio/__init__.py` | Emits `LoadResult` |

## Remaining work (this is the next task)

All the snapshot/IO infrastructure is built. The remaining work is completing the scratch file lifecycle: drain, save-through-swp, crash recovery.

### 1. Drain timer

Periodically write scene state to the .swp. The infrastructure is ready — `snapshot_for_save()` + `SQLiteIO.write()`. Needs:

- `QTimer` on the view, e.g. every 60s
- Dirty tracking (undo stack's `indexChanged` signal sets a flag, drain clears it)
- **Drain** = `scene.snapshot_for_save()` on main thread → `SQLiteIO.write(snapshots)` on background thread
- **New images** (paste, drag-in) should get their blob written to the .swp immediately on insert — `_blob_saved` becomes True, subsequent drains only write metadata
- **Drain is cheap** — metadata rows are small, blobs only for genuinely new images
- **No VACUUM during drain**

### 2. Save through .swp

Currently save still writes directly to the target file. It should drain to .swp first, then atomic-copy to the target:

1. Final drain (flush pending changes to .swp)
2. Copy .swp → temp file in same directory as original (`tempfile.NamedTemporaryFile(dir=..., delete=False)`)
3. `os.replace(temp_file, original)` — atomic swap
4. .swp stays intact, keep operating against it

### 3. Save-As through .swp

1. Final drain
2. Copy .swp → new path
3. Rename .swp in recovery dir to match new path:
   - Close SQLite connection
   - `os.rename(old_swp, derive_swp_name(new_path))`
   - Reopen connection
4. Continue operating against renamed .swp

Same rename for untitled scene's first Save.

### 4. Crash recovery UI

`list_recovery_files()` exists but no UI. On startup, scan recovery dir for `*.bee.swp`:
- Show "Recover unsaved changes for {name}?"
- Yes → open .swp as source
- No → delete .swp

### 5. Close / New Scene cleanup

1. Final drain (if saving)
2. Delete .swp from recovery dir
3. Currently `closeEvent` and `clear_scene` delete the .swp — this just needs to add the drain step

## SQLite concurrency

The .swp is accessed by:
- Drain: writes snapshots (background thread via `ThreadedIO`)
- Future `ImageLoader` thread: would read blobs from `sqlar` (see `async-image-loading.md`)

SQLite in WAL mode handles concurrent readers + one writer. Without WAL, serialize reads and writes (drain is fast, unlikely to overlap).

## Disk cost

2x during operation (original + .swp), brief 3x during Save (+ temp file). Deleted on clean exit.

## Context: future work that should inform decisions

These are NOT part of this task but the scratch file design should not make them harder:

- **Async/placeholder loading** (`async-image-loading.md`) — viewport-driven blob loading from the .swp. The snapshot-based `SQLiteIO.read()` return is the right interface for this.
- **Tiled image storage** (`tiled-image-storage.md`) — replaces sqlar with a tiles table. The .swp would hold tiles instead of single blobs.
- **OpenGL viewport** — GPU rendering with shaders. Unrelated to IO but motivates some item pipeline decisions.

## Key files

| File | Purpose |
|------|---------|
| `beeref/fileio/snapshot.py` | Snapshot dataclasses + IOResult types |
| `beeref/fileio/sql.py` | SQLiteIO — read/write snapshots, no scene dependency |
| `beeref/fileio/scratch.py` | Scratch file create/delete/list |
| `beeref/fileio/__init__.py` | `load_bee`, `save_bee`, `ThreadedIO` |
| `beeref/items.py` | `snapshot()`, `from_snapshot()`, `create_item_from_snapshot()` |
| `beeref/scene.py` | `user_items()`, `snapshot_for_save()` |
| `beeref/view.py` | `on_loading_finished`, `on_saving_finished`, `do_save` |
| `beeref/__main__.py` | `closeEvent` .swp cleanup |
| `docs/async-image-loading.md` | Future async loading design (context only) |
| `docs/tiled-image-storage.md` | Future tiled storage design (context only) |
| `tests/utils.py` | Test helpers: `assert_load_result`, `assert_save_result`, etc. |
