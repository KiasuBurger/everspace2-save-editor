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

"""The pieces the editor is built from: the game's icons, a slot's tile, and labels and pictures."""

import os
from collections.abc import Callable
from typing import TYPE_CHECKING

from .. import itemrules, items
from ..gamedata import GameData
from ..items import Inventory, Item
from . import APP_ICON, view
from .gtk import Gdk, GLib, Gtk, Pango

if TYPE_CHECKING:
    from .window import EditorWindow


Key = tuple[str, str, int]  # of a slot: (inventory key, array, index)


TILE = 86


INT_MAX = 2147483647


class IconStore:
    """Textures from the extracted game icons; every lookup may come back empty."""

    def __init__(self, directory: str | None) -> None:
        self.directory = directory
        self._cache: dict[str, Gdk.Texture | None] = {}

    def _load(self, relative: str) -> Gdk.Texture | None:
        if relative not in self._cache:
            texture = None
            if self.directory:
                path = os.path.join(self.directory, relative)
                if os.path.exists(path):
                    try:
                        texture = Gdk.Texture.new_from_filename(path)
                    except GLib.Error:
                        texture = None
            self._cache[relative] = texture
        return self._cache[relative]

    def application(self) -> Gdk.Texture | None:
        """The icon the editor goes by: the game's inventory menu icon."""
        return self._load(APP_ICON)

    def item(self, game: GameData, template: str) -> Gdk.Texture | None:
        icon = game.item(template).get('icon')
        if not icon:
            return None
        # the materials from dismantling have theirs with the crafting menu's icons
        return self._load('Items/%s.png' % icon) or self._load('Icons/Crafting/%s.png' % icon)

    def pattern(self, kind: str) -> Gdk.Texture | None:
        return self._load('Elements/Items/Backgrounds/illu_itembg_%s_86x86_2x.png' % kind)

    def type(self, name: str) -> Gdk.Texture | None:
        return self._load('Types/icon_type_%s_46x46_2x.png' % name)

    def named(self, icon: str | None) -> Gdk.Texture | None:
        """An item icon by its own name, for the things that are not items (decals)."""
        return self._load('Items/%s.png' % icon) if icon else None


def swatch(color: str) -> Gdk.Texture:
    """A texture of one '#rrggbb' colour."""
    rgb = bytes.fromhex(color.lstrip('#')[:6]) + b'\xff'
    return Gdk.MemoryTexture.new(1, 1, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(rgb), 4)


def picture(texture: Gdk.Texture, size: int, css: str | None = None) -> Gtk.Image:
    """A texture drawn at a fixed size. Gtk.Image is used because it takes its size from
    pixel_size alone; a Gtk.Picture would ask for the texture's own (double) size."""
    image = Gtk.Image.new_from_paintable(texture)
    image.set_pixel_size(size)
    image.set_halign(Gtk.Align.CENTER)
    image.set_valign(Gtk.Align.CENTER)
    if css:
        image.add_css_class(css)
    return image


def label(
    text: str, css: str | None = None, wrap: bool = False, xalign: float = 0.0, selectable: bool = False
) -> Gtk.Label:
    lab = Gtk.Label(label=text, xalign=xalign)
    if wrap:
        lab.set_wrap(True)
        lab.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lab.set_max_width_chars(40)
    if selectable:
        lab.set_selectable(True)
    for c in (css or '').split():
        lab.add_css_class(c)
    return lab


def margins(widget: Gtk.Widget, size: int) -> None:
    """The same margin on every side."""
    widget.set_margin_top(size)
    widget.set_margin_bottom(size)
    widget.set_margin_start(size)
    widget.set_margin_end(size)


def soon(call: Callable[[], object]) -> None:
    """Calls something once the handler that is running has returned."""

    def run() -> bool:
        call()
        return False

    GLib.idle_add(run)


def clear(box: Gtk.Box) -> None:
    child = box.get_first_child()
    while child:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt


def close_on_escape(window: Gtk.Window) -> None:
    """Closes a dialog from the keyboard, which GTK leaves to the dialogs it makes itself."""
    controller = Gtk.ShortcutController()
    controller.add_shortcut(
        Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string('Escape'), action=Gtk.NamedAction.new('window.close'))
    )
    window.add_controller(controller)


class Tile(Gtk.Button):
    """One inventory slot, drawn like the game's: rarity border and tint, the item's icon, its
    level (or amount) in the corner."""

    def __init__(self, window: EditorWindow, inventory: Inventory, array: str, index: int) -> None:
        super().__init__()
        self.window = window
        self.inventory = inventory
        self.array = array
        self.index = index
        self.add_css_class('tile')
        self.set_size_request(TILE, TILE)
        self.set_halign(Gtk.Align.START)
        self.set_valign(Gtk.Align.START)
        self.connect('clicked', lambda _b: window.inventory_page.on_tile_clicked(self))
        self.update()

    @property
    def item(self) -> Item:
        return self.inventory.slots[self.array][self.index]

    @property
    def key(self) -> Key:
        return (self.inventory.key, self.array, self.index)

    def update(self) -> None:
        game, icons = self.window.game, self.window.icons
        item = self.item
        for c in list(self.get_css_classes()):
            if c.startswith('rarity-') or c == 'empty':
                self.remove_css_class(c)
        if items.is_empty(item):
            self.add_css_class('empty')
            self.set_child(None)
            self.set_tooltip_text('Empty slot: click to add an item')
            return
        tpl = items.template(item)
        self.add_css_class('rarity-' + items.rarity(item))
        overlay = Gtk.Overlay()
        overlay.set_size_request(TILE - 4, TILE - 4)
        pattern = icons.pattern(view.tile_background(game, tpl))
        overlay.set_child(picture(pattern, TILE - 4, 'pattern') if pattern else Gtk.Box())
        texture = icons.item(game, tpl)
        if texture:
            overlay.add_overlay(picture(texture, TILE - 14))
        else:
            name = label(game.name(tpl), 'placeholder', wrap=True, xalign=0.5)
            name.set_max_width_chars(9)
            name.set_justify(Gtk.Justification.CENTER)
            name.set_halign(Gtk.Align.CENTER)
            name.set_valign(Gtk.Align.CENTER)
            overlay.add_overlay(name)
        corner = label(
            str(items.get(item, 'Amount', 1))
            if itemrules.is_stackable(game, item)
            else str(items.get(item, 'ItemLevel', '')),
            'level',
            xalign=0.5,
        )
        corner.set_halign(Gtk.Align.END)
        corner.set_valign(Gtk.Align.END)
        overlay.add_overlay(corner)
        marks = Gtk.Box(spacing=2)
        marks.set_halign(Gtk.Align.START)
        marks.set_valign(Gtk.Align.START)
        grade = view.GRADE_MARK.get(items.grade(item))
        if grade:
            marks.append(label(grade, 'mark'))
        if items.has_chaos(item):
            marks.append(label('C', 'mark chaos'))
        elif items.catalysts(item):
            marks.append(label('+', 'mark catalyst'))
        overlay.add_overlay(marks)
        self.set_child(overlay)
        self.set_tooltip_text(
            '%s\n%s%s' % (view.display_name(game, item), items.rarity(item), ', ' + items.grade(item) if grade else '')
        )
