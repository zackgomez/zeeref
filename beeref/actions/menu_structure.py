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

MENU_SEPARATOR = 0

menu_structure = [
    'new_scene',
    {
        'menu': '&Save',
        'items': [
            'save',
            'save_as',
            MENU_SEPARATOR,
            'export_scene',
            'export_images',
        ],
    },
    {
        'menu': '&Load',
        'items': [
            'open',
            {
                'menu': 'Open &Recent',
                'items': '_build_recent_files',
            },
            MENU_SEPARATOR,
            'insert_images',
        ],
    },
    MENU_SEPARATOR,
    {
        'menu': '&View',
        'items': [
            'fit_scene',
            'fit_selection',
            MENU_SEPARATOR,
            'fullscreen',
            'always_on_top',
            'show_titlebar',
        ],
    },
    'insert_text',
    {
        'menu': '&Transform',
        'items': [
            'crop',
            'flip_horizontally',
            'flip_vertically',
            MENU_SEPARATOR,
            'reset_scale',
            'reset_rotation',
            'reset_flip',
            'reset_crop',
            'reset_transforms',
        ],
    },
    {
        'menu': '&Normalize',
        'items': [
            'normalize_height',
            'normalize_width',
            'normalize_size',
        ],
    },
    {
        'menu': '&Arrange',
        'items': [
            'arrange_optimal',
            'arrange_horizontal',
            'arrange_vertical',
            'arrange_square',
        ],
    },
    {
        'menu': '&Images',
        'items': [
            'change_opacity',
            MENU_SEPARATOR,
            'show_color_gamut',
            'sample_color',
        ],
    },
    'help',
    'about',
    'settings',
    MENU_SEPARATOR,
    'quit',
]
