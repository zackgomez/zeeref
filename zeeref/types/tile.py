from typing import NamedTuple


class TileKey(NamedTuple):
    image_id: str
    level: int
    col: int
    row: int
