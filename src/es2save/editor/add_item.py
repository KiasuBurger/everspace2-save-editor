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

"""The dialog that picks an item to put into a slot."""

from typing import TYPE_CHECKING

from .. import itemrules, items
from ..items import Inventory
from .gtk import Gtk
from .widgets import clear, close_on_escape, label, margins, picture

if TYPE_CHECKING:
    from .window import EditorWindow


class AddItemDialog(Gtk.Window):
    """Pick an item template to put into a slot."""

    def __init__(self, window: EditorWindow, inventory: Inventory, array: str, index: int | None) -> None:
        super().__init__(transient_for=window, modal=True, title='Add an item')
        self.add_css_class('es2')
        close_on_escape(self)
        self.window = window
        self.inventory = inventory
        self.array = array
        self.index = index
        self.set_default_size(560, 640)
        game = window.game
        self.templates = itemrules.addable_templates(game, inventory.kind, array, inventory.slots[array])
        self.chosen: str | None = None

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        margins(box, 14)
        where = inventory.label if array == items.CARGO else '%s, %s' % (inventory.label, items.SLOT_NAMES[array])
        box.append(label('ADD TO %s' % where.upper(), 'section-title'))
        self.search = Gtk.SearchEntry(placeholder_text='Search by name, type or ID')
        box.append(self.search)

        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.search.connect('search-changed', lambda _e: self.listbox.invalidate_filter())
        # the search takes Escape before the dialog does: it empties the search, then closes
        self.search.connect('stop-search', lambda e: e.set_text('') if e.get_text() else self.close())
        for t in self.templates:
            row = Gtk.Box(spacing=10)
            texture = window.icons.item(game, t)
            row.append(picture(texture, 34) if texture else Gtk.Box(width_request=34))
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            text.append(label(game.name(t)))
            text.append(label('%s  •  %s' % (game.category_name(t) or 'Item', t), 'dim'))
            row.append(text)
            line = Gtk.ListBoxRow()
            line.set_child(row)
            line.template = t
            self.listbox.append(line)
        self.listbox.set_filter_func(self.visible)
        self.listbox.connect('row-selected', self.on_selected)
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(self.listbox)
        box.append(scroll)

        self.options = Gtk.Box(spacing=8)
        box.append(self.options)
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        cancel = Gtk.Button(label='Cancel')
        cancel.connect('clicked', lambda _b: self.close())
        self.add_button = Gtk.Button(label='Add', sensitive=False)
        self.add_button.add_css_class('accent')
        self.add_button.add_css_class('suggested-action')
        self.add_button.connect('clicked', lambda _b: self.add())
        buttons.append(cancel)
        buttons.append(self.add_button)
        box.append(buttons)
        self.set_child(box)

    def visible(self, row: Gtk.ListBoxRow) -> bool:
        words = self.search.get_text().lower().split()
        game = self.window.game
        hay = ('%s %s %s' % (row.template, game.name(row.template), game.category_name(row.template))).lower()
        return all(w in hay for w in words)

    def on_selected(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        clear(self.options)
        self.chosen = row.template if row else None
        self.add_button.set_sensitive(row is not None)
        if row is None:
            return
        chosen: str = row.template
        game = self.window.game
        rarities = game.rarities(chosen)
        equipment = game.is_equipment(chosen)
        if rarities[0] == 'Legendary':
            self.rarity = 'Legendary'
        elif equipment and 'Superior' in rarities:
            self.rarity = 'Superior'
        elif equipment and rarities[-1] != 'Ascendant':
            self.rarity = rarities[-1]
        else:
            self.rarity = rarities[0]
        self.level = self.window.open_doc.scalar('PlayerLevel') or 1
        self.amount = 1
        self.options.append(label('Rarity', 'dim'))
        dd = Gtk.DropDown.new_from_strings(rarities)
        dd.set_selected(rarities.index(self.rarity))
        dd.connect('notify::selected', lambda d, _p: setattr(self, 'rarity', rarities[d.get_selected()]))
        self.options.append(dd)
        if itemrules.is_device(game, chosen):
            pass  # at the player's level, as the game keeps every device
        elif not itemrules.stacks(game, chosen):
            self.options.append(label('Level', 'dim'))
            sb = Gtk.SpinButton.new_with_range(1, 99, 1)
            sb.set_value(self.level)
            sb.connect('value-changed', lambda s: setattr(self, 'level', int(s.get_value())))
            self.options.append(sb)
        else:
            self.options.append(label('Amount', 'dim'))
            sb = Gtk.SpinButton.new_with_range(1, 99999999, 1)
            sb.set_value(1)
            sb.connect('value-changed', lambda s: setattr(self, 'amount', int(s.get_value())))
            self.options.append(sb)

    def add(self) -> None:
        window, chosen = self.window, self.chosen
        if chosen is None:
            return
        game = window.game
        equipment = game.is_equipment(chosen)
        item = items.create(
            items.exemplar(window.open_doc.save),
            chosen,
            game,
            self.rarity,
            level=1 if itemrules.stacks(game, chosen) else self.level,
            amount=self.amount,
            position_value=1.0 if equipment else 0.5,
        )
        note = window.inventory_page.place_new_item(self.inventory, self.array, self.index, item)
        window.set_status('Added %s' % game.name(chosen) + ('. ' + note if note else ''))
        self.close()
