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

"""Reading what the editor takes from the installed game, the first time it runs."""

import os
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from .. import gamedata, gameidentifiers, gamepaths
from ..gamedata import GameData
from . import config, icons
from .gtk import Gio, GLib, Gtk
from .widgets import label

if TYPE_CHECKING:
    from .window import EditorWindow


class GameFiles:
    def __init__(self, window: EditorWindow) -> None:
        self.window = window

    def prepare_game_files(self, then: Callable[[], object], ask: bool = True, data: str | None = None) -> None:
        """Reads what the editor takes from the installed game and does not have yet into the
        cache, and calls then() once the editor can be used: the icons, and the item
        definitions if data, the path to write them to, is given. If the game is not in one
        of the usual places the user is asked where it is, unless ask is false."""
        if self.window.icons.directory and not data:
            then()
            return
        game = installed_game()
        if game:
            self.read_game(game, then, data)
        elif ask:
            self.ask_for_game(then, data)
        else:
            self.without_game(then)

    def without_game(self, then: Callable[[], object]) -> None:
        """Says what the editor is doing without, and goes on."""
        missing = []
        if not self.window.game:
            missing.append('there are no item definitions, so no names, ranges or checks')
        if not self.window.icons.directory:
            missing.append('there are no icons, so the tiles are plain')
        if missing:
            self.window.set_status(
                "The game's files have not been read: %s." % '; '.join(missing), error=not self.window.game
            )
        then()

    def ask_for_game(self, then: Callable[[], object], data: str | None, problem: str | None = None) -> None:
        detail = (
            'The editor takes the item definitions and the icons it shows from the installed game, and '
            'the game is not in any of the usual places.'
        )
        dialog = Gtk.AlertDialog(
            message='Where is Everspace 2 installed?',
            detail='%s\n\n%s' % (problem, detail) if problem else detail,
            buttons=['Continue without', 'Choose folder…'],
        )
        dialog.set_cancel_button(0)
        dialog.set_default_button(1)

        def picked(dlg: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
            try:
                path = dlg.select_folder_finish(result).get_path()
            except GLib.Error:
                self.ask_for_game(then, data, problem)
                return
            game = gamepaths.game_root(path)
            if game:
                config.update(game_directory=game)
                self.read_game(game, then, data)
            else:
                self.ask_for_game(then, data, '%s is not an Everspace 2 installation.' % path)

        def done(dlg: Gtk.AlertDialog, result: Gio.AsyncResult) -> None:
            try:
                choose = dlg.choose_finish(result) == 1
            except GLib.Error:
                choose = False
            if choose:
                Gtk.FileDialog(title='Choose the folder Everspace 2 is installed in').select_folder(
                    self.window, None, picked
                )
            else:
                self.without_game(then)

        dialog.choose(self.window, None, done)

    def read_again(self, game: str, then: Callable[[], object]) -> None:
        """Reads the item definitions and the icons from the game again, into the cache, as
        after the game has been updated."""
        self.read_game(game, then, gamedata.cache_file(), icons_too=True)

    def read_game(self, game: str, then: Callable[[], object], data: str | None, icons_too: bool = False) -> None:
        """Reads the game's files behind a progress dialog, with the window out of use meanwhile:
        the item definitions if data is given, and the icons if the editor has none yet or
        icons_too is set."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for side in ('top', 'bottom', 'start', 'end'):
            getattr(box, 'set_margin_' + side)(20)
        box.append(label("Reading the game's files", 'section-title'))
        box.append(label('From %s' % game, 'dim', wrap=True))
        bar = Gtk.ProgressBar(show_text=True, text='Item definitions')
        bar.set_size_request(380, -1)
        box.append(bar)
        dialog = Gtk.Window(
            transient_for=self.window, modal=True, title='Game files', resizable=False, deletable=False, child=box
        )
        dialog.add_css_class('es2')
        dialog.connect('close-request', lambda _w: True)  # it goes when the reading is over
        self.window.set_sensitive(False)
        dialog.present()
        icons_dir = icons.cache_dir() if icons_too or not self.window.icons.directory else None

        def progress(done: int, total: int) -> bool:
            bar.set_fraction(done / total)
            bar.set_text('Icon %d of %d' % (done, total))
            return False

        def work() -> None:
            errors = []
            for what, step in (
                ('item definitions', data and (lambda: gameidentifiers.write(game, data))),
                (
                    'icons',
                    icons_dir
                    and (
                        lambda: icons.extract(game, icons_dir, lambda done, total: GLib.idle_add(progress, done, total))
                    ),
                ),
            ):
                try:
                    if step:
                        step()
                except Exception as e:  # noqa: BLE001 (shown to the user; the dialog must go whatever happens)
                    errors.append((what, e))
            GLib.idle_add(finish, errors)

        def finish(errors: list[tuple[str, Exception]]) -> bool:
            dialog.destroy()
            self.window.set_sensitive(True)
            failed = [what for what, _ in errors]
            if data and 'item definitions' not in failed:
                self.window.game = GameData.load(data)
            if icons_dir and 'icons' not in failed:
                self.window.icons = type(self.window.icons)(icons_dir)
            if not errors:
                self.window.set_status("Read the game's files into %s" % os.path.dirname(icons_dir or data or ''))
                then()
                return False
            detail = '\n\n'.join('The %s: %s.' % (what, str(e).rstrip('.')) for what, e in errors)
            alert = Gtk.AlertDialog(message="Some of the game's files could not be read", detail=detail)
            alert.choose(self.window, None, lambda *_: self.without_game(then))
            return False

        threading.Thread(target=work, daemon=True).start()


def installed_game() -> str | None:
    """The game's installation: the one chosen in the settings or when the editor asked for
    it, while it is still there, else one found in the usual places."""
    chosen = config.load().get('game_directory')
    if isinstance(chosen, str):
        game = gamepaths.game_root(chosen)
        if game:
            return game
    return gamepaths.find_game()
