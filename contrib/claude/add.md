Add images to a ZeeRef session, starting one if needed.

Usage: /zeeref <session> <files...>

Build a JSON array with one entry per image, each with `path` (absolute) and optional `title` and `caption` fields. Pipe it to `zeeref-add`:

```bash
echo '[
  {"path": "/abs/path/img1.png", "title": "10x Flake 42", "caption": "SF121 Chip 2"},
  {"path": "/abs/path/img2.png", "title": "5x overview"}
]' | zeeref-add <session> --stdin
```

If no session name is provided, use "default".
If no files are provided, ask the user.
Resolve all file paths to absolute before including in the JSON.
