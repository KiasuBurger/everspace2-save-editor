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

"""The save being edited: loading, saving, backups and the matching preview file.

Nothing here touches GTK, so it can be tested without a display.
"""

import copy
import os
import re
import shutil
import sys
import time
from typing import Any

from .. import items, savegame
from ..savegame import Properties, Save

PREVIEW_SUFFIX = '_PREVIEW_'
# The state of every location and station visited is nine tenths of a late save and nothing
# the editor shows, so it is carried through undecoded.
UNREAD = ('BufferedLocationSaveStates', 'BufferedDockableStationInstances')
# main save value -> field of the preview that the load menu shows
PREVIEW_FIELDS = (('credits', 'credits'), ('PlayerLevel', 'PlayerLevel'), ('XP', 'XP'))


class Document:
    def __init__(self, path: str, save: Save) -> None:
        self.path = path
        self.save = save
        self.dirty = False
        self.save_name: str | None = None  # the name the load menu shows, from the preview; None without one
        self.inventories = items.inventories(save)

    @classmethod
    def load(cls, path: str) -> Document:
        save = savegame.load(path, UNREAD)
        if 'PlayerData' not in save['header'].get('save_game_class', ''):
            raise ValueError(
                '%s is not a main save (it holds %s)' % (os.path.basename(path), save['header'].get('save_game_class'))
            )
        doc = cls(path, save)
        doc.save_name = read_save_name(path)
        return doc

    @property
    def name(self) -> str:
        return os.path.splitext(os.path.basename(self.path))[0]

    @property
    def properties(self) -> Properties:
        return self.save['properties']

    def refresh(self) -> None:
        self.inventories = items.inventories(self.save)

    def inventory(self, key: str) -> items.Inventory | None:
        return next((inv for inv in self.inventories if inv.key == key), None)

    # ---- player values

    def scalar(self, key: str) -> Any:
        return items.get(self.properties, key)

    def set_scalar(self, key: str, value: Any) -> None:
        items.put(self.properties, key, value)
        self.dirty = True

    def set_save_name(self, name: str) -> None:
        """Changes the name the load menu shows. It lives in the preview file, which is
        rewritten when the save is; the save's own file keeps its name."""
        if self.save_name is None:
            raise ValueError('this save has no preview file to hold a name')
        name = name.strip()
        if name != self.save_name:
            self.save_name = name
            self.dirty = True

    # ---- writing

    def write(
        self, path: str | None = None, backup_dir: str | None = None, keep: int | None = None, backup: bool = True
    ) -> str | None:
        """Writes the save to `path` (default: where it came from). Unless `backup` is False, an
        existing file is copied to `backup_dir` first, which then holds no more than the newest
        `keep` copies of it (all of them if None). Also writes the matching preview. Returns the
        backup path."""
        path = path or self.path
        data = savegame.dumps(self.save)
        # read before anything is replaced; a preview that cannot be read is left as it is
        preview = read_preview(self.path)
        kept = backup_file(path, backup_dir, keep) if backup and os.path.exists(path) else None
        # a save written to a new file is called after that file, as the game's own are;
        # written in place it keeps the name it has
        name = os.path.splitext(os.path.basename(path))[0]
        if self.save_name and os.path.abspath(path) == os.path.abspath(self.path):
            name = self.save_name
        tmp = path + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(data)
        os.replace(tmp, path)
        if preview is not None:
            self._write_preview(preview, path, backup_dir, keep, name, backup)
            self.save_name = name
        self.path = path
        self.dirty = False
        return kept

    def _write_preview(
        self, preview: Save, path: str, backup_dir: str | None, keep: int | None, name: str, backup: bool = True
    ) -> None:
        """Gives the save at `path` a preview that shows it as `name`, based on `preview`, the
        preview of the save it was loaded from, with the values the load menu shows brought
        up to date."""
        target = preview_path(path)
        props = preview['properties']
        for key, value in (('SaveGameName', name), ('Filename', os.path.splitext(os.path.basename(target))[0])):
            if key in props:
                props[key]['value'] = value
        for main_key, preview_key in PREVIEW_FIELDS:
            if main_key in self.properties and preview_key in props:
                props[preview_key]['value'] = copy.deepcopy(self.properties[main_key]['value'])
        if backup and os.path.exists(target):
            backup_file(target, backup_dir, keep)
        tmp = target + '.tmp'
        savegame.dump(preview, tmp)
        os.replace(tmp, target)


def read_preview(path: str) -> Save | None:
    """The preview file of a main save, or None when it has none or it cannot be read; the
    save itself does not depend on it."""
    preview = preview_path(path)
    if not os.path.exists(preview):
        return None
    try:
        return savegame.load(preview)
    except savegame.ParseError, ValueError, OSError:
        return None


def read_save_name(path: str) -> str | None:
    """The name the load menu shows for a main save, from its preview file, or None when it
    has no preview that can be read."""
    preview = read_preview(path)
    if preview is None:
        return None
    try:
        return items.get(preview['properties'], 'SaveGameName')
    except KeyError:
        return None


def preview_path(path: str) -> str:
    """The preview file belonging to a main save: 'X.sav' -> 'X_PREVIEW_.sav', and the
    autosave form 'ES2__AUTO_<time>.sav' -> 'ES2__PREVIEW__AUTO_<time>.sav'."""
    directory, base = os.path.split(path)
    stem, ext = os.path.splitext(base)
    if stem.startswith('ES2__AUTO_'):
        return os.path.join(directory, 'ES2__PREVIEW__AUTO_' + stem[len('ES2__AUTO_') :] + ext)
    if stem.startswith('ES2_') and not stem.startswith('ES2__'):
        return os.path.join(directory, 'ES2__PREVIEW_' + stem[len('ES2_') :] + ext)
    return os.path.join(directory, stem + PREVIEW_SUFFIX + ext)


def default_backup_dir() -> str:
    if sys.platform == 'win32':
        base = os.environ.get('LOCALAPPDATA') or os.path.join(os.path.expanduser('~'), 'AppData', 'Local')
    else:
        base = os.environ.get('XDG_DATA_HOME') or os.path.join(os.path.expanduser('~'), '.local', 'share')
    return os.path.join(base, 'es2save', 'backups')


def backup_file(path: str, backup_dir: str | None = None, keep: int | None = None) -> str:
    """Copies a file into the backup directory under a timestamped name; returns the copy.
    With `keep`, the older copies of the file beyond the newest `keep` are deleted."""
    backup_dir = backup_dir or default_backup_dir()
    os.makedirs(backup_dir, exist_ok=True)
    stem, ext = os.path.splitext(os.path.basename(path))
    stamp = time.strftime('%Y%m%d-%H%M%S')
    target = os.path.join(backup_dir, '%s.%s%s' % (stem, stamp, ext))
    n = 1
    while os.path.exists(target):
        n += 1
        target = os.path.join(backup_dir, '%s.%s-%d%s' % (stem, stamp, n, ext))
    shutil.copy2(path, target)
    if keep is not None:
        for old in backups_of(path, backup_dir)[keep:]:
            try:
                os.remove(old)
            except OSError:
                pass  # kept, then; it does no harm
    return target


def backups_of(path: str, backup_dir: str | None = None) -> list[str]:
    """The copies backup_file has made of the file at `path`, newest first."""
    backup_dir = backup_dir or default_backup_dir()
    stem, ext = os.path.splitext(os.path.basename(path))
    pattern = re.compile(re.escape(stem) + r'\.(\d{8}-\d{6})(?:-(\d+))?' + re.escape(ext) + '$')
    try:
        names = os.listdir(backup_dir)
    except OSError:
        return []
    found = []
    for name in names:
        m = pattern.match(name)
        if m:
            found.append(((m.group(1), int(m.group(2) or 1)), os.path.join(backup_dir, name)))
    return [p for _, p in sorted(found, reverse=True)]
