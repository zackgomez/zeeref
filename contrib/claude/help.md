Quick reference for the `zeeref-cli` shared scratch area.

ZeeRef is a reference-image viewer. A *session* is a running zeeref window addressable by name; `zeeref-cli` sends JSON over a local socket and prints JSON on stdout. Use it to drop images and markdown notes for the user, then read or modify what's there.

## Lifecycle (these spawn a session if needed)

```bash
zeeref-cli start    SESSION                        # idempotent ensure-running
zeeref-cli new      SESSION [--force]              # fresh empty scene
zeeref-cli open     SESSION PATH.zref [--force]    # load a .zref file
zeeref-cli add      SESSION FILES...               # auto-spawns
zeeref-cli add-text SESSION "markdown text"        # auto-spawns
                    [--wrap COLS]
```

Text items soft-wrap at a column width so long prose stays readable. The
default comes from settings (100 columns); `--wrap 0` disables wrapping for
that item. Wrapping is a *maximum* — text narrower than the limit keeps its
own width, so tables and short notes are unaffected.

`--force` discards an unsaved dirty scene.

## Probes (no spawn — error fast if session is down)

```bash
zeeref-cli ping     SESSION
zeeref-cli status   SESSION    # {loaded_file, item_count, dirty}
zeeref-cli sessions            # list running sessions
zeeref-cli stop     SESSION    # shut down a running session
```

## Reads (no spawn)

```bash
zeeref-cli list  SESSION       # all items
zeeref-cli get   SESSION ID    # one item
zeeref-cli view  SESSION       # viewport state (center, zoom, geometry)
```

## Writes (no spawn — error if session is down)

```bash
zeeref-cli edit   SESSION ID  [--x ...] [--y ...] [--scale ...] \
                              [--rotation ...] [--z ...] [--flip ±1] \
                              [--opacity 0..1] [--title ...] \
                              [--caption ...] [--text ...] [--wrap COLS]

zeeref-cli delete SESSION ID [ID...]

zeeref-cli save   SESSION [PATH.zref] [--force]
```

`edit` is additive: only fields you pass are touched. Empty string or `null` on `title`/`caption`/`text` clears that metadata. `wrap` takes an integer 0-500 (`null` is rejected). Use `--stdin` for batch edits (JSON array of `{id, ...fields}`).

`save` writes the live scene to a `.zref`. Omit the path to overwrite the session's current file (`status.loaded_file`); pass a path to save-as, which then becomes the session's file. `--force` is required only to overwrite a *different* existing file.

## Item shape (from `list` / `get`)

```json
{
  "id": "<uuid hex>",
  "type": "pixmap" | "text",
  "x": 0.0, "y": 0.0,
  "scale": 1.0, "rotation": 0.0,
  "z": 0.0, "flip": 1,
  "data": {
    "filename": "...", "title": "...", "caption": "...",
    "opacity": 1.0, "text": "...", "wrap": 100
  },
  "image_id": "...", "width": int, "height": int
}
```

## Reply shapes

- `add` / `add-text` echo `ids` (list of newly-created item ids) — feed them straight into `edit`/`delete`.
- `edit` echoes `items` (the post-edit snapshots) — confirm the change or read side effects without a follow-up `get`.
- `open` echoes `status` (loaded_file, item_count, dirty) — no follow-up `status` call needed.
- `save` echoes `path` (where it landed) and `status` (loaded_file, item_count, dirty=false) — confirm the write without a follow-up `status`.
- `ping` echoes `protocol_version` and `app_version` from the server's hello.

## Notes

- `x, y` are the item's **top-left in scene coords**, matching the `.zref` `items` schema. Scale and rotation grow from local (0, 0); for centered placement, omit `x/y` (zeeref centers at the view) or compute the offset yourself.
- Unknown ids on `get` / `edit` / `delete` return an error (non-zero exit).
- Reads bypass the mutation queue; writes serialize.

## Patterns

```bash
# Add and immediately edit — no separate list call needed
id=$(zeeref-cli add scratch /tmp/img.png | jq -r '.ids[0]')
zeeref-cli edit scratch "$id" --title "tagged" --x 200 --y 100

# Find an image by filename and move it
id=$(zeeref-cli list scratch \
     | jq -r '.items[] | select(.data.filename | test("a.png$")) | .id')
zeeref-cli edit scratch "$id" --x 200 --y 100

# Drop a markdown note
zeeref-cli add-text scratch "TODO: pick favorite" --x 0 --y 0

# Snapshot the scene and react
zeeref-cli list scratch | jq '.items | length'
```
