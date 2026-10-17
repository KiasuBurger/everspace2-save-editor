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

"""The page of player and companion perks."""

from typing import TYPE_CHECKING

from .. import perks
from .gtk import Gtk
from .widgets import clear, label, picture

if TYPE_CHECKING:
    from .window import EditorWindow


class PerksPage:
    def __init__(self, window: EditorWindow) -> None:
        self.window = window

    def show(self) -> None:
        self.window.inventory = None
        self.window.tiles = {}
        self.window.selection = None
        clear(self.window.page)
        self.window.item_panel.build_detail()
        self.window.sync_nav('perks')
        game, props = self.window.game, self.window.open_doc.properties
        self.window.page.append(label('PERKS', 'page-title'))
        with self.window.filling():
            if perks.PLAYER_PERKS in props and game:
                self.window.page.append(label('PLAYER PERKS', 'section-title'))
                chosen, unlocked = set(perks.selected(props)), perks.unlocked_sets(props)
                self.perk_notes = {}
                grid = Gtk.Grid(column_spacing=10, row_spacing=6)
                for n, choices in enumerate(perks.player_sets(game)):
                    grid.attach(label('Level %d' % ((n + 1) * perks.LEVELS_PER_SET), 'key'), 0, 2 * n, 1, 1)
                    group = None
                    for column, perk in enumerate(choices):
                        button = Gtk.ToggleButton()
                        button.add_css_class('perk')
                        inner = Gtk.Box(spacing=8)
                        texture = self.window.icons.item(game, perk)
                        if texture:
                            inner.append(picture(texture, 34, 'glyph'))
                        inner.append(label(game.name(perk)))
                        button.set_child(inner)
                        button.set_tooltip_text(game.description(perk))
                        button.set_size_request(230, -1)
                        if group:
                            button.set_group(group)
                        group = group or button
                        button.set_active(perk in chosen)
                        button.set_sensitive(n < unlocked)
                        button.connect('toggled', self.on_perk_toggled, perk, n)
                        grid.attach(button, column + 1, 2 * n, 1, 1)
                    picked = next((p for p in choices if p in chosen), None)
                    text = (
                        game.description(picked)
                        if picked
                        else 'Not chosen yet.'
                        if n < unlocked
                        else 'Opens at player level %d.' % ((n + 1) * 5)
                    )
                    note = label(text, 'perk-text', wrap=True)
                    note.set_max_width_chars(90)
                    self.perk_notes[n] = note
                    grid.attach(note, 1, 2 * n + 1, 3, 1)
                self.window.page.append(grid)

            groups = perks.by_companion(game, props) if game else []
            if groups:
                self.window.page.append(label('COMPANION PERKS', 'section-title'))
                self.window.page.append(
                    label(
                        'A level counts as reached when everything it costs has been handed in. '
                        'Setting one here marks its costs as paid without taking the resources.',
                        'dim',
                        wrap=True,
                    )
                )
                grid = Gtk.Grid(column_spacing=12, row_spacing=6)
                row = 0
                self.companion_notes = {}
                for who, entry in ((who, entry) for who, group in groups for entry in group):
                    if entry is dict(groups)[who][0]:
                        grid.attach(label(who.upper(), 'slot-label'), 0, row, 4, 1)
                        row += 1
                    name = Gtk.Box(spacing=8)
                    texture = self.window.icons.item(game, entry.id)
                    if texture:
                        name.append(picture(texture, 30, 'glyph'))
                    name.append(label(game.name(entry.id)))
                    grid.attach(name, 0, row, 1, 1)
                    spin = self.window.spin(
                        entry.level, 0, len(entry.levels), lambda v, p=entry: self.set_companion_level(p, v)
                    )
                    spin.set_hexpand(False)
                    grid.attach(spin, 1, row, 1, 1)
                    grid.attach(label('of %d' % len(entry.levels), 'dim'), 2, row, 1, 1)
                    note = label(self.companion_text(entry), 'perk-text', wrap=True)
                    note.set_max_width_chars(70)
                    self.companion_notes[entry.id] = note
                    grid.attach(note, 3, row, 1, 1)
                    row += 1
                self.window.page.append(grid)

    def companion_text(self, perk: perks.CompanionPerk) -> str:
        """What the highest level reached does, and what is still owed towards the next."""
        game = self.window.game
        parts = []
        if perk.level and game:
            parts.append(game.description(perk.level_ids[perk.level - 1]))
        owed = [
            '%d of %d %s' % (have, need, game.name(what) if game else what)
            for what, have, need in perk.progress()
            if have and have < need
        ]
        if owed:
            parts.append('Towards the next level: %s.' % ', '.join(owed))
        return '  '.join(parts)

    def on_perk_toggled(self, button: Gtk.ToggleButton, perk: str, row: int) -> None:
        if button.get_active():
            self.select_perk(perk, row)

    def select_perk(self, perk: str, row: int) -> None:
        if self.window.busy:
            return
        perks.select(self.window.open_doc.properties, self.window.game, perk)
        self.perk_notes[row].set_label(self.window.game.description(perk))
        self.window.mark_dirty()

    def set_companion_level(self, perk: perks.CompanionPerk, level: int) -> None:
        perk.set_level(level)
        self.companion_notes[perk.id].set_label(self.companion_text(perk))
        self.window.mark_dirty()
