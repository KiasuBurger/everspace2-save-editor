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

"""Builds the catalogue of the game's identifiers, game_identifiers.json, from the game.

The game defines items, affixes, catalysts, attributes and sets in DataFiles/*.CSV inside
pakchunk0-Windows.pak (see pak.py). The files have no header row and use '|~' as the column
separator.

The ship colours, decals and appearance modules a player can unlock are not in those files
but in data tables among the assets in the game's containers (see gameicons.py).

    write(game, path)       writes the catalogue of the game installed at `game`

gamedata.py is what reads the catalogue; the editor writes it to the cache when it first
runs, and scripts/game_identifiers.py writes it out with a readable form of it.
"""

import collections
import json
import os
import re
import struct
from collections.abc import Sequence
from typing import Any

from . import gameicons, pak

Number = float | str  # a cell of a data file: a number where it is one
Table = dict[str, dict[str, Any]]  # the rows of a table by their IDs

DATA_FILES = 'ES2/Content/DataFiles/'
DATA_TABLES = '../../../ES2/Content/Blueprints/DataTables/'
TABLE_KEYS = ('ship_colors', 'ship_decals', 'ship_modules')
COLOR_TYPES = ['hull', 'emissive', 'window', 'trail']  # EShipColorType, as DT_ShipColors numbers it
SHIP_CLASSES = ['Light', 'Medium', 'Heavy']
TABLE_NAMES = ['DT_ShipColors', 'DT_ShipDecals'] + ['DT_ShipModules_%s_EA' % c for c in SHIP_CLASSES]


def load(files: dict[str, bytes], name: str) -> list[list[str]]:
    """Rows of a data file as lists of stripped strings (file names differ in case)."""
    data = next((d for f, d in files.items() if f.lower() == name.lower()), None)
    if data is None:
        raise ValueError('the game has no data file %s' % name)
    text = data.decode('cp1252')
    return [[c.strip() for c in line.split('|~')] for line in text.split('\r\n') if line]


def clean(text: str) -> str:
    """'{0}|genderedNoun(Energizer SP,n)' -> 'Energizer SP'; strips rich-text tags."""
    m = re.search(r'\|gender\w+\(([^,)]*)', text)
    if m:
        return m.group(1)
    return re.sub(r'</?[A-Za-z_]*>', '', text).strip()


def id_list(cell: str) -> list[str]:
    return [c.strip() for c in cell.split(',') if c.strip()]


def number(s: str) -> Number:
    try:
        v = float(s)
    except ValueError:
        return s
    return int(v) if v == int(v) else v


def read_attributes(files: dict[str, bytes]) -> tuple[Table, list[str]]:
    """attributes.CSV is transposed: one attribute per group of three columns."""
    rows = load(files, 'attributes.CSV')
    order = []
    out = collections.OrderedDict()
    for col in range(0, len(rows[0]), 3):
        attr = rows[0][col]
        order.append(attr)
        if attr:
            # rows: 0 id, 1 name, 2 sentence, 3 short sentence (used on weapon cards), 5 sign shown
            # before the value and unit (second and third cell), 6 growth per level, how it
            # is applied ('*' multiplies, 'c' accumulates) and the power the growth is raised
            # to first (1 for all but the slot counts of ships), 7 type and decimals shown
            out[attr] = {
                'name': rows[1][col],
                'format': rows[2][col],
                'short_format': rows[3][col],
                'prefix': rows[5][col + 1] if col + 1 < len(rows[5]) else '',
                'type': rows[7][col],
                'unit': rows[5][col + 2] if col + 2 < len(rows[5]) else '',
                'decimals': int(rows[7][col + 1] or 0),
                'growth': number(rows[6][col]) if rows[6][col] else 1,
                'growth_op': rows[6][col + 1],
                'growth_exp': number(rows[6][col + 2]) if col + 2 < len(rows[6]) and rows[6][col + 2] else 1,
            }
    return out, order


def read_items(files: dict[str, bytes], attr_order: list[str]) -> tuple[Table, Table]:
    """items.CSV: main category and sub-category are given once and apply to the rows below.
    Columns: 0 main category, 1 its name, 2 sub-category, 3 its name, 4 id, 5 name,
    6 blueprint class, 7 icon, 8 description, 9 legendary effect text, 10 set/manufacturer,
    11 min rarity, 12 max rarity, 13.. one (min, operator, max) triple per attribute."""
    items = collections.OrderedDict()
    categories: Table = collections.OrderedDict()
    main = sub = ''
    for r in load(files, 'items.CSV'):
        if r[0]:
            main, sub = r[0], ''
            categories[main] = {'name': r[1], 'sub_categories': collections.OrderedDict()}
        if r[2]:
            sub = r[2]
            categories[main]['sub_categories'][sub] = r[3]
        attrs = collections.OrderedDict()
        for i in range(13, len(r) - 2, 3):
            if r[i] or r[i + 2]:
                attrs[attr_order[(i - 13) // 3]] = [number(r[i]), r[i + 1], number(r[i + 2])]
        items[r[4]] = {
            'name': clean(r[5]),
            'main_category': main,
            'sub_category': sub,
            'description': clean(r[8]),
            'legendary_effect': clean(r[9]),
            'group': r[10],
            'min_rarity': r[11],
            'max_rarity': r[12],
            'blueprint_class': r[6],
            'icon': r[7],
            'attributes': attrs,
        }
    return items, categories


def modifiers(cells: list[str]) -> dict[str, Number]:
    """[attr, value, attr, value, ...] -> {attr: value}"""
    return collections.OrderedDict((cells[i], number(cells[i + 1])) for i in range(0, len(cells) - 1, 2) if cells[i])


def read_affixes(files: dict[str, bytes]) -> Table:
    """affixes.csv: name format, id, allowed categories/items, two numbers, min rarity, modifiers."""
    out = collections.OrderedDict()
    for r in load(files, 'affixes.csv'):
        out[r[1]] = {
            'name': clean(r[0]),
            'applies_to': id_list(r[2]),
            'value_3': number(r[3]),
            'value_4': number(r[4]),
            'min_rarity': r[5],
            'modifiers': modifiers(r[6:]),
        }
    return out


def read_catalysts(files: dict[str, bytes]) -> Table:
    """catalysts.csv: id, min level, allowed categories/items, affix granted, modifiers."""
    out = collections.OrderedDict()
    for r in load(files, 'catalysts.csv'):
        out[r[0]] = {
            'min_level': number(r[1]) if r[1] else None,
            'applies_to': id_list(r[2]),
            'adds_affix': r[3] or None,
            'modifiers': modifiers(r[4:]),
        }
    return out


def read_mutators(files: dict[str, bytes]) -> Table:
    """mutators.csv: id, a weight and three flags. The file does not name its columns. The
    names here come from the data in saves:

    high_risk_areas  only mutators with this flag occur in high-risk areas and on signal decoders
    rifts            the mutators that can occur in an ancient rift have this flag
    rift_bosses      only some mutator_riftboss_* mutators have this flag"""
    out = collections.OrderedDict()
    for r in load(files, 'mutators.csv'):
        out[r[0]] = {
            'weight': number(r[1]),
            'high_risk_areas': r[2] == '1',
            'rifts': r[3] == '1',
            'rift_bosses': r[4] == '1',
        }
    return out


def build_items(files: dict[str, bytes], saves: dict[str, Any] | None = None) -> dict[str, Any]:
    """The catalogue of what is in the data files, {name: bytes}. saves is what
    scripts/identifiers.py found in saves, which fills in the "in saves" entries."""
    saves = saves or {}
    attributes, attr_order = read_attributes(files)
    items, categories = read_items(files, attr_order)
    affixes = read_affixes(files)
    catalysts = read_catalysts(files)
    for cid, c in catalysts.items():
        it = items.get(cid, {})
        c['name'] = it.get('name', '')
        c['description'] = it.get('description', '')
        c['is_item'] = cid in items
        c['seen_on_in_saves'] = list(saves.get('catalyst_scopes', {}).get(cid, {}))
    for aid, a in affixes.items():
        a['catalysts'] = [c for c, d in catalysts.items() if d['adds_affix'] == aid]
        a['seen_on_in_saves'] = list(saves.get('affix_scopes', {}).get(aid, {}))
    seen = {i for fam in saves.get('item_templates', {}).values() for i in fam}
    for iid, it in items.items():
        it['in_saves'] = iid in seen
    sets = collections.OrderedDict(
        (r[0], {'key': r[1], 'values': [v for v in r[2:] if v]}) for r in load(files, 'sets.csv')
    )
    counts = collections.OrderedDict((f, len(load(files, f))) for f in sorted(files, key=str.lower))
    return {
        'affixes': affixes,
        'catalysts': catalysts,
        'categories': categories,
        'items': items,
        'item_sets': sets,
        'attributes': attributes,
        'mutators': read_mutators(files),
        'catalysts_only_in_items': [i for i in items if i.startswith('catalyst_') and i not in catalysts],
        'affixes_only_in_saves': sorted(set(saves.get('affix_scopes', {})) - set(affixes)),
        'legacy_items': sorted(seen - set(items)),
        'data_files': counts,
    }


# ---------------------------------------------------------------- data tables


def name_table(asset: bytes) -> tuple[list[str], int]:
    """The names an asset refers to by number, and where its header ends. The names follow
    the header, whose length depends on the engine's version, as a count, the size of the
    strings, the hash they were made with, a hash and a length for each, and then the
    strings; they are found by that hash's number."""
    for p in range(8, min(len(asset), 256) - 16, 4):
        count, size, algorithm = struct.unpack_from('<IIQ', asset, p)
        if algorithm == 0xC1640000 and count and p + 16 + 10 * count + size <= len(asset):
            break
    else:
        raise ValueError('an asset without names')
    q = p + 16 + 8 * count
    lengths = struct.unpack_from('>%dH' % count, asset, q)
    q += 2 * count
    names = []
    for n in lengths:
        if n & 0x8000:  # UTF-16, at an even place
            n = 2 * (n & 0x7FFF)
            q += q & 1
            names.append(asset[q : q + n].decode('utf-16-le'))
        else:
            names.append(asset[q : q + n].decode('latin1'))
        q += n
    return names, struct.unpack_from('<I', asset, 4)[0]


class TableReader:
    """The rows of a DataTable export. They are written without property tags: a row is its
    name and then, for its struct, a header saying which properties follow."""

    def __init__(self, tables: dict[str, bytes], name: str) -> None:
        if name not in tables:
            raise ValueError('the game has no data table %s' % name)
        self.names, header = name_table(tables[name])
        self.b = tables[name][header:]
        self.o = 10  # past the table object's own properties
        self.rows = self.take('i')

    def take(self, fmt: str) -> Any:
        v = struct.unpack_from('<' + fmt, self.b, self.o)
        self.o += struct.calcsize('<' + fmt)
        return v if len(v) > 1 else v[0]

    def name(self) -> str:
        index, number = self.take('ii')
        return self.names[index] + ('_%d' % (number - 1) if number else '')

    def string(self) -> str:
        n = self.take('i')
        if n < 0:
            s = self.b[self.o : self.o - 2 * n - 2].decode('utf-16-le')
            self.o -= 2 * n
        else:
            s = self.b[self.o : self.o + max(n - 1, 0)].decode('utf-8', 'replace')
            self.o += n
        return s

    def text(self) -> str:
        self.take('i')
        history = self.take('b')
        if history == 0:  # namespace, key, source string
            self.string()
            self.string()
            return self.string()
        if history == -1:
            return self.string() if self.take('i') else ''
        raise ValueError('text history %d' % history)

    def soft_path(self) -> str:
        """The asset's name out of (package, asset, sub-path)."""
        _package, asset = self.name(), self.name()
        self.string()
        return asset

    def present(self) -> dict[int, bool]:
        """{property index: whether its value follows}; an absent one is zero."""
        fragments = []
        while True:
            f = self.take('H')
            fragments.append((f & 0x7F, bool(f & 0x80), f >> 9))
            if f & 0x100:
                break
        bits = sum(count for _skip, zeroes, count in fragments if zeroes)
        mask = 0
        if bits:
            size = 1 if bits <= 8 else 2 if bits <= 16 else 4 * ((bits + 31) // 32)
            mask = int.from_bytes(self.b[self.o : self.o + size], 'little')
            self.o += size
        out, index, bit = {}, 0, 0
        for skip, zeroes, count in fragments:
            index += skip
            for _ in range(count):
                out[index] = not (zeroes and mask >> bit & 1)
                bit += zeroes
                index += 1
        return out


def srgb(linear: Sequence[float]) -> str:
    """'#rrggbb' of a linear colour, which is how the tables hold them."""

    def channel(v: float) -> int:
        v = min(max(v, 0.0), 1.0)
        v = 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055
        return round(v * 255)

    return '#%02x%02x%02x' % tuple(channel(v) for v in linear[:3])


def ship_colors(tables: dict[str, bytes]) -> Table:
    """DT_ShipColors, rows of ColorPreset: display name, what the colour is for, the colour."""
    r = TableReader(tables, 'DT_ShipColors')
    out = collections.OrderedDict()
    for _ in range(r.rows):
        row, has = r.name(), r.present()
        name = r.text() if has.get(0) else ''
        kind = r.take('B') if has.get(1) else 0
        rgba = r.take('4f') if has.get(2) else (0.0, 0.0, 0.0, 1.0)
        out[row] = {'name': name, 'type': COLOR_TYPES[kind], 'color': srgb(rgba)}
    return out


def ship_decals(tables: dict[str, bytes]) -> Table:
    """DT_ShipDecals, rows of ShipDecalTextures: display name, whether it comes with a DLC,
    two textures and the icon."""
    r = TableReader(tables, 'DT_ShipDecals')
    out = collections.OrderedDict()
    for _ in range(r.rows):
        row, has = r.name(), r.present()
        name = r.text() if has.get(0) else ''
        dlc = bool(r.take('B')) if has.get(1) else False
        paths = [r.soft_path() if has.get(i) else 'None' for i in (2, 3, 4)]
        out[row] = {'name': name, 'dlc': dlc, 'icon': '' if paths[2] == 'None' else paths[2]}
    return out


def ship_modules(tables: dict[str, bytes]) -> dict[str, Table]:
    """DT_ShipModules_<class>_EA, which is the table the game uses; the one without the suffix
    lists parts it never offers. {class: {module: {'type', 'name', 'ships', 'extensions'}}}: a row
    is the name the menu shows (a letter for a body, a number for a rear or cockpit), its mesh, the kind of part, the ship models it is for (wings only) and
    its extensions, which are a name and two meshes each."""
    out: dict[str, Table] = collections.OrderedDict()
    for ship_class in SHIP_CLASSES:
        r = TableReader(tables, 'DT_ShipModules_%s_EA' % ship_class)
        modules: Table = collections.OrderedDict()  # in the order the customisation menu shows them
        for _ in range(r.rows):
            row, has = r.name(), r.present()
            name = r.text() if has.get(0) else ''
            if has.get(1):
                r.soft_path()
            if has.get(2):
                r.take('B')
            ships, extensions = [], []
            if has.get(3):  # a set: what to remove, then its members
                r.take('i')
                ships = [r.name().split('::')[-1] for _ in range(r.take('i'))]
            for _ in range(r.take('i') if has.get(4) else 0):
                part = r.present()
                extensions.append(r.name() if part.get(0) else '')
                for i in (1, 2):
                    if part.get(i):
                        r.soft_path()
            modules[row] = {'type': row.rsplit('_', 1)[0], 'name': name, 'ships': ships, 'extensions': extensions}
        if len(r.b) - r.o != 4:
            raise ValueError('DT_ShipModules_%s_EA: %d bytes left over' % (ship_class, len(r.b) - r.o - 4))
        out[ship_class] = modules
    return out


def build_tables(tables: dict[str, bytes]) -> dict[str, Any]:
    """What is in the data tables, {name: the asset's bytes}."""
    try:
        return {
            'ship_colors': ship_colors(tables),
            'ship_decals': ship_decals(tables),
            'ship_modules': ship_modules(tables),
        }
    except struct.error, IndexError, UnicodeDecodeError:
        raise ValueError("the game's data tables are not as expected") from None


# ---------------------------------------------------------------- from the game


def build(game: str, saves: dict[str, Any] | None = None) -> dict[str, Any]:
    """The catalogue of the game installed at `game`. Raises OSError, or ValueError if the
    game's files are not as expected."""
    files = pak.read_files(game, DATA_FILES, lambda name: '/' not in name and name.lower().endswith('.csv'))
    try:
        catalogue = build_items(files, saves)
    except IndexError, KeyError, UnicodeDecodeError:
        raise ValueError("the game's data files are not as expected") from None
    catalogue.update(build_tables(dict(gameicons.read_assets(game, DATA_TABLES, TABLE_NAMES.__contains__))))
    return catalogue


def write(game: str, path: str) -> None:
    """Writes the catalogue of the game installed at `game` to path, whole or not at all."""
    catalogue = build(game)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + '.partial', 'w') as f:
        json.dump(catalogue, f, indent=1)
    os.replace(path + '.partial', path)
