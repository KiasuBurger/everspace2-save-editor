# Copyright (C) 2026 KiasuBurger
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""The game's icons as the editor uses them: which ones, and where they are kept as PNGs.

gameicons.py reads the textures out of the game; this says which of them the editor draws
with, writes those to the cache when the editor first runs, and finds them there afterwards.
"""

import os
import shutil
from collections.abc import Callable

from .. import gamepaths

# the folders the editor uses: all of each, or the textures named
IMAGES = '../../../ES2/Content/UI/Images/'
# the folders the editor uses: all of each, or the textures named
FOLDERS = {
    'Items': None,
    'Elements/Items/Backgrounds': None,
    'Types': None,
    'Icons/Crafting': None,
    'Icons/Menu': ['icon_menu_inventory_active_60x60_2x'],
}


def cache_dir() -> str:
    """Where the editor keeps the extracted icons."""
    return os.path.join(gamepaths.cache_dir(), 'icons')


def wanted(name: str) -> bool:
    """Whether the asset of this name below IMAGES is an icon the editor uses."""
    folder, _, stem = name.rpartition('/')
    if folder not in FOLDERS:
        return False
    stems = FOLDERS[folder]
    return stems is None or stem in stems


def extract(game: str, destination: str, progress: Callable[[int, int], object] | None = None) -> None:
    """Writes the game's icons to destination as PNGs, replacing what is there; if it fails,
    what is there is left. progress is called with (done, total) as it goes. Raises OSError,
    or ValueError if the game's files are not as expected."""
    from .. import gameicons  # brings in numpy, which only reading the game's files needs

    partial = destination + '.partial'
    shutil.rmtree(partial, ignore_errors=True)
    read = written = 0
    try:
        for name, data in gameicons.read_assets(game, IMAGES, wanted, progress):
            read += 1
            texture = gameicons.find_texture(data)
            if texture:  # the rest are not textures, or not of a kind that is read here
                fmt, width, height, at = texture
                path = os.path.join(partial, *name.split('/')) + '.png'
                os.makedirs(os.path.dirname(path), exist_ok=True)
                gameicons.write_png(
                    path,
                    gameicons.decode_texture(
                        fmt, width, height, data[at : at + gameicons.texture_bytes(fmt, width, height)]
                    ),
                )
                written += 1
        if not read:
            raise ValueError('there are no icons in the containers in %s' % os.path.join(game, gamepaths.PAKS))
        if not written:
            raise ValueError('none of the icons in %s could be read' % os.path.join(game, gamepaths.PAKS))
        shutil.rmtree(destination, ignore_errors=True)
        os.replace(partial, destination)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise


def find_icons(explicit: str | None = None) -> str | None:
    """The directory of extracted game icons, the given one or the one in the user's cache, if
    the icons are there."""
    directory = explicit or cache_dir()
    return os.path.abspath(directory) if os.path.isdir(os.path.join(directory, 'Items')) else None
