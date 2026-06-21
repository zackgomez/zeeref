# Subtask Agent Memory — ZeeRef

## Index
- [qt_richtext_markdown.md](qt_richtext_markdown.md) — Qt `QGraphicsTextItem` rich-text quirks for markdown rendering (hr, blockquote, tables, code, width pinning). Read this before touching `ZeeTextItem` styling.

## Project quick facts
- ZeeRef = PyQt6 whiteboard/mood-board app. Markdown cells = `ZeeTextItem` in `zeeref/items.py`, rendered via `mistune.html()` → `setHtml()`.
- Package mgr: `uv`. Run tests: `QT_QPA_PLATFORM=offscreen uv run --extra test pytest` (the default addopts add coverage; use `-o addopts=""` to disable). Lint: `uvx ruff@0.15.6 check`, `uv run ty check zeeref/` (ty only checks `zeeref/`, not `tests/`).
- Commit style: plain imperative sentence, NO `[type]` prefix (e.g. "Add zeeref-cli save subcommand"). Base branch: `master`.
- CLI to drive a live session: `uv run zeeref --session NAME &` then `uv run zeeref-cli add-text NAME --stdin < payload.json`. Installed `zeeref`/`zeeref-cli` point at the MAIN checkout — to test worktree code, launch the GUI from the worktree yourself; the CLI connects by socket name regardless.
- Screenshot a specific window (niri compositor): `niri msg windows` to find the Window ID by PID, then `niri msg action focus-window --id N`. Full-desktop `grim` misses windows on other workspaces.
