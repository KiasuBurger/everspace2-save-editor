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

"""The page that shows an inventory: what a ship has equipped, its passives, and the grid of its cargo."""

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

from .. import itemrules, items, perks
from ..items import Inventory, Item
from . import view
from .add_item import AddItemDialog
from .gtk import Gtk
from .widgets import Key, Tile, clear, label, soon

if TYPE_CHECKING:
    from .window import EditorWindow


FILTERS = [
    ('all', 'All'),
    ('weapons', 'Weapons'),
    ('modules', 'Modules'),
    ('consumables', 'Consumables'),
    ('catalysts', 'Catalysts'),
    ('other', 'Other'),
]


class InventoryPage:
    def __init__(self, window: EditorWindow) -> None:
        self.window = window
        self.filter_text = ''
        self.filter_category = 'all'

    def show(self, inventory: Inventory | None) -> None:
        self.window.inventory = inventory
        self.window.tiles = {}
        clear(self.window.page)
        if inventory is None:
            return
        self.window.sync_nav(inventory.key)
        top = Gtk.Box(spacing=10)
        top.append(label(inventory.label.upper(), 'page-title'))
        self.count_label = label(view.cargo_summary(inventory), 'dim')
        top.append(self.count_label)
        spacer = Gtk.Box(hexpand=True)
        top.append(spacer)
        if inventory.kind in ('station', 'ship'):
            search = Gtk.SearchEntry(placeholder_text='Filter by name, type or rarity')
            search.set_text(self.filter_text)
            search.set_size_request(240, -1)
            search.connect('search-changed', self.on_search)
            top.append(search)
            dropdown = Gtk.DropDown.new_from_strings([name for _, name in FILTERS])
            dropdown.set_selected([k for k, _ in FILTERS].index(self.filter_category))
            dropdown.connect('notify::selected', self.on_filter)
            top.append(dropdown)
        add = Gtk.Button(label='Add item…')
        add.connect('clicked', lambda _b: self.add_item_dialog(inventory, items.CARGO))
        if inventory.is_full():
            add.set_sensitive(False)
            add.set_tooltip_text('Every cargo slot is taken')
        top.append(add)
        self.window.page.append(top)

        if inventory.kind == 'ship' and self.window.game:
            self.build_passives(inventory)
        equipment = {array: (name, lst) for array, name, lst in inventory.equipment()}
        if equipment:
            self.window.page.append(label('EQUIPPED', 'section-title'))
            # three rows, grouped as in the game: weapons, modules, then devices and consumables
            for arrays in (
                ('PrimaryWeapons', 'SecondaryWeapons'),
                ('EnergyCores', 'Shields', 'Platings', 'Sensors', 'thrusters', 'CargoUnits'),
                ('Devices', 'Consumables'),
            ):
                line = Gtk.Box(spacing=16)
                for array in arrays:
                    if array not in equipment:
                        continue
                    name, lst = equipment[array]
                    group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
                    group.append(label(name.upper(), 'slot-label'))
                    row = Gtk.Box(spacing=6)
                    for index in range(len(lst)):
                        row.append(self.make_tile(inventory, array, index))
                    group.append(row)
                    line.append(group)
                if line.get_first_child():
                    self.window.page.append(line)
            self.window.page.append(label('CARGO', 'section-title'))

        if inventory.kind == 'resources':
            # the game's own page: sections with a note on where their things come from
            for title, note, indices in view.resource_sections(self.window.game, inventory):
                head = Gtk.Box(spacing=10)
                head.add_css_class('resource-head')
                name = label(title.upper(), 'section-title')
                name.set_hexpand(True)
                head.append(name)
                head.append(label(note, 'section-note'))
                self.window.page.append(head)
                self.window.page.append(self.tile_grid(inventory, indices))
        else:
            self.grid = self.tile_grid(inventory, range(len(inventory.cargo)))
            self.grid.set_filter_func(self.tile_visible)
            self.window.page.append(self.grid)

        if (
            self.window.selection
            and self.window.selection[0] == inventory.key
            and self.window.selection in self.window.tiles
        ):
            self.window.select(self.window.selection)
        else:
            self.window.select(None)

    def build_passives(self, inventory: Inventory) -> None:
        """The ship's own perks: the passive of its class and the ones it rolled, each of which
        can be swapped for another from the class's pool."""
        game = self.window.game
        ship = items.get(inventory.ship or {}, 'ShipItemState')
        if not ship or not perks.passive_pool(game, ship):
            return
        head = Gtk.Box(spacing=10)
        head.append(label('PASSIVES', 'section-title'))
        have, usual = perks.passives(game, ship), perks.passive_count(ship)
        head.append(
            label(
                '%d, where the game gives a ship of this tier %d' % (len(have), usual) if len(have) != usual else '',
                'dim',
            )
        )
        self.window.page.append(head)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.add_css_class('perk-list')
        fixed = perks.class_passive(ship)
        if fixed:
            box.append(self.perk_line(label(game.attribute_name(fixed), 'perk-name'), self.passive_text(ship, fixed)))
        for name in have:
            choices = itemrules.swap_choices(game, ship, name)
            pick = self.window.dropdown(
                choices,
                [game.attribute_name(a) for a in choices],
                name,
                lambda new, old=name: self.swap_passive(inventory, old, new),
            )
            pick.set_hexpand(False)
            remove = Gtk.Button(label='×')
            remove.add_css_class('danger')
            remove.set_tooltip_text('Remove this passive')
            remove.connect('clicked', lambda _b, n=name: self.change_passive(inventory, items.remove_attribute, n))
            box.append(self.perk_line(pick, self.passive_text(ship, name), remove))
        spare = itemrules.addable_stats(game, ship)
        if spare:
            pick = self.window.dropdown(
                spare, [game.attribute_name(a) for a in spare], spare[0], lambda v: setattr(self, 'new_passive', v)
            )
            pick.set_hexpand(False)
            self.new_passive = spare[0]
            add = Gtk.Button(label='Add passive')
            add.connect(
                'clicked',
                lambda _b: self.change_passive(
                    inventory, items.add_attribute, self.new_passive, 0.5, list(game.attributes)
                ),
            )
            line = Gtk.Box(spacing=10)
            line.append(pick)
            line.append(add)
            box.append(line)
        self.window.page.append(box)

    def passive_text(self, ship: Item, name: str) -> str:
        value = self.window.game.range_value(items.template(ship), name, items.position(ship, name) or 0.0)
        return self.window.game.attribute_text(name, value) if value is not None else ''

    def perk_line(self, first: Gtk.Widget, text: str, last: Gtk.Widget | None = None) -> Gtk.Box:
        line = Gtk.Box(spacing=10)
        first.set_size_request(250, -1)
        first.set_valign(Gtk.Align.CENTER)
        line.append(first)
        if last:
            line.append(last)
        description = label(text, 'perk-text', wrap=True)
        description.set_max_width_chars(70)
        description.set_hexpand(True)
        line.append(description)
        return line

    def swap_passive(self, inventory: Inventory, old: str, new: str) -> None:
        if new != old:
            self.change_passive(inventory, items.replace_attribute, old, new, list(self.window.game.attributes))

    def change_passive(self, inventory: Inventory, change: Callable[..., object], *args: Any) -> None:
        change(items.get(inventory.ship or {}, 'ShipItemState'), *args)
        self.window.mark_dirty()
        # the page is rebuilt, which must not happen inside the handler of one of its widgets
        soon(lambda: self.show(self.window.open_doc.inventory(inventory.key)))

    def tile_grid(self, inventory: Inventory, indices: Iterable[int]) -> Gtk.FlowBox:
        grid = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            column_spacing=6,
            row_spacing=6,
            max_children_per_line=40,
            homogeneous=True,
            valign=Gtk.Align.START,
            halign=Gtk.Align.START,
        )
        for index in indices:
            child = Gtk.FlowBoxChild(focusable=False)
            child.set_child(self.make_tile(inventory, items.CARGO, index))
            grid.append(child)
        return grid

    def make_tile(self, inventory: Inventory, array: str, index: int) -> Tile:
        tile = Tile(self.window, inventory, array, index)
        self.window.tiles[tile.key] = tile
        return tile

    def tile_visible(self, child: Gtk.FlowBoxChild) -> bool:
        tile = child.get_child()
        if not self.filter_text and self.filter_category == 'all':
            return True
        if items.is_empty(tile.item):
            return False
        return view.matches(self.window.game, tile.item, self.filter_text, self.filter_category)

    def on_search(self, entry: Gtk.SearchEntry) -> None:
        self.filter_text = entry.get_text().strip()
        self.grid.invalidate_filter()

    def on_filter(self, dropdown: Gtk.DropDown, _param: object) -> None:
        self.filter_category = FILTERS[dropdown.get_selected()][0]
        self.grid.invalidate_filter()

    def on_tile_clicked(self, tile: Tile) -> None:
        if items.is_empty(tile.item):
            self.add_item_dialog(tile.inventory, tile.array, tile.index)
        else:
            self.window.select(tile.key)

    def fit_hold(self, inventory: Inventory | None, array: str) -> str:
        """After a change to a ship's cargo unit: gives the hold the number of slots the game
        would. Returns a note for the status line, '' when nothing changed."""
        if array != 'CargoUnits' or not self.window.game or inventory is None:
            return ''
        before = len(inventory.cargo)
        if not inventory.fit_cargo(self.window.game):
            return ''
        note = 'Cargo hold resized from %d to %d slots' % (before, len(inventory.cargo))
        capacity = inventory.cargo_capacity(self.window.game)
        if capacity is not None and len(inventory.cargo) > capacity:
            note += ' (%d too many: the ship holds %d, and nothing was removed)' % (
                len(inventory.cargo) - capacity,
                capacity,
            )
        self.window.set_status(note)
        return note

    def reload_page(self, select: Key | None = None) -> None:
        self.window.selection = select
        self.window.mark_dirty()
        self.window.build_sidebar()
        self.show(self.window.open_doc.inventory(self.window.inventory.key) if self.window.inventory else None)

    def add_item_dialog(self, inventory: Inventory, array: str, index: int | None = None) -> None:
        if not self.window.game:
            self.window.set_status('Adding items needs game_identifiers.json.', error=True)
            return
        if array == items.CARGO and index is None and inventory.is_full():
            self.window.set_status('%s has no free cargo slot' % inventory.label, error=True)
            return
        AddItemDialog(self.window, inventory, array, index).present()

    def place_new_item(self, inventory: Inventory, array: str, index: int | None, item: Item) -> str:
        lst = inventory.slots[array]
        if index is not None and index < len(lst) and items.is_empty(lst[index]):
            lst[index] = item
        else:
            index = inventory.add(item, array)
        self.window.selection = (inventory.key, array, index)
        note = self.fit_hold(inventory, array)
        self.window.mark_dirty()
        self.window.build_sidebar()
        self.show(self.window.open_doc.inventory(inventory.key))
        return note
