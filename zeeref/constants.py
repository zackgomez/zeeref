# This file is part of ZeeRef.
#
# ZeeRef is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# ZeeRef is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with ZeeRef.  If not, see <https://www.gnu.org/licenses/>.

APPNAME = "ZeeRef"
APPNAME_FULL = f"{APPNAME} Reference Image Viewer"
try:
    from importlib.metadata import version

    VERSION = version("zeeref")
except Exception:
    VERSION = "dev"
WEBSITE = "https://github.com/zackgomez/zeeref"
COPYRIGHT = "Copyright © 2025-2026 Zack Gomez, 2021-2024 Rebecca Breu"

CHANGED_SYMBOL = "✎"

COLORS = {
    # Qt:
    "Active:Base": (60, 60, 60),
    "Active:AlternateBase": (70, 70, 70),
    "Active:Window": (40, 40, 40),
    "Active:Button": (40, 40, 40),
    "Active:Text": (200, 200, 200),
    "Active:HighlightedText": (255, 255, 255),
    "Active:WindowText": (200, 200, 200),
    "Active:ButtonText": (200, 200, 200),
    "Active:Highlight": (83, 167, 165),
    "Active:Link": (90, 181, 179),
    "Disabled:Base": (40, 40, 40),
    "Disabled:Window": (40, 40, 40, 50),
    "Disabled:WindowText": (120, 120, 120),
    "Disabled:Light": (0, 0, 0, 0),
    "Disabled:Text": (140, 140, 140),
    # ZeeRef specific:
    "Scene:Selection": (116, 234, 231),
    "Scene:Canvas": (60, 60, 60),
    "Scene:Text": (200, 200, 200),
}
