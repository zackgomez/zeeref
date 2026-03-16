# Scratch File Architecture

## Context

BeeRef currently keeps all image data in memory and writes to the .bee file only on explicit Save. This means: (1) crash = lose everything since last save, (2) Save-As with unloaded placeholders can't work without all blobs in memory, (3) no autosave.

This design introduces a scratch file (working copy) pattern — the same approach Photoshop uses with its scratch disk. The working copy is always reasonably current, Save is cheap, and crash recovery is free.

## How it works

### Open

1. Read metadata from original .bee file (read-only, fast — items table only, no blobs)
2. Show placeholders, `fit_scene()`
3. Background: copy original .bee → working copy (e.g., `file.bee.working`)
4. Background: VACUUM the working copy (compacts free pages, runs while user is orienting)
5. When copy finishes: start `ImageLoader` against the working copy

The user sees the layout instantly. The copy overlaps with the user looking at placeholders. By the time they scroll, the copy + VACUUM are likely done and images start loading.

### During operation

Scene is in-memory as today. Changes accumulate in the undo stack. Periodically (timer, e.g., every 60s if dirty, or on idle), drain changes to the working copy:

- **Drain** = iterate scene items, write metadata updates to the working copy's `items` table (same diff logic as current `write_data`)
- **New images** (paste, drag-in) get their blob written to the working copy's `sqlar` immediately on insert — they get a `save_id` right away
- **Deleted items** are removed from the working copy during drain
- **Drain is cheap** — metadata rows are small, blobs are only written for genuinely new images
- **No VACUUM during drain** — unnecessary overhead for an intermediate state

### Save

1. Final drain (flush any pending changes to working copy)
2. `os.replace(working_copy, original)` — atomic rename on same filesystem
3. Start a new working copy from the now-current original (or just keep using the same file)

Save is near-instant. No blob re-encoding, no VACUUM.

### Save-As

1. Final drain
2. `shutil.copy(working_copy, new_path)`
3. Continue operating against the working copy (or switch to a new working copy of the new file)

Works with placeholders — blobs are in the working copy's sqlar, not in memory.

### Crash recovery

On open, check for `file.bee.working`. If it exists:
- The previous session crashed (or was killed)
- Offer to recover: "Found unsaved changes from a previous session. Recover?"
- Yes → open the working copy as the source instead of the original
- No → delete the working copy, open the original normally

### Close / New Scene

1. Final drain (if user chose to save)
2. Delete the working copy
3. Clean up ImageLoader and SQLite connections

## Interaction with async loading

The scratch file is the single source for blob reads:

```
Original .bee                Working copy (.bee.working)
  (read-only)                  (read-write)
      |                              |
  read metadata              copy from original (bg)
      |                       VACUUM (bg)
  show placeholders                  |
      |                       ImageLoader reads blobs
      |                       drains write metadata
      |                       new image blobs written
      |                              |
  Save: --------atomic rename--------+
```

The `ImageLoader` always reads from the working copy. Drains write to the working copy. No contention with the original file after the initial copy.

## SQLite concurrency

The working copy is accessed by:
- `ImageLoader` thread: reads blobs from `sqlar`
- Drain (main thread or dedicated thread): writes metadata to `items`, writes new blobs to `sqlar`

SQLite in WAL mode handles this cleanly — concurrent readers + one writer. Without WAL, we'd need to serialize blob reads and drain writes, but they're unlikely to overlap in practice (drain is fast).

## Disk cost

Temporary 2x file size during operation (original + working copy). For a 500MB .bee file, that's 500MB extra. Photoshop users routinely eat 10x+ scratch disk costs; this is modest by comparison. The working copy is deleted on clean close.

## Future: mip storage

With the scratch file in place, storing pre-computed mips in the working copy becomes natural:
- On first load of an image, write mip blobs to a `mips` table in the working copy
- Subsequent opens can use cached mips from the working copy (if recovering) or regenerate
- Save could optionally include mips in the final .bee file for faster future opens

## Implementation order

This is a follow-up to the basic placeholder/async loading. The sequence:

1. **First**: Placeholder + async loading (current plan in `async-image-loading.md`)
   - ImageLoader reads from original file (read-only)
   - Save/Save-As deferred (force-load-all as stopgap)
2. **Second**: Scratch file
   - ImageLoader reads from working copy
   - Drain replaces explicit save logic
   - Save = drain + atomic rename
   - Save-As = drain + copy
   - Crash recovery
3. **Third**: Autosave timer + mip caching
