Add images to a ZeeRef session, starting one if needed.

Usage: /zeeref <session> <files...> [--title "..."] [--caption "..."]

Run `zeeref-add` with the provided arguments. Resolve any relative file paths to absolute before passing them.

If no session name is provided, use "default".
If no files are provided, ask the user.

Examples:
```bash
zeeref-add microscopy /path/to/image.png --title "10x Flake 42" --caption "SF121 Chip 2"
zeeref-add default ./scan1.png ./scan2.png
```
