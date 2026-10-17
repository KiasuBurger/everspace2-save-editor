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

"""Reader, writer and item tools for Everspace 2 savegames.

    import es2save
    save = es2save.load('Saved/SaveGames/Example.sav')
    save['properties']['credits']['value'] += 1000
    es2save.dump(save, 'Example-edited.sav')

Modules:
    savegame   the save format: container, GVAS property tree and ES2-specific structs
    chaos      Chaos catalyst outcomes for items
    editor     the save editor
"""

from .savegame import (
    EncodeError,
    ParseError,
    build_gvas,
    dump,
    dumps,
    load,
    loads,
    pack_container,
    parse_gvas,
    unpack_container,
)

try:
    from ._version import __version__
except ImportError:  # a checkout that has not been built or installed
    __version__ = 'unknown'

__all__ = [
    'EncodeError',
    'ParseError',
    '__version__',
    'build_gvas',
    'dump',
    'dumps',
    'load',
    'loads',
    'pack_container',
    'parse_gvas',
    'unpack_container',
]
