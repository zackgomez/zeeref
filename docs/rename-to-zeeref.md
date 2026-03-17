# Rename BeeRef to ZeeRef

Do this on main after all in-progress branches are merged.

## New repo setup

1. Create a fresh repo: `gh repo create zeeref --public`
2. Add as a new remote and push:
   ```bash
   git remote rename origin beeref-upstream
   git remote add origin git@github.com:zackgomez/zeeref.git
   git push -u origin main
   ```
3. Git history preserves full provenance — every original BeeRef commit stays intact.

## README

Replace `README.rst` with `README.md`:

```markdown
# ZeeRef

A reference image viewer for artists and creators. Arrange, view, and
organize your reference images with a minimal interface that stays out
of your way.

ZeeRef is a personal fork of [BeeRef](https://github.com/rbreu/beeref)
by rbreu, with new features, removed features, updated defaults, and
infrastructure improvements for large images.

## Installation

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/).

    git clone https://github.com/zackgomez/zeeref.git
    cd zeeref
    uv sync
    uv run zeeref

## File format

ZeeRef files use the `.zref` extension. The format is a SQLite database
with images stored in an sqlar table. You can extract images with:

    sqlite3 myfile.zref -Axv

To migrate old BeeRef files: `mv myfile.bee myfile.zref`

## License

GPLv3 — see [LICENSE](LICENSE).

Original BeeRef copyright (C) 2021-2024 rbreu.
```

## File extension

- New extension: `.zref` (drop `.bee` support)
- Files are SQLite — migration is just `mv old.bee old.zref`
- Scratch/recovery files become `.zref.swp`

## Steps

### 1. Rename package directory

```bash
git mv beeref/ zeeref/
```

### 2. Rename top-level files

```bash
git mv beeref.desktop zeeref.desktop
git mv org.beeref.BeeRef.appdata.xml org.zeeref.ZeeRef.appdata.xml
git mv BeeRef.spec ZeeRef.spec
```

### 3. Global text replacements

In order:

| Find | Replace | Scope |
|------|---------|-------|
| `beeref` | `zeeref` | All imports, pyproject.toml entry points, MIME types, desktop file, test mocks, pre-commit config |
| `BeeRef` | `ZeeRef` | APPNAME constant, class names (BeeRefApplication, BeeRefMainWindow), license headers, docs, appdata XML |
| `Bee` prefix on classes | `Zee` | BeeGraphicsView, BeeGraphicsScene, BeePixmapItem, BeeTextItem, BeeErrorItem, BeeItemMixin, BeeSettings, BeeSettingsEvents, BeeProgressDialog, BeeNotification, BeeFileIOError, BeeLogger, BeeRotatingFileHandler, BeeAssets |
| `.bee"` / `.bee'` / `*.bee` | `.zref` | File extension in sql.py, view.py, scratch.py, spec file, desktop file |
| `.bee.swp` | `.zref.swp` | scratch.py recovery files |
| `org.beeref` | `org.zeeref` | appdata XML, build_appimage.py, spec file |
| `application/x-beeref` | `application/x-zeeref` | desktop file MIME type |

### 4. Update constants.py

```python
APPNAME = 'ZeeRef'
APPNAME_FULL = f'{APPNAME} Reference Image Viewer'
WEBSITE = '<new repo URL>'
```

### 5. Update pyproject.toml

- `name = "ZeeRef"`
- `include = ["zeeref*"]`
- Entry point: `zeeref = "zeeref.__main__:main"`
- Update project URLs to new repo

### 6. Update build/CI files

- `.github/workflows/build.yml` — `pyinstaller ZeeRef.spec`
- `.github/workflows/build_appimage.yml` — artifact names
- `tools/build_appimage.py` — all org.beeref refs, output filename
- `.pre-commit-config.yaml` — `ty check zeeref/`
- `setup.cfg` — `source = zeeref`

### 7. Reinstall and test

```bash
uv sync
uv run pytest
```

### 8. Config directory

APPNAME change means Qt settings move from `~/.config/BeeRef/` to `~/.config/ZeeRef/`.
Copy manually if you want to keep settings:

```bash
cp -r ~/.config/BeeRef ~/.config/ZeeRef
```

## Not changing

- Internal method names like `bee_scene()`, `bee_view()`, `bee_actiongroups` — these are fine as-is, just internal API. Rename later if it bothers you.
- Logo/icon asset filenames (`logo.svg`) — no reason to rename these.
- CHANGELOG.rst historical entries — keep original project name in history.
