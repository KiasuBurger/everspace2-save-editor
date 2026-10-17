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

"""Where the game is installed and where it keeps its saves.

Both are looked for in the places Steam and GOG put them; nothing is read from either here.
"""

import os
import sys

GAME_DIRECTORY = 'EVERSPACE™ 2'
PAKS = os.path.join('ES2', 'Content', 'Paks')
STEAM_APP = '1128920'  # the game on Steam, whose Proton prefix holds the saves on Linux


def game_root(path: str) -> str | None:
    """The installation that path is, is inside of, or directly contains; or None."""
    path = os.path.abspath(path)
    candidates = [path, os.path.join(path, GAME_DIRECTORY)]
    while os.path.dirname(path) != path:
        path = os.path.dirname(path)
        candidates.append(path)
    for c in candidates:
        if os.path.isdir(os.path.join(c, PAKS)):
            return c
    return None


def steam_libraries() -> list[str]:
    """The directories that may be Steam libraries: where Steam is installed, and the others
    it lists."""
    home = os.path.expanduser('~')
    roots = [
        os.path.join(home, '.steam', 'steam'),
        os.path.join(home, '.steam', 'root'),
        os.path.join(home, '.local', 'share', 'Steam'),
        os.path.join(home, '.var', 'app', 'com.valvesoftware.Steam', '.local', 'share', 'Steam'),
        os.path.join(home, 'snap', 'steam', 'common', '.local', 'share', 'Steam'),
        os.path.join(home, 'Library', 'Application Support', 'Steam'),
    ]
    if sys.platform == 'win32':
        for variable in ('ProgramFiles(x86)', 'ProgramFiles'):
            if os.environ.get(variable):
                roots.append(os.path.join(os.environ[variable], 'Steam'))
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam') as key:
                roots.insert(0, winreg.QueryValueEx(key, 'SteamPath')[0])
        except ImportError, OSError:
            pass
    libraries = []
    for root in roots:
        libraries.append(root)
        try:  # the other libraries are listed as "path" "..." lines
            with open(os.path.join(root, 'steamapps', 'libraryfolders.vdf'), encoding='utf-8', errors='replace') as f:
                for line in f:
                    parts = line.split('"')
                    if len(parts) >= 4 and parts[1] == 'path':
                        libraries.append(parts[3].replace('\\\\', '\\'))
        except OSError:
            pass
    return libraries


def candidates() -> list[str]:
    """The places an installation is usually found, likeliest first."""
    out = [os.path.join(library, 'steamapps', 'common', GAME_DIRECTORY) for library in steam_libraries()]
    home = os.path.expanduser('~')
    for name in (GAME_DIRECTORY, 'EVERSPACE 2'):  # GOG, as its installer, Heroic and Lutris place it
        out += [
            os.path.join(home, 'GOG Games', name),
            os.path.join(home, 'Games', name),
            os.path.join(home, 'Games', 'Heroic', name),
        ]
        if sys.platform == 'win32':
            drive = os.environ.get('SystemDrive', 'C:') + os.sep
            out += [
                os.path.join(drive, 'GOG Games', name),
                os.path.join(os.environ.get('ProgramFiles(x86)', drive), 'GOG Galaxy', 'Games', name),
            ]
    return list(dict.fromkeys(os.path.normpath(c) for c in out))


def find_game() -> str | None:
    """The game's installation directory if it is in one of the usual places, or None."""
    for c in candidates():
        if os.path.isdir(os.path.join(c, PAKS)):
            return c
    return None


def find_save_dir() -> str | None:
    """The game's SaveGames directory, if it is where the game puts it: in the user's local
    application data on Windows, and the same place inside the game's Proton prefix in a
    Steam library elsewhere."""
    saves = os.path.join('ES2', 'Saved', 'SaveGames')
    candidates = []
    if sys.platform == 'win32':
        local = os.environ.get('LOCALAPPDATA') or os.path.join(os.path.expanduser('~'), 'AppData', 'Local')
        candidates.append(os.path.join(local, saves))
    candidates += [
        os.path.join(
            library,
            'steamapps',
            'compatdata',
            STEAM_APP,
            'pfx',
            'drive_c',
            'users',
            'steamuser',
            'AppData',
            'Local',
            saves,
        )
        for library in steam_libraries()
    ]
    for c in candidates:
        if os.path.isdir(c):
            return os.path.normpath(c)
    return None


def cache_dir() -> str:
    """Where what is read from the installed game is kept, so that it is read once."""
    if sys.platform == 'win32':
        base = os.environ.get('LOCALAPPDATA') or os.path.join(os.path.expanduser('~'), 'AppData', 'Local')
    elif sys.platform == 'darwin':
        base = os.path.join(os.path.expanduser('~'), 'Library', 'Caches')
    else:
        base = os.environ.get('XDG_CACHE_HOME') or os.path.join(os.path.expanduser('~'), '.cache')
    return os.path.join(base, 'es2save')
