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

"""The selected item: its card as the game draws it, and the controls that change it."""

import math
import threading
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from .. import chaos, itemrules, items
from ..items import Item
from . import view
from .gtk import GLib, Gtk, Pango
from .widgets import INT_MAX, clear, close_on_escape, label, margins, picture, soon

if TYPE_CHECKING:
    from .window import EditorWindow


class ItemPanel:
    def __init__(self, window: EditorWindow) -> None:
        self.window = window
        self.card: view.Card | None = None
        self.search_cancel: threading.Event | None = None  # set to stop the seed search under way

    def edited_item(self) -> Item:
        item = self.window.selected_item()
        if item is None:
            raise RuntimeError('no item is selected')
        return item

    @property
    def shown_card(self) -> view.Card:
        if self.card is None:
            raise RuntimeError('no item is selected')
        return self.card

    def changed(self, rebuild_controls: bool = False) -> None:
        """Call after changing the selected item: refreshes its tile, the card and the title."""
        self.window.mark_dirty()
        tile = self.window.tiles.get(self.window.selection) if self.window.selection else None
        if tile:
            tile.update()
        self.build_card()
        if rebuild_controls:
            # the control that changed is among what is rebuilt, which must not happen inside its handler
            soon(self.build_controls)
        if self.window.selection and self.window.inventory_page.fit_hold(
            self.window.open_doc.inventory(self.window.selection[0]), self.window.selection[1]
        ):
            # the page is rebuilt, which must not happen inside the handler of one of its widgets
            key = self.window.selection[0]
            soon(lambda: self.window.inventory_page.show(self.window.open_doc.inventory(key)))

    def build_detail(self) -> None:
        self.build_card()
        self.build_controls()

    def build_card(self) -> None:
        clear(self.window.card_box)
        item = self.window.selected_item()
        if item is None:
            hint = label('Select an item to see and change it.\nClick an empty slot to add one.', 'dim', wrap=True)
            self.window.card_box.append(hint)
            self.card = None
            return
        card = self.card = view.build_card(self.window.game, item)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class('card')
        box.add_css_class('rarity-' + card.rarity)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        margins(inner, 12)
        box.append(inner)

        head = Gtk.Box(spacing=10)
        badge = label(str(card.amount if card.stackable else card.level), 'level-badge', xalign=0.5)
        badge.set_valign(Gtk.Align.START)
        head.append(badge)
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        names.append(label(card.name, 'name', wrap=True))
        kind = Gtk.Box(spacing=6)
        texture = self.window.icons.type(view.type_icon(self.window.game, card.template))
        if texture:
            kind.append(picture(texture, 18, 'glyph'))
        kind.append(label(card.type or card.template, 'type'))
        names.append(kind)
        head.append(names)
        icon = self.window.icons.item(self.window.game, card.template)
        if icon:
            head.append(picture(icon, 64))
        inner.append(head)

        tags = Gtk.Box(spacing=6)
        if card.rarity != 'None':
            tags.append(label(card.rarity.upper(), 'tag'))
        if card.grade != 'None':
            tags.append(label(card.grade.upper(), 'tag grade'))
        if card.group:
            tags.append(label(card.group, 'dim'))
        inner.append(tags)

        if card.effect:
            inner.append(label(card.effect, 'effect', wrap=True))
        if card.dps:
            dps = Gtk.Box(spacing=18)
            for title, text in card.dps:
                cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                cell.append(label(title, 'dps-title'))
                cell.append(label(text, 'dps-value'))
                dps.append(cell)
            inner.append(dps)
        shown = [s for s in card.stats if (s.kind != 'fixed' or s.value) and not s.catalyst]
        if shown:
            stats = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            for s in shown:
                if s.name.startswith('legendary_'):
                    continue  # already part of the effect text
                stats.append(label(s.text or '%d%% %s' % (round(s.position * 100), s.label), 'stat-line', wrap=True))
            inner.append(stats)
        if card.rank:
            inner.append(label('★ ' + card.rank, 'rank'))
        for c in card.catalysts:
            block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            block.append(label('◆ Catalyst: %s' % c.name, 'catalyst rarity-' + c.rarity))
            if c.description:
                block.append(label(c.description, 'catalyst-text', wrap=True))
            for line in c.effects:
                block.append(label('• ' + line, 'catalyst-effect', wrap=True))
            inner.append(block)
        if card.description:
            inner.append(label(card.description, 'flavour', wrap=True))
        self.window.card_box.append(box)
        for w in card.warnings:
            warn = label(w, 'warning', wrap=True)
            warn.set_margin_top(6)
            self.window.card_box.append(warn)

    def build_controls(self) -> None:
        clear(self.window.edit_box)
        item = self.window.selected_item()
        if item is None:
            return
        with self.window.filling():
            self.fill_controls(item)

    def fill_controls(self, item: Item) -> None:
        game, card, box = self.window.game, self.shown_card, self.window.edit_box
        tpl = card.template

        box.append(label('ITEM', 'section-title'))
        if card.stackable:
            box.append(
                self.window.row(
                    'Amount', self.window.spin(card.amount, 1, 99999999, lambda v: self.set_field('Amount', v))
                )
            )
        if itemrules.is_device(game, tpl):
            # the game holds every device at the player's level, whatever the save says
            box.append(self.window.row('Level', label(str(card.level), 'value')))
        elif 'ItemLevel' in item and not card.stackable:
            box.append(
                self.window.row('Level', self.window.spin(card.level, 1, 99, lambda v: self.set_field('ItemLevel', v)))
            )
        if card.max_ammo is not None:
            # a save can hold more ammo than the weapon's capacity. The control keeps that
            # amount until the user changes it.
            top = max(card.max_ammo, card.ammo or 0)
            ammo = self.window.spin(card.ammo or 0, 0, top, lambda v: self.set_field('Ammo', v))
            ammo.set_tooltip_text('%d when full' % card.max_ammo)
            box.append(self.window.row('Ammo', ammo))
        if card.equipment or card.rarity != 'None':
            allowed = game.rarities(tpl)
            box.append(self.window.row('Rarity', self.window.dropdown(allowed, allowed, card.rarity, self.set_rarity)))
        if card.rarity == 'Ascendant' and card.equipment:  # only equipment has a rank
            count = items.ascendant_count(item)
            box.append(
                self.window.row(
                    'Rank',
                    self.window.spin(
                        (count or 0) + 1, 1, items.ASCENDANT_MAX_COUNT + 1, lambda v: self.set_ascendant(v - 1)
                    ),
                )
            )
        if card.equipment and 'Grade' in item:
            grades = items.grades_for(card.rarity)
            box.append(self.window.row('Grade', self.window.dropdown(grades, grades, card.grade, self.set_grade)))
        if 'Grade' in item and itemrules.radiant_blessings(game, tpl):
            box.append(self.window.row('Radiant', self.window.switch(card.grade == 'Radiant', self.set_radiant)))
        if card.equipment:
            affixes = [items.EMPTY] + game.affixes_for(tpl)
            names = ['None'] + [game.affix_name(a) for a in affixes[1:]]
            prefix = self.window.dropdown(affixes, names, card.affix, self.set_affix)
            if itemrules.catalyst_affix(game, item):
                # the game takes the prefix from the catalyst, whatever the save says
                prefix.set_sensitive(False)
                prefix.set_tooltip_text('Set by the prefix catalyst')
            box.append(self.window.row('Prefix', prefix))

        # Everything Chaos scales is listed. Values the game fixes (a stack limit of 6, a
        # weapon's base numbers) have no slider; the legendary ones stay in view and the rest
        # fold away.
        editable = [s for s in card.stats if s.kind != 'fixed' or s.name.startswith('legendary_')]
        folded = [s for s in card.stats if s not in editable]
        if editable or folded:
            head = Gtk.Box(spacing=8)
            title = label('ATTRIBUTES', 'section-title')
            title.set_hexpand(True)
            head.append(title)
            if self.maxable(card.stats):
                best = Gtk.Button(label='Max all')
                best.connect('clicked', lambda _b: self.max_all())
                head.append(best)
            box.append(head)
            self.stat_rows: dict[str, tuple[Gtk.Label | Gtk.Entry, Gtk.Label]] = {}
            for s in editable:
                box.append(self.stat_row(item, s))
            if folded:
                expander = Gtk.Expander(label='Fixed values (%d)' % len(folded))
                inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                for s in folded:
                    inner.append(self.stat_row(item, s))
                expander.set_child(inner)
                box.append(expander)
            addable = itemrules.addable_stats(game, item)
            if addable and itemrules.takes_stats(game, tpl):
                self.new_stat = addable[0]
                add = Gtk.Box(spacing=8)
                add.append(
                    self.window.dropdown(
                        addable,
                        [game.attribute_name(a) for a in addable],
                        addable[0],
                        lambda v: setattr(self, 'new_stat', v),
                    )
                )
                button = Gtk.Button(label='Add stat')
                button.connect('clicked', lambda _b: self.add_stat())
                add.append(button)
                box.append(add)

        if card.equipment and 'Catalysts' in item:
            box.append(label('CATALYSTS', 'section-title'))
            # one prefix catalyst or none, picked like the prefix; the others are listed and
            # added to, and those that can go on again stay on offer
            prefixes = itemrules.prefix_catalysts(game, item)
            if prefixes:
                choices = [items.EMPTY] + prefixes
                names = ['None'] + [game.catalyst_name(c) for c in prefixes]
                current = itemrules.prefix_catalyst(game, item) or items.EMPTY
                box.append(
                    self.window.row(
                        'Prefix catalyst', self.window.dropdown(choices, names, current, self.set_prefix_catalyst)
                    )
                )
            for c in card.catalysts:
                if itemrules.is_prefix_catalyst(game, c.id):
                    continue
                cid = c.id
                row = Gtk.Box(spacing=8)
                lab = label(c.name, 'catalyst rarity-' + c.rarity)
                lab.set_hexpand(True)
                row.append(lab)
                remove = Gtk.Button(label='Remove')
                remove.add_css_class('danger')
                remove.connect('clicked', lambda _b, c=cid: self.remove_catalyst(c))
                row.append(remove)
                box.append(row)
            available = itemrules.installable_catalysts(game, item)
            if available:
                self.new_catalyst = available[0]
                add = Gtk.Box(spacing=8)
                add.append(
                    self.window.dropdown(
                        available,
                        [game.catalyst_name(c) for c in available],
                        available[0],
                        lambda v: setattr(self, 'new_catalyst', v),
                    )
                )
                button = Gtk.Button(label='Install')
                button.connect('clicked', lambda _b: self.add_catalyst())
                add.append(button)
                box.append(add)

        if card.chaos:
            box.append(label('CHAOS SEED', 'section-title'))
            seed_row = Gtk.Box(spacing=8)
            seed_row.append(self.window.spin(card.seed or 1, -INT_MAX - 1, INT_MAX, self.set_seed))
            search = Gtk.Button(label='Find best')
            search.add_css_class('accent')
            search.add_css_class('suggested-action')
            search.connect('clicked', lambda _b: self.search_seed())
            seed_row.append(search)
            box.append(seed_row)
            box.append(
                label(
                    'Each attribute is multiplied by a factor between 0.8 and 1.2 that the seed '
                    'decides. "Find best" tries every seed for the highest worst-case factor.',
                    'dim',
                    wrap=True,
                )
            )

        # Moving has a destination, so it gets a line of its own; the other two act on this slot.
        box.append(label('ACTIONS', 'section-title'))
        source = self.window.selected_inventory
        others = [
            inv for inv in self.window.open_doc.inventories if inv.key != self.window.selected_slot[0] and inv.limited
        ]
        if others:
            self.move_target = others[0]
            line = Gtk.Box(spacing=8)
            line.append(
                self.window.dropdown(
                    others, [inv.label for inv in others], others[0], lambda v: setattr(self, 'move_target', v)
                )
            )
            move = Gtk.Button(label='Move')
            move.connect('clicked', lambda _b: self.move_item())
            line.append(move)
            box.append(self.window.row('Move to', line))
        actions = Gtk.Box(spacing=8)
        if source.limited:  # resources and quest items are one stack per kind
            dup = Gtk.Button(label='Duplicate')
            dup.set_tooltip_text('Put a copy into a free cargo slot of %s' % source.label)
            dup.connect('clicked', lambda _b: self.duplicate_item())
            if source.is_full():
                dup.set_sensitive(False)
                dup.set_tooltip_text('%s has no free cargo slot' % source.label)
            actions.append(dup)
        delete = Gtk.Button(label='Delete')
        delete.add_css_class('danger')
        delete.set_halign(Gtk.Align.END)
        delete.set_hexpand(True)
        delete.connect('clicked', lambda _b: self.delete_item())
        actions.append(delete)
        box.append(actions)
        box.append(label('%s  •  seed %s' % (tpl, items.get(item, 'Seed', '?')), 'dim', selectable=True))

    def stat_row(self, item: Item, s: view.Stat) -> Gtk.Box:
        game = self.window.game
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        outer.add_css_class('stat-row')
        top = Gtk.Box(spacing=8)
        if s.kind == 'bonus':
            choices = itemrules.swap_choices(game, item, s.name)
            top.append(
                self.window.dropdown(
                    choices,
                    [game.attribute_name(a) for a in choices],
                    s.name,
                    lambda new, old=s.name: self.swap_stat(old, new),
                )
            )
            # a reliquary has its blessing, and its radiant one while it is Radiant, which the
            # Radiant switch takes away
            if not itemrules.radiant_blessings(game, items.template(item)):
                remove = Gtk.Button(label='×')
                remove.add_css_class('danger')
                remove.set_tooltip_text('Remove this stat')
                remove.connect('clicked', lambda _b, n=s.name: self.remove_stat(n))
                top.append(remove)
        else:
            name = label(s.label)
            name.set_hexpand(True)
            # a long name gives way instead of widening the panel when its row is unfolded
            name.set_ellipsize(Pango.EllipsizeMode.END)
            name.set_tooltip_text(s.label)
            top.append(name)
            if s.low:
                top.append(label('%s – %s' % (s.low, s.high), 'range'))
        if s.kind == 'count':
            # set as a number; it goes with its catalyst, so there is nothing to remove it by
            top.append(self.window.spin(s.shown or 0, 0, 1000000, self.set_kills))
            outer.append(top)
            return outer
        outer.append(top)
        line = top
        value: Gtk.Label | Gtk.Entry = label('', 'value', xalign=1.0)
        if not s.sliding:
            # nothing to slide: the game gives every item of this kind the same value, and a
            # bonus stat of one value has it whatever its position
            if s.kind == 'fixed':
                top.append(label('fixed', 'range'))
        else:
            line = Gtk.Box(spacing=8)
            outer.append(line)
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.0, 1.0, 0.01)
            scale.set_draw_value(False)
            scale.set_hexpand(True)
            scale.set_value(max(0.0, min(1.0, s.position)))
            if s.locked:
                scale.set_sensitive(False)
                scale.set_tooltip_text("Held at its maximum by the item's prefix or a catalyst")
            scale.connect('value-changed', lambda sc, n=s.name: self.set_position(n, sc.get_value()))
            line.append(scale)
            if not s.locked:
                value = self.value_entry(item, s.name, scale)
        factor = label('', 'factor', xalign=1.0)
        line.append(value)
        if self.shown_card.chaos:  # the column is only there for the factors Chaos gives
            line.append(factor)
        self.stat_rows[s.name] = (value, factor)
        self.fill_stat_row(s)
        return outer

    def value_entry(self, item: Item, name: str, scale: Gtk.Scale) -> Gtk.Entry:
        """The value beside a slider, in an entry. The user can type a value instead of using the
        slider. When the user presses Enter or leaves the entry, the slider moves to the value.
        A value outside the range moves the slider to the nearest end."""
        entry = Gtk.Entry(xalign=1.0, width_chars=9, max_width_chars=9)
        entry.add_css_class('value')
        entry.set_tooltip_text('Type a value within the range and press Enter')

        def commit(*_args: object) -> None:
            # the entry also loses focus when the panel rebuilds the controls for a different
            # item. Do not apply the text to that item.
            if self.window.busy or self.window.selected_item() is not item:
                return
            stat = next((st for st in self.shown_card.stats if st.name == name), None)
            if stat is None:
                return
            pos = self.typed_position(stat, entry.get_text())
            if pos is not None:
                scale.set_value(pos)  # the slider's handler sets the position
            self.refresh_stat_rows()  # replace the typed text with the calculated value

        entry.connect('activate', commit)
        focus = Gtk.EventControllerFocus()
        focus.connect('leave', commit)
        entry.add_controller(focus)
        return entry

    def typed_position(self, s: view.Stat, text: str) -> float | None:
        """The position in the range of a typed value, limited to the range. None if the text is
        not a number. If the values at the ends of the range are not known, the text is a
        percentage of the range."""
        if s.span:
            value = self.window.game.read_value(s.name, text)
            low, high = s.span
            if value is None or low == high:
                return None
            pos = (value - low) / (high - low)
        else:
            text = text.strip().removesuffix('of range').strip().removesuffix('%')
            try:
                pos = float(text) / 100
            except ValueError:
                return None
        if math.isnan(pos):
            return None
        return max(0.0, min(1.0, pos))

    def fill_stat_row(self, s: view.Stat) -> None:
        value, factor = self.stat_rows[s.name]
        text = s.value or '%d%% of range' % round(s.position * 100)
        if isinstance(value, Gtk.Entry):
            value.set_text(text)
        else:
            value.set_label(text)
        for c in ('good', 'bad'):
            factor.remove_css_class(c)
        if s.factor is None:
            factor.set_label('')
        else:
            factor.set_label('×%.3f' % s.factor)
            factor.add_css_class('good' if s.factor >= 1.0 else 'bad')

    def refresh_stat_rows(self) -> None:
        for s in self.shown_card.stats:
            if s.name in getattr(self, 'stat_rows', {}):
                self.fill_stat_row(s)

    def set_field(self, key: str, value: Any) -> None:
        items.put(self.edited_item(), key, value)
        self.changed()

    def set_rarity(self, rarity: str) -> None:
        items.set_rarity(self.edited_item(), rarity, ranked=self.shown_card.equipment)
        self.changed(rebuild_controls=True)

    def set_ascendant(self, count: int) -> None:
        items.set_ascendant_count(self.edited_item(), count)
        self.changed()

    def set_grade(self, grade: str) -> None:
        items.set_grade(self.edited_item(), grade)
        self.changed(rebuild_controls=True)  # Flawless installs on Radiant items alone

    def set_radiant(self, radiant: bool) -> None:
        """Makes a reliquary Radiant, with the first radiant blessing unless it has one, or plain."""
        item = self.edited_item()
        blessing = None
        if radiant:
            blessing = (
                items.radiant_blessing(item) or itemrules.radiant_blessings(self.window.game, items.template(item))[0]
            )
        items.set_radiant_blessing(item, blessing, list(self.window.game.attributes))
        self.changed(rebuild_controls=True)

    def set_affix(self, affix: str) -> None:
        items.set_affix(self.edited_item(), affix)
        self.changed(rebuild_controls=True)  # Optimized holds the sliders at their maximum

    def set_position(self, name: str, pos: float) -> None:
        if self.window.busy:
            return
        items.set_position(self.edited_item(), name, pos)
        self.changed()
        self.refresh_stat_rows()

    def set_kills(self, kills: int) -> None:
        item = self.edited_item()
        before = [(n, p) for n, p in items.attributes(item) if n != items.EVOLUTION_KILL_COUNT]
        itemrules.set_evolution_kills(self.window.game, item, kills)
        after = [(n, p) for n, p in items.attributes(item) if n != items.EVOLUTION_KILL_COUNT]
        # the rows only change when the count crosses one of the catalyst's thresholds
        self.changed(rebuild_controls=before != after)

    @staticmethod
    def maxable(stats: Iterable[view.Stat]) -> list[view.Stat]:
        """The stats with a slider: those 'Max all' sets to the top of their range."""
        return [s for s in stats if s.sliding and not s.locked]

    def max_all(self) -> None:
        item = self.edited_item()
        for s in self.maxable(self.shown_card.stats):
            items.set_position(item, s.name, 1.0)
        self.changed(rebuild_controls=True)

    def swap_stat(self, old: str, new: str) -> None:
        if new != old:
            item = self.edited_item()
            items.replace_attribute(item, old, new, list(self.window.game.attributes))
            self.changed(rebuild_controls=True)

    def remove_stat(self, name: str) -> None:
        items.remove_attribute(self.edited_item(), name)
        self.changed(rebuild_controls=True)

    def add_stat(self) -> None:
        item = self.edited_item()
        items.add_attribute(item, self.new_stat, 1.0, list(self.window.game.attributes))
        self.changed(rebuild_controls=True)

    def add_catalyst(self) -> None:
        itemrules.install_catalyst(self.window.game, self.edited_item(), self.new_catalyst)
        self.changed(rebuild_controls=True)

    def set_prefix_catalyst(self, catalyst: str) -> None:
        itemrules.set_prefix_catalyst(
            self.window.game, self.edited_item(), None if catalyst == items.EMPTY else catalyst
        )
        self.changed(rebuild_controls=True)

    def remove_catalyst(self, catalyst: str) -> None:
        items.remove_catalyst(self.edited_item(), catalyst)
        itemrules.sync_affix(self.window.game, self.edited_item())
        self.changed(rebuild_controls=True)

    def set_seed(self, seed: int) -> None:
        items.set_catalyst_seed(self.edited_item(), seed)
        self.changed()
        self.refresh_stat_rows()

    def search_seed(self) -> None:
        """Searches every Chaos seed for the best one for the item, behind a dialog that keeps
        the window out of use until the search is over, so that nothing changes meanwhile."""
        item = self.edited_item()
        kinds = chaos.analyse(item, self.window.game.items, attribute_order=self.window.game.attributes).kinds()
        if not any(d for _, _, d in kinds):
            self.window.set_status('This item has nothing for Chaos to improve.')
            return
        cancel = self.search_cancel = threading.Event()

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        margins(box, 20)
        box.append(label('Trying every Chaos seed', 'section-title'))
        box.append(label('For %s' % self.shown_card.name, 'dim', wrap=True))
        bar = Gtk.ProgressBar(show_text=True)
        bar.set_size_request(380, -1)
        box.append(bar)
        stop = Gtk.Button(label='Cancel', halign=Gtk.Align.END)
        stop.connect('clicked', lambda _b: self.cancel_search())
        box.append(stop)
        dialog = Gtk.Window(transient_for=self.window, modal=True, title='Chaos seed', resizable=False, child=box)
        dialog.add_css_class('es2')
        close_on_escape(dialog)
        self.window.set_sensitive(False)

        def close(_w: Gtk.Window) -> bool:
            self.cancel_search()
            return True  # it goes when the search has stopped

        dialog.connect('close-request', close)
        dialog.present()

        def progress(fraction: float) -> bool:
            if not cancel.is_set():
                bar.set_fraction(fraction)
            return False

        def work() -> None:
            try:
                result, error = (
                    chaos.search(kinds, top=1, progress=lambda f: GLib.idle_add(progress, f), cancel=cancel),
                    None,
                )
            except chaos.SearchCancelled:
                result, error = None, None
            except Exception as e:  # noqa: BLE001 (shown to the user)
                result, error = None, e
            GLib.idle_add(finish, result, error)

        def finish(result: list[tuple[int, float, float]] | None, error: Exception | None) -> bool:
            self.search_cancel = None
            dialog.destroy()
            self.window.set_sensitive(True)
            if error:
                self.window.set_status('Seed search failed: %s' % error, error=True)
            elif cancel.is_set() or not result:
                self.window.set_status('Seed search cancelled')
            else:
                seed, worst, mean = result[0]
                items.set_catalyst_seed(item, seed)
                self.changed(rebuild_controls=True)
                self.window.set_status(
                    'Best seed %d: every scored attribute gets at least ×%.3f (average ×%.3f)' % (seed, worst, mean)
                )
            return False

        threading.Thread(target=work, daemon=True).start()

    def cancel_search(self) -> None:
        """Stops a seed search that is under way; its dialog goes once it has."""
        if self.search_cancel:
            self.search_cancel.set()

    def duplicate_item(self) -> None:
        inv = self.window.selected_inventory
        if inv.is_full():
            self.window.set_status('%s has no free cargo slot' % inv.label, error=True)
            return
        index = inv.add(items.duplicate(self.edited_item()))
        self.window.inventory_page.reload_page((inv.key, items.CARGO, index))
        self.window.set_status('Duplicated into cargo slot %d' % (index + 1))

    def delete_item(self) -> None:
        inv = self.window.selected_inventory
        inv.remove(self.window.selected_slot[1], self.window.selected_slot[2])
        note = self.window.inventory_page.fit_hold(inv, self.window.selected_slot[1])
        self.window.inventory_page.reload_page(None)
        self.window.set_status('Slot emptied' + ('. ' + note if note else ''))

    def move_item(self) -> None:
        inv = self.window.selected_inventory
        target = self.move_target
        if target.is_full():
            self.window.set_status('%s has no free cargo slot' % target.label, error=True)
            return
        item = inv.remove(self.window.selected_slot[1], self.window.selected_slot[2])
        index = target.add(item)
        note = self.window.inventory_page.fit_hold(inv, self.window.selected_slot[1])
        self.window.inventory_page.reload_page(None)
        self.window.set_status('Moved to %s, cargo slot %d' % (target.label, index + 1) + ('. ' + note if note else ''))
