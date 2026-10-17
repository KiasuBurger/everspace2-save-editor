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

"""What the editor remembers between runs, in a file in the user's configuration directory.

save_directory      where the save last opened or written is, for the Open dialog
open_directory      where the Open dialog always starts; without it, where save_directory is
game_directory      the game's installation, when it was chosen rather than found
backups             False when saving keeps no copy of the file it replaces
backup_directory    where saving keeps a copy of the file it replaces; without it, the default
backups_kept        how many copies of each file to keep there; without it, all of them
theme               'game' for the game's colours (the default), or 'system' for the GTK theme

The command line's options win over these.
"""

import json
import os
import sys
from typing import Any

from .document import default_backup_dir


def config_file() -> str:
    if sys.platform == 'win32':
        base = os.environ.get('APPDATA') or os.path.join(os.path.expanduser('~'), 'AppData', 'Roaming')
    elif sys.platform == 'darwin':
        base = os.path.join(os.path.expanduser('~'), 'Library', 'Application Support')
    else:
        base = os.environ.get('XDG_CONFIG_HOME') or os.path.join(os.path.expanduser('~'), '.config')
    return os.path.join(base, 'es2save', 'editor.json')


def load() -> dict[str, Any]:
    """The settings; none if the file is not there or cannot be read."""
    try:
        with open(config_file(), encoding='utf-8') as f:
            values = json.load(f)
    except OSError, ValueError:
        return {}
    return values if isinstance(values, dict) else {}


def update(**values: Any) -> None:
    """Changes the given settings and keeps the rest; a setting given as None is removed. Not
    being able to write the file is not an error: the editor then just does not remember."""
    settings = load()
    if all(settings.get(k) == v for k, v in values.items()):
        return
    settings.update(values)
    settings = {k: v for k, v in settings.items() if v is not None}
    path = config_file()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + '.new', 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=2)
            f.write('\n')
        os.replace(path + '.new', path)
    except OSError:
        pass


def backups() -> bool:
    """Whether saving keeps a copy of the file it replaces."""
    return load().get('backups') is not False


def backup_directory() -> str:
    """Where saving keeps the copies of the files it replaces."""
    folder = load().get('backup_directory')
    return folder if isinstance(folder, str) and folder else default_backup_dir()


def backups_kept() -> int | None:
    """How many copies of each file the backup directory keeps, or None for all of them."""
    kept = load().get('backups_kept')
    return kept if isinstance(kept, int) and not isinstance(kept, bool) and kept > 0 else None
