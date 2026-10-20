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

"""The page of the devices the pilot has unlocked, how far each is upgraded, and the tokens
to upgrade them with."""

from typing import TYPE_CHECKING

from .. import devices, items
from .gtk import Gtk
from .widgets import INT_MAX, label, picture

if TYPE_CHECKING:
    from .window import EditorWindow


class DevicesPage:
    def __init__(self, window: EditorWindow) -> None:
        self.window = window

    def show(self) -> None:
        """Each device the pilot can unlock, whether it is, and its level. A device that is
        equipped stays unlocked."""
        self.window.start_page(
            'devices',
            'DEVICES',
            'Mastery modes can be chosen in the game.',
        )
        game, props = self.window.game, self.window.open_doc.properties
        have = set(devices.unlocked(props))
        equipped = {
            items.template(it): inv.label
            for inv in self.window.open_doc.inventories
            if inv.kind == 'ship'
            for it in inv.slots.get(items.DEVICES, [])
            if not items.is_empty(it)
        }
        levels = list(range(1, devices.MAX_LEVEL + 1))
        names = [str(n) for n in levels[:-1]] + ['%d (mastered)' % devices.MAX_LEVEL]
        grid = Gtk.Grid(column_spacing=10, row_spacing=6)
        row = 0
        with self.window.filling():
            tokens = self.window.spin(devices.tokens(props), 0, INT_MAX, self.set_tokens)
            tokens.set_size_request(220, -1)
            tokens.set_hexpand(False)
            self.window.page.append(self.window.row('Upgrade tokens', tokens))
            for title, members in devices.sections(game):
                grid.attach(label(title.upper(), 'slot-label'), 0, row, 2, 1)
                row += 1
                for device in members:
                    chip = Gtk.ToggleButton()
                    chip.add_css_class('perk')
                    inner = Gtk.Box(spacing=8)
                    texture = self.window.icons.item(game, device)
                    if texture:
                        inner.append(picture(texture, 30, 'glyph'))
                    inner.append(label(game.name(device)))
                    chip.set_child(inner)
                    chip.set_size_request(260, -1)
                    chip.set_active(device in have)
                    chip.set_tooltip_text(game.description(device))
                    pick = self.window.dropdown(
                        levels, names, devices.level(props, device), lambda v, d=device: self.set_level(d, v)
                    )
                    pick.set_hexpand(False)
                    pick.set_valign(Gtk.Align.CENTER)
                    pick.set_sensitive(device in have)
                    chip.connect('toggled', lambda b, d=device, p=pick: self.set_unlocked(d, b, p, equipped.get(d)))
                    grid.attach(chip, 0, row, 1, 1)
                    grid.attach(pick, 1, row, 1, 1)
                    row += 1
        self.window.page.append(grid)

    def set_unlocked(self, device: str, chip: Gtk.ToggleButton, level: Gtk.DropDown, ship: str | None) -> None:
        if self.window.busy:
            return
        on = chip.get_active()
        if not on and ship:
            with self.window.filling():
                chip.set_active(True)
            self.window.set_status(
                '%s is equipped on the %s and must stay unlocked' % (self.window.game.name(device), ship), error=True
            )
            return
        devices.set_unlocked(self.window.open_doc.properties, device, on)
        with self.window.filling():
            level.set_selected(devices.level(self.window.open_doc.properties, device) - 1)
        level.set_sensitive(on)
        self.window.mark_dirty()

    def set_level(self, device: str, value: int) -> None:
        devices.set_level(self.window.open_doc.properties, device, value)
        self.window.mark_dirty()

    def set_tokens(self, count: float) -> None:
        devices.set_tokens(self.window.open_doc.properties, int(count))
        self.window.mark_dirty()
