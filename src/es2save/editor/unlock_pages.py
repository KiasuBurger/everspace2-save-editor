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

"""The pages of what is unlocked: blueprints, ship colours and decals, and ship modules."""

from collections.abc import Callable, Iterable, Sequence
from typing import TYPE_CHECKING, Any

from .. import items, unlocks
from .gtk import Gdk, Gtk
from .widgets import label, picture, swatch

if TYPE_CHECKING:
    from .window import EditorWindow


class UnlockPages:
    def __init__(self, window: EditorWindow) -> None:
        self.window = window

    def chip(
        self,
        text: str,
        active: bool,
        on_toggle: Callable[..., object],
        texture: Gdk.Texture | None = None,
        glyph: bool = False,
        rarity: str | None = None,
        tooltip: str | None = None,
    ) -> Gtk.ToggleButton:
        """A toggle for one thing that is unlocked or not."""
        button = Gtk.ToggleButton(active=active)
        button.add_css_class('perk')
        if rarity:
            button.add_css_class('rarity-edge')
            button.add_css_class('rarity-' + rarity)
        inner = Gtk.Box(spacing=6)
        if texture:
            inner.append(picture(texture, 22, 'glyph' if glyph else 'swatch'))
        inner.append(label(text))
        button.set_child(inner)
        if tooltip:
            button.set_tooltip_text(tooltip)
        button.connect('toggled', lambda b: on_toggle(b.get_active()))
        return button

    def unlock_section(self, title: str, chips: Sequence[Gtk.ToggleButton], css: str = 'section-title') -> None:
        """A heading that counts the chips that are on and can switch them all."""
        count = label('', 'dim')

        def recount(*_args: object) -> None:
            count.set_label('%d of %d' % (sum(c.get_active() for c in chips), len(chips)))

        def set_all(on: bool) -> None:
            for c in chips:
                c.set_active(on)

        head = Gtk.Box(spacing=10)
        head.append(label(title.upper(), css))
        head.append(count)
        for text, on in (('All', True), ('None', False)):
            button = Gtk.Button(label=text)
            button.add_css_class('flat')
            button.connect('clicked', lambda _b, on=on: set_all(on))
            head.append(button)
        for c in chips:
            c.connect('toggled', recount)
        recount()
        self.window.page.append(head)

    def flow(self, chips: Iterable[Gtk.ToggleButton]) -> None:
        box = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            column_spacing=6,
            row_spacing=6,
            max_children_per_line=40,
            valign=Gtk.Align.START,
            halign=Gtk.Align.START,
        )
        for c in chips:
            child = Gtk.FlowBoxChild(focusable=False)
            child.set_child(c)
            box.append(child)
        self.window.page.append(box)

    def unlock(self, change: Callable[..., object], *args: Any) -> None:
        """Applies one of the changes in unlocks.py from a chip."""
        try:
            change(self.window.open_doc.properties, *args)
        except KeyError as e:
            self.window.set_status(str(e.args[0]), error=True)
            return
        self.window.mark_dirty()

    def show_blueprints(self) -> None:
        self.window.start_page(
            'blueprints',
            'BLUEPRINTS',
            'What the crafting menu can make, by rarity. The game unlocks a piece of equipment at '
            'a rarity when %d of them have been dismantled at it.' % unlocks.THRESHOLD,
        )
        game, props = self.window.game, self.window.open_doc.properties
        for title, members in unlocks.blueprint_sections(game):
            rows, singles, chips = [], [], []
            for template in members:
                have = unlocks.unlocked_rarities(props, template)
                offered = unlocks.blueprint_rarities(game, template)
                rarities = offered + [r for r in items.RARITIES if r in have and r not in offered]
                name = game.name(template)
                if title == 'Catalysts' and name.startswith('Catalyst: '):
                    name = name[len('Catalyst: ') :]
                made = [
                    self.chip(
                        name if len(rarities) == 1 else rarity,
                        rarity in have,
                        lambda on, t=template, r=rarity: self.unlock(unlocks.set_blueprint, t, r, on),
                        texture=self.window.icons.item(game, template) if len(rarities) == 1 else None,
                        rarity=rarity,
                        tooltip=game.description(template) or None,
                    )
                    for rarity in rarities
                ]
                chips += made
                if len(rarities) == 1:
                    singles += made
                else:
                    rows.append((template, name, made))
            self.unlock_section(title, chips)
            if rows:
                grid = Gtk.Grid(column_spacing=6, row_spacing=6)
                for y, (template, name, made) in enumerate(rows):
                    texture = self.window.icons.item(game, template)
                    grid.attach(picture(texture, 30) if texture else Gtk.Box(width_request=30), 0, y, 1, 1)
                    name_label = label(name)
                    name_label.set_size_request(170, -1)
                    grid.attach(name_label, 1, y, 1, 1)
                    for x, c in enumerate(made):
                        grid.attach(c, 2 + x, y, 1, 1)
                self.window.page.append(grid)
            if singles:
                self.flow(singles)

    def show_colours(self) -> None:
        self.window.start_page('colours', 'SHIP COLOURS AND DECALS')
        game, props = self.window.game, self.window.open_doc.properties
        if unlocks.COLORS in props:
            have = set(unlocks.names(props, unlocks.COLORS))
            for kind, title in unlocks.COLOR_TYPES:
                chips = [
                    self.chip(
                        game.ship_colors[c].get('name') or c,
                        c in have,
                        lambda on, c=c: self.unlock(unlocks.set_name, unlocks.COLORS, c, on),
                        texture=swatch(game.ship_colors[c]['color']),
                    )
                    for c in unlocks.colors(game, kind)
                ]
                self.unlock_section('Colours: ' + title, chips)
                self.flow(chips)
        if unlocks.DECALS in props:
            have = set(unlocks.names(props, unlocks.DECALS))
            chips = [
                self.chip(
                    game.ship_decals[d].get('name') or d,
                    d in have,
                    lambda on, d=d: self.unlock(unlocks.set_name, unlocks.DECALS, d, on),
                    texture=self.window.icons.named(game.ship_decals[d].get('icon')),
                    glyph=True,
                )
                for d in unlocks.decals(game)
            ]
            self.unlock_section('Decals', chips)
            self.flow(chips)

    def show_modules(self) -> None:
        self.window.start_page('modules', 'SHIP MODULES')
        game, props = self.window.game, self.window.open_doc.properties
        if unlocks.MODULES in props:
            for ship_class in unlocks.SHIP_CLASSES:
                have = set(unlocks.modules(props, ship_class))
                groups, rows = [], []
                for kind, title in unlocks.MODULE_TYPES:
                    groups.append(
                        [
                            self.chip(
                                '%s %s' % (title, unlocks.module_name(game, ship_class, module)),
                                module in have,
                                lambda on, c=ship_class, m=module: self.unlock(unlocks.set_module, c, m, on),
                                tooltip=module,
                            )
                            for module in unlocks.module_choices(game, ship_class, kind)
                        ]
                    )
                for model, wings in unlocks.wing_families(game, ship_class):
                    for wing in wings:
                        rows.append(
                            (
                                '%s: %s' % (model, unlocks.module_name(game, ship_class, wing)),
                                [
                                    self.chip(
                                        'Tier ' + tier,
                                        extension in have,
                                        lambda on, c=ship_class, w=wing, e=extension: self.unlock(
                                            unlocks.set_wing, self.window.game, c, w, e, on
                                        ),
                                        tooltip=extension,
                                    )
                                    for tier, extension in unlocks.wing_tiers(game, ship_class, wing)
                                ],
                            )
                        )
                self.unlock_section(
                    '%s ship modules' % ship_class,
                    [c for made in groups for c in made] + [c for _name, made in rows for c in made],
                )
                for made in groups:
                    self.flow(made)
                grid = Gtk.Grid(column_spacing=6, row_spacing=6)
                for y, (name, made) in enumerate(rows):
                    name_label = label(name)
                    name_label.set_size_request(170, -1)
                    grid.attach(name_label, 0, y, 1, 1)
                    for x, c in enumerate(made):
                        grid.attach(c, 1 + x, y, 1, 1)
                self.window.page.append(grid)
