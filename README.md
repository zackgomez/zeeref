# ZeeRef

A reference image viewer for artists and creators. Arrange, view, and
organize your reference images with a minimal interface that stays out
of your way.

ZeeRef is a personal fork of [BeeRef](https://github.com/rbreu/beeref)
by rbreu, with new features, removed features, updated defaults, and
infrastructure improvements for large images.

## Installation

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```
git clone https://github.com/zackgomez/zeeref.git
cd zeeref
uv sync
uv run zeeref
```

## File format

ZeeRef files use the `.zref` extension. The format is a SQLite database
with images stored in an sqlar table. You can extract images with:

```
sqlite3 myfile.zref -Axv
```

To migrate old BeeRef files: `mv myfile.bee myfile.zref`

## License

GPLv3 — see [LICENSE](LICENSE).

Original BeeRef copyright (C) 2021-2024 rbreu.
