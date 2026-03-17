# This file is part of BeeRef.
#
# BeeRef is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# BeeRef is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with BeeRef.  If not, see <https://www.gnu.org/licenses/>.

"""Immutable snapshot dataclasses and IO result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ItemSnapshot:
    """Immutable snapshot of an item's state for thread-safe saving."""

    save_id: str
    type: str
    x: float
    y: float
    z: float
    scale: float
    rotation: float
    flip: float
    data: dict[str, Any]
    created_at: float


@dataclass(frozen=True)
class PixmapItemSnapshot(ItemSnapshot):
    """Snapshot for pixmap items, including image dimensions and blob data."""

    width: int
    height: int
    export_filename: str
    pixmap_bytes: bytes | None = None  # None if blob already in DB
    pixmap_format: str | None = None


@dataclass(frozen=True)
class ErrorItemSnapshot:
    """Preserves a broken item's DB row from deletion."""

    save_id: str


@dataclass
class IOResult:
    """Base result from a threaded IO operation."""

    filename: Path | None
    errors: list[str] = field(default_factory=list)


@dataclass
class LoadResult(IOResult):
    """Result from loading a bee file."""

    snapshots: list[ItemSnapshot] = field(default_factory=list)
    scratch_file: Path | None = None


@dataclass
class SaveResult(IOResult):
    """Result from saving a bee file."""

    newly_saved: list[str] = field(default_factory=list)
