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

"""The GTK application: its style, and the window it opens."""

import os
import sys

import gi

from .. import gamedata, items
from ..gamedata import GameData
from . import APP_ID, config, find_icons
from .gtk import Gdk, Gio, GLib, Gtk
from .widgets import IconStore
from .window import EditorWindow


def stylesheet(system_theme: bool = False) -> str:
    """The editor's CSS: the colours, the game's or the GTK theme's, and then the layout
    drawn with them."""
    here = os.path.dirname(os.path.abspath(__file__))
    parts = []
    for name in ('system.css' if system_theme else 'game.css', 'style.css'):
        with open(os.path.join(here, name)) as f:
            parts.append(f.read())
    return '\n'.join(parts)


def use_libadwaita() -> bool:
    """Draws the application as libadwaita does, if it is installed. That is what nearly every
    GTK 4 program on a desktop is drawn by, so it is what makes this one match them: the same
    window buttons and spacing, and the desktop's light or dark setting and accent colour.
    Without it GTK draws its own version of the theme, which is close but not the same."""
    try:
        gi.require_version('Adw', '1')
        from gi.repository import Adw
    except ImportError, ValueError:
        return False
    Adw.init()
    return True


def windows_prefers_dark() -> bool:
    """Whether Windows is set to draw applications dark. GTK does not look, and draws light
    unless told otherwise."""
    if sys.platform != 'win32':
        return False
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize'
        ) as key:
            return winreg.QueryValueEx(key, 'AppsUseLightTheme')[0] == 0
    except OSError:
        return False


# whether libadwaita draws the controls, which it does under either theme so that changing
# between them changes only the colours
adwaita = False
provider: Gtk.CssProvider | None = None


def apply_theme() -> None:
    """Draws every window in the theme the settings name: the game's colours, always dark, or
    the desktop's theme, light or dark as the desktop is. It can be called again at any time."""
    global provider
    system_theme = config.load().get('theme') == 'system'
    settings = Gtk.Settings.get_default()
    if adwaita:
        from gi.repository import Adw

        Adw.StyleManager.get_default().set_color_scheme(
            Adw.ColorScheme.DEFAULT if system_theme else Adw.ColorScheme.FORCE_DARK
        )
    elif settings:
        settings.set_property('gtk-application-prefer-dark-theme', not system_theme or windows_prefers_dark())
    if provider is None:
        provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
    provider.load_from_string(stylesheet(system_theme))


class EditorApp(Gtk.Application):
    def __init__(
        self,
        path: str | None = None,
        game_data: str | None = None,
        icons: str | None = None,
        screenshot: str | None = None,
        select: int | None = None,
        page: str | None = None,
    ) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.path = path
        self.game_data = game_data
        self.icons_dir = icons
        self.screenshot = screenshot
        self.select = select
        self.page = page

    def do_activate(self) -> None:
        settings = Gtk.Settings.get_default()
        if settings:
            # a dialog with no button to take the focus gives it to its first label that can be
            # copied from, which would otherwise open with all its text selected
            settings.set_property('gtk-label-select-on-focus', False)
        global adwaita
        adwaita = use_libadwaita()
        apply_theme()
        game = GameData.load(self.game_data, near=self.path)
        window = EditorWindow(self, game, IconStore(find_icons(self.icons_dir)))
        window.command_line = {
            option
            for option, given in (
                ('--game-data', self.game_data),
                ('--icons', self.icons_dir),
            )
            if given
        }
        window.present()
        # the item definitions are read from the game once, unless some were asked for by name
        cache = gamedata.cache_file()

        def then() -> None:
            window.show_icon()
            if self.path:
                window.open_path(self.path, then=lambda: self.after_open(window))

        # the editor builds a cache from an older editor again. It does not ask for the game for that cache.
        window.game_files.prepare_game_files(
            then,
            ask=not self.screenshot and not os.path.exists(cache),
            data=None if self.game_data else gamedata.cache_to_build(game),
        )

    def after_open(self, window: EditorWindow) -> None:
        if self.page:
            inv = next((i for i in window.open_doc.inventories if i.key == self.page or i.kind == self.page), None)
            if self.page == 'pilot':
                window.pilot_page.show()
            elif self.page == 'perks':
                window.perks_page.show()
            elif self.page == 'devices':
                window.devices_page.show()
            elif self.page == 'blueprints':
                window.unlock_pages.show_blueprints()
            elif self.page == 'colours':
                window.unlock_pages.show_colours()
            elif self.page == 'modules':
                window.unlock_pages.show_modules()
            elif inv:
                window.inventory_page.show(inv)
        if self.select is not None:
            found = next(
                (
                    (inv, array, index)
                    for inv in window.open_doc.inventories
                    for array, index, item in inv.items()
                    if items.get(item, 'Seed') == self.select
                ),
                None,
            )
            if found:
                inv, array, index = found
                window.inventory_page.show(inv)
                window.select((inv.key, array, index))
        if self.screenshot:
            GLib.timeout_add(900, self.take_screenshot, window)

    def take_screenshot(self, window: EditorWindow) -> bool:
        """Renders the window to a PNG and quits; used to check the layout without a desktop."""
        paintable = Gtk.WidgetPaintable.new(window)
        width, height = window.get_width(), window.get_height()
        snapshot = Gtk.Snapshot()
        paintable.snapshot(snapshot, width, height)
        node = snapshot.to_node()
        texture = window.get_native().get_renderer().render_texture(node, None)
        texture.save_to_png(self.screenshot)
        self.quit()
        return False
