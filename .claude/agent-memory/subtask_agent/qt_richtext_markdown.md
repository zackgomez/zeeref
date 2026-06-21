# Qt QGraphicsTextItem rich-text quirks (markdown rendering)

`ZeeTextItem` is a `QGraphicsTextItem`. Its `paint()` path is **more limited** than
`QTextDocument.drawContents()`. ALWAYS validate markdown styling through the real
`QGraphicsTextItem.paint()` path, not `drawContents` — they differ. Repro harness:

```python
item = QtWidgets.QGraphicsTextItem()
item.document().setDefaultStyleSheet(css)
item.setHtml(html)
item.setTextWidth(item.document().idealWidth())
item.paint(painter, QtWidgets.QStyleOptionGraphicsItem(), None)  # render to QImage
```

## What the GTI painter honors vs ignores
- **`border-left` on blockquote: IGNORED** in GTI (works in drawContents only). A real
  blockquote accent bar is NOT achievable. Use indentation (default) or a background tint.
- **`<hr>`: needs block width to render at all.** With no text width set (the default -1),
  `<hr>` is invisible. Once a width is pinned it draws.
- **`<hr>` color: the `color=` HTML attribute is IGNORED.** To control hr color/contrast,
  use CSS `hr { background-color: #xxxxxx; }` via **`setDefaultStyleSheet`** (NOT inline
  `<style>` — inline didn't take for hr). `#41464c` = subtle low-contrast on the dark canvas.
- **Tables: cell gridlines need the `border="1"` HTML attribute** on `<table>`. CSS
  `th,td{border:...}` ALONE renders nothing in GTI. Recipe:
  `html.replace("<table>", '<table border="1" cellspacing="0">')` + CSS for border color/padding.
  `cellspacing="0"` collapses the boxy gaps into shared gridlines. Header full-cell fill needs
  the `bgcolor` attribute (CSS `background-color` on `<th>` only paints behind the text glyphs).
- **Code block double-bg:** fenced code is `<pre><code>`. Styling bg on both `pre` AND `code`
  double-darkens. Fix: `pre code { background: none; }` (descendant selector works via
  setDefaultStyleSheet).
- **Strikethrough:** mistune emits `<del>`, which Qt does NOT strike by default. Add
  `del { text-decoration: line-through; }`.
- **mistune default has NO task-list plugin** — `- [x]` renders literally as `[x]`.

## Width pinning — the subtle bug (cost me a real fix)
To make `<hr>` render, pin the item to its natural width: `setTextWidth(idealWidth())`.
BUT `idealWidth()` is **NOT layout-independent** — it returns the width of the *current*
(possibly already-constrained) layout. If a previous render pinned a narrow width, the next
`setHtml` wraps to that width and `idealWidth()` returns the squished value → long content
collapses into a thin column on re-render (undo/redo, `set_markdown`).
**Fix:** reset `setTextWidth(-1)` BEFORE `setHtml` so the measuring layout is unbounded,
then pin `setTextWidth(idealWidth())`. Also reset `setTextWidth(-1)` in `enter_edit_mode`
so raw-markdown editing stays no-wrap (matches original behavior).
Regression test: `tests/items/test_textitem.py::test_render_pins_natural_width`.

## Style reference (Typora Night, what landed)
Border/hr color `#41464c`; table padding `5px 10px`; header = bold + left-aligned (no extra
color — brighter `#dedede` read as "too strong"); code bg `rgba(0,0,0,0.25)`; link `#6aeae7`;
graduated heading margins (h1 16px → h4-6 10px top).
