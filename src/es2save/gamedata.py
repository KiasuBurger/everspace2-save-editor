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

"""The game's item definitions, read from game_identifiers.json.

That file is made from the installed game by gameidentifiers.py, which the editor does when
it first runs. It tells the tools what an item is called, which attributes, affixes and catalysts it can
have, and the range of every attribute, and lists the ship colours, decals and appearance
modules there are to unlock. Everything here degrades to "unknown" when the file or an item
is missing.
"""

import json
import os
import re
from collections.abc import Collection, Iterable, Sequence
from typing import Any

from . import gamepaths

GAME_DATA_NAME = 'game_identifiers.json'
# the sections of the catalogue that GameData reads
SECTIONS = (
    'items',
    'affixes',
    'catalysts',
    'attributes',
    'categories',
    'ship_colors',
    'ship_decals',
    'ship_modules',
    'mutators',
)
_TAGS = re.compile(r'</?[A-Za-z_]*>')  # rich-text markup such as <highlight>10s</>

EQUIPMENT_CATEGORIES = (
    'cat_weapon',
    'cat_launcher',
    'cat_energy_core',
    'cat_shield',
    'cat_plating',
    'cat_sensor',
    'cat_thrusters',
    'cat_cargo_unit',
)
RARITIES = ['None', 'Common', 'Uncommon', 'Rare', 'Superior', 'Ascendant', 'Legendary']  # lowest first


def level_scale(meta: dict[str, Any], level: float) -> float:
    """What an attribute's range value is multiplied by on an item of an effective level
    (ItemLevel + VirtualLevelOffset), from its growth in attributes.CSV.

    'c' accumulates: growth x (1 + 2 + ... + level), which is exact against recorded values
    (x703 at level 37). '*' compounds per level from level 1, which matches the game's
    card for a level 30 Ascendant autocannon (11 x 1.13^35 = 598 kinetic damage)."""
    growth = meta.get('growth', 1)
    op = meta.get('growth_op', '*')
    if not isinstance(growth, (int, float)):
        return 1.0
    if op == 'c':
        return growth * level * (level + 1) / 2
    if op == '*':
        # the growth is first raised to a power of its own, which is 1 for everything but the
        # slot counts of ships (UItemLib::MakeAttributeInstance in the game)
        power = meta.get('growth_exp', 1)
        return (growth ** (power if isinstance(power, (int, float)) else 1)) ** (level - 1)
    return 1.0


def value_in_range(r: Sequence[Any] | None, position: float) -> float | None:
    """The unscaled value at a position in an items.CSV (min, operator, max) range."""
    if not r or not isinstance(r[0], (int, float)) or not isinstance(r[2], (int, float)):
        return None
    return r[0] + position * (r[2] - r[0])


def cache_file() -> str:
    """Where the editor writes the file when it first runs, from the installed game."""
    return os.path.join(gamepaths.cache_dir(), GAME_DATA_NAME)


def cache_to_build(game: GameData) -> str | None:
    """The cache file, if the editor must build it from the game. This is when there is no
    cache yet, or when `game` came from the cache and is outdated. None otherwise."""
    cache = cache_file()
    if not os.path.exists(cache) or (game.path == cache and game.outdated):
        return cache
    return None


def candidates(near: str | None = None) -> list[str]:
    """Where game_identifiers.json is looked for: where the editor writes it, the current
    directory, next to `near`, and this checkout."""
    here = os.path.dirname(os.path.abspath(__file__))
    out = [cache_file(), GAME_DATA_NAME]
    if near:
        out.append(os.path.join(os.path.dirname(os.path.abspath(near)), GAME_DATA_NAME))
    return out + [os.path.join(here, '..', '..', GAME_DATA_NAME)]


def find_game_data(explicit: str | None = None, near: str | None = None) -> str | None:
    """Path of game_identifiers.json: the given one, else the first of candidates() that is
    there. None if there is none."""
    if explicit:
        return explicit if os.path.exists(explicit) else None
    for c in candidates(near):
        if os.path.exists(c):
            return os.path.normpath(c)
    return None


class GameData:
    def __init__(self, data: dict[str, Any] | None = None, path: str | None = None) -> None:
        data = data or {}
        self.path = path
        self.items = data.get('items', {})
        self.affixes = data.get('affixes', {})
        self.catalysts = data.get('catalysts', {})
        self.attributes = data.get('attributes', {})
        self.categories = data.get('categories', {})
        self.mutators = data.get('mutators', {})  # the modifiers of high-risk areas and rifts
        # true if the catalogue does not have all the sections. An older editor wrote such a catalogue.
        self.outdated = bool(data) and any(key not in data for key in SECTIONS)
        # what a ship can be made to look like (see unlocks.py)
        self.ship_colors = data.get('ship_colors', {})
        self.ship_decals = data.get('ship_decals', {})
        self.ship_modules = data.get('ship_modules', {})
        self._told_apart = self._tell_apart()

    def _tell_apart(self) -> dict[str, str]:
        """{catalyst: name}: the game gives some catalysts the same name (Kinetic Damage comes
        as an uncommon +15% and a rare +30%), so those get what they do after it, or their
        rarity where the data does not say."""
        named: dict[str, list[str]] = {}
        for cid, info in self.catalysts.items():
            if info.get('is_item') and info.get('name'):
                named.setdefault(info['name'], []).append(cid)
        out = {}
        for name, ids in named.items():
            for cid in ids if len(ids) > 1 else ():
                changes = [v for v in self.catalysts[cid].get('modifiers', {}).values() if isinstance(v, (int, float))]
                mark = (
                    '%+d%%' % round(changes[0] * 100)
                    if len(set(changes)) == 1
                    else '(%s)' % self.item(cid).get('max_rarity', cid)
                )
                out[cid] = '%s %s' % (name, mark)
        return out

    @classmethod
    def load(cls, explicit: str | None = None, near: str | None = None) -> GameData:
        path = find_game_data(explicit, near)
        if not path:
            return cls()
        with open(path) as f:
            return cls(json.load(f), path)

    def __bool__(self) -> bool:
        return bool(self.items)

    # ---- items

    def item(self, template: str) -> dict[str, Any]:
        return self.items.get(template, {})

    def name(self, template: str) -> str:
        return self._told_apart.get(template) or self.item(template).get('name') or template

    def category(self, template: str) -> str:
        return self.item(template).get('main_category', '')

    def sub_category(self, template: str) -> str:
        return self.item(template).get('sub_category', '')

    def category_name(self, template: str) -> str:
        """'Shield', or the sub-category where there is one ('Rail Gun')."""
        info = self.item(template)
        cat = self.categories.get(info.get('main_category'), {})
        return cat.get('sub_categories', {}).get(info.get('sub_category')) or cat.get('name', '')

    def area_mutator(self, attribute: str) -> bool:
        """Whether a mutator can occur in a high-risk area, and thus on a signal decoder. True
        if the catalogue has no mutators table."""
        if not self.mutators:
            return True
        return bool(self.mutators.get(attribute, {}).get('high_risk_areas'))

    def is_equipment(self, template: str) -> bool:
        return self.category(template) in EQUIPMENT_CATEGORIES

    def rarities(self, template: str) -> list[str]:
        """The rarities the template allows, lowest first."""
        info = self.item(template)
        lo, hi = info.get('min_rarity'), info.get('max_rarity')
        if lo not in RARITIES or hi not in RARITIES:
            return list(RARITIES)
        return RARITIES[RARITIES.index(lo) : RARITIES.index(hi) + 1]

    def templates(self, main_category: str | None = None) -> list[str]:
        return [t for t, d in self.items.items() if main_category is None or d['main_category'] == main_category]

    # ---- attributes

    def ranges(self, template: str) -> dict[str, list[Any]]:
        """{attribute: [min, operator, max]} in the game's attribute order."""
        return self.item(template).get('attributes', {})

    def fixed_attributes(self, template: str) -> list[str]:
        """Attributes every item of the template has (everything but the level-scaled bonus stats)."""
        return [a for a, r in self.ranges(template).items() if r[1] != '-']

    def bonus_attributes(self, template: str) -> list[str]:
        """The level-scaled stats an item rolls a few of."""
        return [a for a, r in self.ranges(template).items() if r[1] == '-']

    def attribute_name(self, attribute: str) -> str:
        return self.attributes.get(attribute, {}).get('name') or attribute.replace('_', ' ')

    def range_value(self, template: str, attribute: str, position: float) -> float | None:
        """The unscaled value at a position in the attribute's range, or None."""
        return value_in_range(self.ranges(template).get(attribute), position)

    def format_value(self, attribute: str, value: float | None, precise: bool = True) -> str:
        """A value with its unit: percentages, metres (stored as centimetres), seconds.
        With precise=False it is rounded the way the game's item cards show it."""
        if value is None:
            return ''
        meta = self.attributes.get(attribute, {})
        kind, unit = meta.get('type', ''), meta.get('unit', '')
        if kind == 'percent' or unit == '%':
            number, unit = value * 100, '%'
        elif unit in ('m', 'm/s', 'm/s^2'):
            number = value / 100  # distances are stored in centimetres
        elif unit == 'km':
            number = value / 100000
        else:
            number = value
        if precise:
            return _number(number) + unit
        sign = '+' if meta.get('prefix') == '+' and number > 0 else ''
        if 'decimals' in meta:  # how many decimals the game shows for this attribute
            if abs(number) >= 10000 and unit != '%':
                return '%s%.1fk%s' % (sign, number / 1000, unit)  # '18.0k', as the game writes it
            # decimals are dropped from large values: the game writes 11.5/s but 1895/s
            decimals = meta['decimals'] if abs(number) < 1000 else 0
            return '%s%.*f%s' % (sign, decimals, number, unit)
        if kind == 'float' and abs(number) < 100:
            return sign + ('%.1f' % number).rstrip('0').rstrip('.') + unit
        return '%s%d%s' % (sign, round(number), unit)

    def read_value(self, attribute: str, text: str) -> float | None:
        """The stored value of typed text. The text has the format that format_value writes, with
        or without the unit. None if the text is not a number."""
        meta = self.attributes.get(attribute, {})
        kind, unit = meta.get('type', ''), meta.get('unit', '')
        if kind == 'percent':
            unit = '%'
        text = text.strip()
        if unit and text.endswith(unit):
            text = text[: -len(unit)].strip()
        thousands = text.lower().endswith('k')
        try:
            number = float(text[:-1] if thousands else text) * (1000 if thousands else 1)
        except ValueError:
            return None
        if unit == '%':
            return number / 100
        if unit in ('m', 'm/s', 'm/s^2'):
            return number * 100
        if unit == 'km':
            return number * 100000
        return number

    def stat_text(self, attribute: str, value: float | None) -> str:
        """An attribute as an item card states it: the short sentence where the game has one
        ('11.5/s Fire Rate'), else the full one ('10% of kinetic damage bypasses the shield.'),
        else the value and the attribute's name."""
        meta = self.attributes.get(attribute, {})
        shown = self.format_value(attribute, value, precise=False)
        template = meta.get('short_format') or meta.get('format') or ''
        if '{0}' in template and template != '{0}':
            return _TAGS.sub('', template.replace('{0}', shown))
        if template and '{0}' not in template:
            return _TAGS.sub('', template)
        return '%s %s' % (shown, self.attribute_name(attribute))

    def modifier(self, item_affix: str, catalysts: Iterable[str], attribute: str) -> float:
        """The combined multiplier an item's prefix and catalysts apply to an attribute:
        affixes.csv and catalysts.csv list them as fractions (0.15 is +15%)."""
        factor = 1.0
        sources = [self.affixes.get(item_affix, {})] + [self.catalysts.get(c, {}) for c in catalysts]
        for source in sources:
            change = source.get('modifiers', {}).get(attribute)
            if isinstance(change, (int, float)):
                factor *= 1 + change
        return factor

    # ---- affixes and catalysts

    def _in_scope(self, scope: Collection[str], template: str) -> bool:
        info = self.item(template)
        return template in scope or info.get('main_category') in scope or info.get('sub_category') in scope

    def affixes_for(self, template: str) -> list[str]:
        return [a for a, d in self.affixes.items() if self._in_scope(d['applies_to'], template)]

    def catalysts_for(self, template: str) -> list[str]:
        """Catalysts that fit the template and exist as items in the game."""
        return [c for c, d in self.catalysts.items() if d.get('is_item') and self._in_scope(d['applies_to'], template)]

    def description(self, template: str) -> str:
        """A template's description with its {placeholders} filled in. They name attributes
        of the template's own definition, each with a single value (catalysts, perks)."""
        text = self.item(template).get('description', '')

        def value(match: re.Match[str]) -> str:
            v = self.range_value(template, match.group(1), 0.0)
            return match.group(0) if v is None else self.format_value(match.group(1), v, precise=False)

        return _TAGS.sub('', re.sub(r'\{(\w+)\}', value, text))

    def catalyst_description(self, catalyst: str) -> str:
        return self.description(catalyst)

    def attribute_text(self, attribute: str, value: float | None) -> str:
        """The sentence the game shows for an attribute ('Gains 50% shield and armor
        piercing.'), from its format string, or '' when it has none of its own."""
        template = self.attributes.get(attribute, {}).get('format', '')
        if not template or template == '{0}':
            return ''
        text = template.replace('{0}', self.format_value(attribute, value, precise=False))
        return _TAGS.sub('', text)

    def affix_name(self, affix: str) -> str:
        return self.affixes.get(affix, {}).get('name') or affix

    def catalyst_name(self, catalyst: str) -> str:
        name = self._told_apart.get(catalyst) or self.catalysts.get(catalyst, {}).get('name') or self.name(catalyst)
        return name.removeprefix('Catalyst: ')


def _number(value: float) -> str:
    if abs(value - round(value)) < 5e-3 or abs(value) >= 1000:
        return '%d' % round(value)
    return ('%.2f' % value).rstrip('0').rstrip('.')
