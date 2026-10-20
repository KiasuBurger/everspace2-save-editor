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

"""The editor's window.

Laid out like the game's inventory: where things are on the left (ships, storage,
resources), the page that is on show in the middle, and the selected item's card on the
right with the controls to change it underneath. The window opens and saves, knows what
is selected, and has an object for each thing that fills one of those areas."""

import contextlib
import os
import threading
from collections.abc import Callable, Iterator, Sequence
from typing import Any

from .. import __version__, devices, items, perks, unlocks
from ..gamedata import GameData
from ..gamepaths import find_save_dir
from ..items import Inventory, Item
from . import COPYRIGHT, LICENCE, config
from .devices_page import DevicesPage
from .document import Document
from .game_files import GameFiles
from .gtk import Gio, GLib, Gtk, Pango
from .inventory_page import InventoryPage
from .item_panel import ItemPanel
from .perks_page import PerksPage
from .pilot_page import PilotPage
from .unlock_pages import UnlockPages
from .widgets import IconStore, Key, Tile, clear, close_on_escape, label, margins

SHORTCUTS = [  # (window action, key, method)
    ('open', '<Control>o', 'choose_file'),
    ('save', '<Control>s', 'save'),
    ('save-as', '<Control><Alt>s', 'choose_save_as'),
    ('quit', '<Control>q', 'close'),
    ('settings', '<Control>comma', 'show_settings'),
]


class EditorWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, game: GameData, icons: IconStore) -> None:
        super().__init__(application=app, title='Everspace 2 save editor')
        self.game = game
        self.icons = icons
        self.doc: Document | None = None
        self.inventory: Inventory | None = None  # the Inventory on show, or None for the pilot page
        self.selection: Key | None = None
        self.tiles: dict[Key, Tile] = {}
        self.busy = False  # set while controls are being filled in, to ignore their signals
        self.command_line: set[str] = set()  # the options the editor was started with, which settings do not change
        self.add_css_class('es2')
        self.set_default_size(1560, 960)

        header = Gtk.HeaderBar()
        self.title_label = label('EVERSPACE 2 SAVE EDITOR', 'title app-title', xalign=0.5)
        self.subtitle_label = label('No save open', 'subtitle', xalign=0.5)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        titles.append(self.title_label)
        titles.append(self.subtitle_label)
        header.set_title_widget(titles)
        open_button = Gtk.Button(label='Open…')
        open_button.connect('clicked', lambda _b: self.choose_file())
        header.pack_start(open_button)
        self.save_as_button = Gtk.Button(label='Save as…', sensitive=False)
        self.save_as_button.connect('clicked', lambda _b: self.choose_save_as())
        self.save_button = Gtk.Button(label='Save', sensitive=False)
        self.save_button.add_css_class('accent')  # ours, and the system theme's word for it
        self.save_button.add_css_class('suggested-action')
        self.save_button.connect('clicked', lambda _b: self.save())
        menu = Gio.Menu()
        menu.append('Settings', 'win.settings')
        menu.append('About', 'win.about')
        menu_button = Gtk.MenuButton(icon_name='open-menu-symbolic', menu_model=menu, tooltip_text='Menu')
        header.pack_end(menu_button)
        header.pack_end(self.save_button)
        header.pack_end(self.save_as_button)
        self.set_titlebar(header)

        # the same things from the keyboard; Save and Save as are enabled with their buttons
        self.actions = {}
        for name, accel, handler in SHORTCUTS:
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', lambda _a, _p, h=handler: getattr(self, h)())
            self.add_action(action)
            self.actions[name] = action
            app.set_accels_for_action('win.' + name, [accel])
            widget = {'open': open_button, 'save': self.save_button, 'save-as': self.save_as_button}.get(name)
            if widget:
                widget.set_tooltip_text(Gtk.accelerator_get_label(*Gtk.accelerator_parse(accel)[1:]))
        self.actions['save'].set_enabled(False)
        self.actions['save-as'].set_enabled(False)
        about = Gio.SimpleAction.new('about', None)
        about.connect('activate', lambda _a, _p: self.show_about())
        self.add_action(about)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body = Gtk.Box(vexpand=True)
        root.append(body)
        self.status = label('', 'statusbar')
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        root.append(self.status)
        self.set_child(root)

        self.sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.sidebar.add_css_class('sidebar')
        self.sidebar.set_size_request(230, -1)
        side_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        side_scroll.set_child(self.sidebar)
        side_scroll.set_hexpand(False)  # the count labels inside would otherwise make it stretch
        body.append(side_scroll)

        self.page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, hexpand=True)
        margins(self.page, 16)
        page_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, hexpand=True)
        page_scroll.set_child(self.page)
        body.append(page_scroll)

        self.detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        margins(self.detail, 14)
        self.card_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.edit_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.detail.append(self.card_box)
        self.detail.append(self.edit_box)
        detail_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        detail_scroll.add_css_class('detail')
        detail_scroll.set_size_request(440, -1)
        detail_scroll.set_child(self.detail)
        body.append(detail_scroll)

        # what fills the areas of the window
        self.game_files = GameFiles(self)
        self.inventory_page = InventoryPage(self)
        self.item_panel = ItemPanel(self)
        self.pilot_page = PilotPage(self)
        self.perks_page = PerksPage(self)
        self.devices_page = DevicesPage(self)
        self.unlock_pages = UnlockPages(self)

        self.connect('close-request', self.on_close_request)
        # a search left running would keep the program going until it was over
        self.connect('destroy', lambda _w: self.item_panel.cancel_search())
        self.show_welcome()

    @property
    def open_doc(self) -> Document:
        if self.doc is None:
            raise RuntimeError('no save is open')
        return self.doc

    @property
    def selected_slot(self) -> Key:
        if self.selection is None:
            raise RuntimeError('no item is selected')
        return self.selection

    @property
    def selected_inventory(self) -> Inventory:
        inventory = self.open_doc.inventory(self.selected_slot[0])
        if inventory is None:
            raise RuntimeError('the selected item is in no inventory')
        return inventory

    def set_status(self, text: str, error: bool = False) -> None:
        self.status.set_label(text)
        if error:
            self.status.add_css_class('error')
        else:
            self.status.remove_css_class('error')

    def update_title(self) -> None:
        self.actions['save'].set_enabled(bool(self.doc and self.doc.dirty))
        self.actions['save-as'].set_enabled(bool(self.doc))
        if not self.doc:
            self.subtitle_label.set_label('No save open')
            return
        self.subtitle_label.set_label('%s%s' % (self.doc.name, '  •  unsaved changes' if self.doc.dirty else ''))
        self.save_button.set_sensitive(self.doc.dirty)
        self.save_as_button.set_sensitive(True)

    @contextlib.contextmanager
    def filling(self) -> Iterator[None]:
        """Ignores the signals of the controls filled in meanwhile, which are not changes."""
        self.busy = True
        try:
            yield
        finally:
            self.busy = False

    def mark_dirty(self) -> None:
        self.open_doc.dirty = True
        self.update_title()

    def show_icon(self) -> bool:
        """Gives the window the game's inventory icon, for the taskbar and the list of windows
        to show, once the icons are there and the window is on screen. Whether that does
        anything is up to the desktop: X11 and Windows take it, and Wayland only where the
        compositor has a way to be told, the others going by the installed .desktop file."""
        surface = self.get_surface()
        texture = self.icons.application()
        if not surface or not texture:
            return False
        surface.set_icon_list([texture])
        return True

    def show_welcome(self) -> None:
        clear(self.page)
        clear(self.sidebar)
        clear(self.card_box)
        clear(self.edit_box)
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10,
            valign=Gtk.Align.CENTER,
            halign=Gtk.Align.CENTER,
            vexpand=True,
        )
        box.add_css_class('welcome')
        box.append(label('EVERSPACE 2 SAVE EDITOR', 'big', xalign=0.5))
        box.append(label('Version %s' % __version__, 'dim', xalign=0.5, selectable=True))
        button = Gtk.Button(label='Open a save…', halign=Gtk.Align.CENTER)
        button.add_css_class('accent')
        button.add_css_class('suggested-action')
        button.connect('clicked', lambda _b: self.choose_file())
        box.append(button)
        about = label('%s\n%s' % (COPYRIGHT, LICENCE), 'dim small', xalign=0.5)
        about.set_justify(Gtk.Justification.CENTER)
        box.append(about)
        self.page.append(box)

    def start_folder(self) -> str | None:
        """Where the Open dialog starts: the folder chosen for it in the settings, else next to
        the open save, else where the last one was, else where the game keeps them, which is
        looked for once and then remembered."""
        fixed = config.load().get('open_directory')
        if isinstance(fixed, str) and os.path.isdir(fixed):
            return fixed
        if self.doc:
            return os.path.dirname(self.doc.path)
        folder = config.load().get('save_directory')
        if not isinstance(folder, str) or not os.path.isdir(folder):
            folder = find_save_dir()
            if folder:
                config.update(save_directory=folder)
        return folder

    def choose_file(self) -> None:
        dialog = Gtk.FileDialog(title='Open an Everspace 2 save')
        folder = self.start_folder()
        if folder:
            dialog.set_initial_folder(Gio.File.new_for_path(folder))
        saves = Gtk.FileFilter()
        saves.set_name('Everspace 2 saves')
        saves.add_pattern('*.sav')
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(saves)
        dialog.set_filters(filters)

        def done(dlg: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
            try:
                file = dlg.open_finish(result)
            except GLib.Error:
                return
            self.confirm_discard(lambda: self.open_path(file.get_path()))

        dialog.open(self, None, done)

    def open_path(self, path: str, then: Callable[[], object] | None = None) -> None:
        self.set_status('Loading %s…' % os.path.basename(path))
        self.set_sensitive(False)

        def work() -> None:
            try:
                doc = Document.load(path)
                error = None
            except Exception as e:  # noqa: BLE001 (shown to the user; a bad file must not take the window down)
                doc, error = None, e
            GLib.idle_add(finish, doc, error)

        def finish(doc: Document | None, error: Exception | None) -> bool:
            self.set_sensitive(True)
            if error or doc is None:
                self.set_status('Could not open %s: %s' % (os.path.basename(path), error), error=True)
            else:
                self.doc = doc
                self.selection = None
                self.build_sidebar()
                self.inventory_page.show(doc.inventories[0] if doc.inventories else None)
                self.update_title()
                self.set_status('Opened %s' % path)
                config.update(save_directory=os.path.dirname(os.path.abspath(path)))
                if then:
                    then()
            return False

        threading.Thread(target=work, daemon=True).start()

    def choose_save_as(self) -> None:
        dialog = Gtk.FileDialog(title='Save as')
        dialog.set_initial_folder(Gio.File.new_for_path(os.path.dirname(self.open_doc.path)))
        dialog.set_initial_name(self.open_doc.name + ' edited.sav')

        def done(dlg: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
            try:
                file = dlg.save_finish(result)
            except GLib.Error:
                return
            chosen = file.get_path()
            path = chosen if chosen.lower().endswith('.sav') else chosen + '.sav'
            # the dialog asked about replacing the name as it was typed, not with .sav added
            if path != chosen and os.path.exists(path):
                self.confirm_replace(path, lambda: self.save(path))
            else:
                self.save(path)

        dialog.save(self, None, done)

    def confirm_replace(self, path: str, proceed: Callable[[], object]) -> None:
        self.confirm(
            'Replace %s?' % os.path.basename(path),
            'A file of that name is already in %s. Saving replaces it.' % os.path.dirname(path),
            'Replace',
            proceed,
        )

    def confirm(self, message: str, detail: str, verb: str, proceed: Callable[[], object]) -> None:
        """Asks before doing something that cannot be undone; Cancel is the default."""
        dialog = Gtk.AlertDialog(message=message, detail=detail, buttons=['Cancel', verb])
        dialog.set_cancel_button(0)
        dialog.set_default_button(0)

        def done(dlg: Gtk.AlertDialog, result: Gio.AsyncResult) -> None:
            try:
                if dlg.choose_finish(result) == 1:
                    proceed()
            except GLib.Error:
                pass

        dialog.choose(self, None, done)

    def save(self, path: str | None = None, then: Callable[[], object] | None = None) -> None:
        doc = self.open_doc
        self.set_status('Saving…')
        self.set_sensitive(False)

        def work() -> None:
            try:
                backup = doc.write(path, config.backup_directory(), config.backups_kept(), config.backups())
                error = None
            except Exception as e:  # noqa: BLE001 (shown to the user)
                backup, error = None, e
            GLib.idle_add(finish, backup, error)

        def finish(backup: str | None, error: Exception | None) -> bool:
            self.set_sensitive(True)
            if error:
                self.set_status('Saving failed: %s' % error, error=True)
            else:
                self.update_title()
                self.set_status('Saved %s%s' % (doc.path, '  (previous version kept as %s)' % backup if backup else ''))
                config.update(save_directory=os.path.dirname(os.path.abspath(doc.path)))
                if then:
                    then()
            return False

        threading.Thread(target=work, daemon=True).start()

    def confirm_discard(self, proceed: Callable[[], object]) -> None:
        if not self.doc or not self.doc.dirty:
            proceed()
            return
        self.confirm(
            'Discard unsaved changes?',
            '%s has changes that have not been saved.' % self.doc.name,
            'Discard',
            proceed,
        )

    def on_close_request(self, _window: Gtk.Window) -> bool:
        if self.item_panel.search_cancel:
            # as the search's own dialog would: the search stops, and the window can then be closed
            self.item_panel.cancel_search()
            return True
        if self.doc and self.doc.dirty:

            def close() -> None:
                self.open_doc.dirty = False
                self.close()

            self.confirm_discard(close)
            return True
        return False

    def show_settings(self) -> None:
        from .settings import SettingsDialog

        SettingsDialog(self).present()

    def show_about(self) -> None:
        about = Gtk.AboutDialog(
            transient_for=self,
            modal=True,
            program_name='Everspace 2 Save Editor',
            version=__version__,
            copyright=COPYRIGHT,
            license_type=Gtk.License.GPL_3_0,
        )
        texture = self.icons.application()
        if texture:
            about.set_logo(texture)
        about.add_css_class('es2')
        close_on_escape(about)
        about.present()

    def redraw(self) -> None:
        """Draws the open save again on the page that was on show, after what the editor takes
        from the game has changed."""
        self.show_icon()
        if not self.doc:
            return
        shown = next((key for key, button in self.nav_buttons.items() if button.get_active()), None)
        self.build_sidebar()
        handler = self.nav_handlers.get(shown) if shown else None
        if handler:
            handler()
        else:
            self.inventory_page.show(self.doc.inventories[0] if self.doc.inventories else None)

    def build_sidebar(self) -> None:
        clear(self.sidebar)
        self.nav_buttons: dict[str, Gtk.ToggleButton] = {}
        self.nav_handlers: dict[str, Callable[..., object]] = {}
        group = None

        def add(key: str, text: str, count: int | None, handler: Callable[..., object]) -> None:
            nonlocal group
            button = Gtk.ToggleButton()
            button.add_css_class('nav')
            row = Gtk.Box(spacing=6)
            row.append(label(text))
            if count is not None:
                c = label(str(count), 'count', xalign=1.0)
                c.set_hexpand(True)
                row.append(c)
            button.set_child(row)
            if group:
                button.set_group(group)
            group = group or button
            button.connect('clicked', lambda b: b.get_active() and handler())
            self.sidebar.append(button)
            self.nav_buttons[key] = button
            self.nav_handlers[key] = handler

        # the sections of fixed length come first, so that the number of ships moves nothing
        self.sidebar.append(label('PILOT', 'heading'))
        add('pilot', 'Credits and level', None, self.pilot_page.show)
        if perks.PLAYER_PERKS in self.open_doc.properties or perks.COMPANION_PERKS in self.open_doc.properties:
            add('perks', 'Perks', None, self.perks_page.show)
        if self.game and devices.UNLOCKED in self.open_doc.properties:
            add('devices', 'Devices', None, self.devices_page.show)
        if self.game and unlocks.BLUEPRINTS in self.open_doc.properties:
            add('blueprints', 'Blueprints', None, self.unlock_pages.show_blueprints)
        if self.game.ship_colors and any(k in self.open_doc.properties for k in (unlocks.COLORS, unlocks.DECALS)):
            add('colours', 'Ship colours and decals', None, self.unlock_pages.show_colours)
        if self.game.ship_modules and unlocks.MODULES in self.open_doc.properties:
            add('modules', 'Ship modules', None, self.unlock_pages.show_modules)
        self.sidebar.append(label('HOME BASE', 'heading'))
        for inv in self.open_doc.inventories:
            if inv.kind != 'ship':
                add(inv.key, inv.label, len(inv.items()), lambda inv=inv: self.inventory_page.show(inv))
        ships = [inv for inv in self.open_doc.inventories if inv.kind == 'ship']
        if ships:
            self.sidebar.append(label('SHIPS', 'heading'))
            for inv in ships:
                add(inv.key, inv.label, len(inv.items()), lambda inv=inv: self.inventory_page.show(inv))

    def sync_nav(self, key: str) -> None:
        button = self.nav_buttons.get(key)
        if button and not button.get_active():
            button.set_active(True)

    def select(self, key: Key | None) -> None:
        for tile in self.tiles.values():
            tile.remove_css_class('selected')
        self.selection = key
        chosen = self.tiles.get(key) if key else None
        if chosen:
            chosen.add_css_class('selected')
        self.item_panel.build_detail()

    def selected_item(self) -> Item | None:
        if not self.selection or not self.doc:
            return None
        inv = self.doc.inventory(self.selection[0])
        if inv is None:
            return None
        lst = inv.slots.get(self.selection[1], [])
        if self.selection[2] >= len(lst) or items.is_empty(lst[self.selection[2]]):
            return None
        return lst[self.selection[2]]

    def dropdown(
        self, values: Sequence[Any], names: Sequence[str], current: Any, on_change: Callable[..., object]
    ) -> Gtk.DropDown:
        if current not in values:
            values = [current] + list(values)
            names = [str(current)] + list(names)
        dd = Gtk.DropDown.new_from_strings(list(names))
        dd.set_selected(values.index(current))
        dd.set_hexpand(True)

        def changed(d: Gtk.DropDown, _p: object) -> None:
            if not self.busy:
                on_change(values[d.get_selected()])

        dd.connect('notify::selected', changed)
        return dd

    def spin(
        self,
        value: float,
        low: float,
        high: float,
        on_change: Callable[..., object],
        digits: int = 0,
        step: float = 1,
    ) -> Gtk.SpinButton:
        sb = Gtk.SpinButton.new_with_range(low, high, step)
        sb.set_digits(digits)
        sb.set_value(value)
        sb.set_hexpand(True)

        def changed(s: Gtk.SpinButton) -> None:
            if not self.busy:
                on_change(s.get_value() if digits else round(s.get_value()))

        sb.connect('value-changed', changed)
        return sb

    def switch(self, active: bool, on_change: Callable[[bool], object]) -> Gtk.Switch:
        sw = Gtk.Switch()
        sw.set_active(active)
        sw.set_halign(Gtk.Align.START)
        sw.set_hexpand(True)

        def changed(s: Gtk.Switch, _p: object) -> None:
            if not self.busy:
                on_change(s.get_active())

        sw.connect('notify::active', changed)
        return sw

    def row(self, title: str, widget: Gtk.Widget) -> Gtk.Box:
        box = Gtk.Box(spacing=8)
        box.add_css_class('editor-row')
        box.append(label(title, 'field'))
        box.append(widget)
        return box

    def start_page(self, key: str, title: str, note: str = '') -> None:
        self.inventory = None
        self.tiles = {}
        self.selection = None
        clear(self.page)
        self.item_panel.build_detail()
        self.sync_nav(key)
        self.page.append(label(title, 'page-title'))
        if note:
            self.page.append(label(note, 'dim', wrap=True))
