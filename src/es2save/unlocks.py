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

"""What a main save records as unlocked (see "Unlocks" in FORMAT.md).

Ship colours and decals are lists of names. Ship appearance modules are such a list for each
ship class. A wing belongs to one ship model and has an extension for each tier of ship, and
each of those is unlocked under a name of its own. Blueprints are a record per item that
lists the rarities it can be crafted at.

The names come from the game's data tables by way of game_identifiers.json. Everything here
changes the save tree in place, like items.py.
"""

import copy
import re
from collections.abc import Iterable

from . import items
from .gamedata import GameData
from .savegame import Properties

COLORS = 'UnlockedShipColors'
DECALS = 'UnlockedDecals'
MODULES = 'UnlockedShipModules'
BLUEPRINTS = 'BlueprintStats'

COLOR_TYPES = [('hull', 'Hull'), ('emissive', 'Lights'), ('window', 'Cockpit glass'), ('trail', 'Engine trails')]
SHIP_CLASSES = ['Light', 'Medium', 'Heavy']
# the parts a ship is given a choice of; its base and cockpit interior are fixed
MODULE_TYPES = [('Body', 'Body'), ('Rear', 'Rear'), ('Cockpit_Exterior', 'Cockpit')]
WINGS = 'Wings'
# the game's names for the ship models in its tables, and the ones it shows
SHIP_MODELS = {
    'Scout': 'Scout',
    'PatrolShip': 'Vanguard',
    'SpecOps': 'Stinger',
    'Sentinel': 'Sentinel',
    'Interceptor': 'Interceptor',
    'AssaultFighter': 'Striker',
    'Gunship': 'Gunship',
    'Bomber': 'Bomber',
    'Defender': 'Vindicator',
}
TIERS = ['I', 'II', 'III', 'IV']
BLUEPRINTABLE = 'blueprintable'  # the attribute of every item the crafting menu lists
UNCRAFTABLE_EQUIPMENT = ('Legendary', 'Ascendant')  # equipment is crafted up to Superior
THRESHOLD = 3  # dismantling this many of an item at a rarity unlocks it


# ---------------------------------------------------------------- lists of names


def names(props: Properties, key: str) -> list[str]:
    return list(items.get(props, key, []))


def set_name(props: Properties, key: str, name: str, on: bool) -> None:
    """Adds a name to the end of one of the lists, as the game does, or takes it out."""
    have = names(props, key)
    if on and name not in have:
        items.put(props, key, have + [name])
    elif not on and name in have:
        items.put(props, key, [n for n in have if n != name])


def colors(game: GameData, kind: str) -> list[str]:
    """The colours for one use of them ('hull', 'emissive', 'window', 'trail'), in the game's order."""
    return [c for c, info in game.ship_colors.items() if info.get('type') == kind]


def decals(game: GameData) -> list[str]:
    """The decals a save can unlock. The ones of a DLC come with owning it, and one has no
    icon because the game never offers it."""
    return [d for d, info in game.ship_decals.items() if not info.get('dlc') and info.get('icon')]


# ---------------------------------------------------------------- ship modules


def _class_entry(props: Properties, ship_class: str, create: bool = False) -> Properties | None:
    entries = items.get(props, MODULES, [])
    for key, entry in entries:
        if str(key).split('::')[-1] == ship_class:
            return entry
    if not create:
        return None
    if not entries:
        raise KeyError('this save has no ship modules to model a new entry on')
    key, entry = copy.deepcopy(entries[0])
    entry['ModuleIDs']['value'] = []
    entries.append(['%s::%s' % (str(key).split('::')[0], ship_class), entry])
    return entry


def modules(props: Properties, ship_class: str) -> list[str]:
    entry = _class_entry(props, ship_class)
    return list(items.get(entry, 'ModuleIDs', [])) if entry else []


def module_choices(game: GameData, ship_class: str, kind: str) -> list[str]:
    return [m for m, info in game.ship_modules.get(ship_class, {}).items() if info.get('type') == kind]


def set_module(props: Properties, ship_class: str, module: str, on: bool) -> None:
    entry = _class_entry(props, ship_class, create=on)
    if entry is None:
        return
    have = list(items.get(entry, 'ModuleIDs', []))
    if on and module not in have:
        have.append(module)
    elif not on:
        have = [m for m in have if m != module]
    items.put(entry, 'ModuleIDs', have)


def module_name(game: GameData, ship_class: str, module: str) -> str:
    """What the customisation menu calls a part: a wing has a name, a body a letter, a rear
    or cockpit a number."""
    return game.ship_modules.get(ship_class, {}).get(module, {}).get('name') or module


def wing_families(game: GameData, ship_class: str) -> list[tuple[str, list[str]]]:
    """[(ship model as the game shows it, [wing, ...]), ...] for the models of a class."""
    table = game.ship_modules.get(ship_class, {})
    wings = [m for m, info in table.items() if info.get('type') == WINGS and info.get('extensions')]
    models = [s for s in SHIP_MODELS if any(s in table[w].get('ships', []) for w in wings)]
    models += sorted({s for w in wings for s in table[w].get('ships', [])} - set(models))
    return [(SHIP_MODELS.get(s, s), [w for w in wings if s in table[w].get('ships', [])]) for s in models]


def wing_tiers(game: GameData, ship_class: str, wing: str) -> list[tuple[str, str]]:
    """[(tier, extension), ...]: the forms of a wing, one for each tier of ship."""
    out: list[tuple[str, str]] = []
    for extension in game.ship_modules.get(ship_class, {}).get(wing, {}).get('extensions', []):
        m = re.search(r'_T(\d)_', extension)
        tier = int(m.group(1)) if m else len(out) + 1
        out.append((TIERS[tier - 1] if 0 < tier <= len(TIERS) else str(tier), extension))
    return out


def set_wing(props: Properties, game: GameData, ship_class: str, wing: str, extension: str, on: bool) -> None:
    """Unlocks a wing for one tier of ship. The wing's own name is listed for as long as any
    of its tiers is."""
    entry = _class_entry(props, ship_class, create=on)
    if entry is None:
        return
    have = list(items.get(entry, 'ModuleIDs', []))
    if on:
        have += [m for m in (wing, extension) if m not in have]
    else:
        have = [m for m in have if m != extension]
        if not {e for _tier, e in wing_tiers(game, ship_class, wing)} & set(have):
            have = [m for m in have if m != wing]
    items.put(entry, 'ModuleIDs', have)


# ---------------------------------------------------------------- blueprints


def blueprint_items(game: GameData) -> list[str]:
    return [t for t, info in game.items.items() if BLUEPRINTABLE in info.get('attributes', {})]


def blueprint_rarities(game: GameData, template: str) -> list[str]:
    """The rarities an item can be crafted at."""
    rarities = [r for r in game.rarities(template) if r != 'None']
    if game.is_equipment(template):
        rarities = [r for r in rarities if r not in UNCRAFTABLE_EQUIPMENT]
    return rarities


def blueprint_sections(game: GameData) -> list[tuple[str, list[str]]]:
    """[(title, [template, ...]), ...]: the blueprints as the crafting menu sorts them."""
    titles = {
        'cat_weapon': 'Primary weapons',
        'cat_launcher': 'Secondary weapons',
        'cat_consumable': 'Consumables',
        'cat_resource_tec': 'Components',
        'cat_crafting_catalyst': 'Catalysts',
    }
    sections: dict[str, list[str]] = {
        title: []
        for title in ['Primary weapons', 'Secondary weapons', 'Modules', 'Consumables', 'Components', 'Catalysts']
    }
    for template in blueprint_items(game):
        info = game.item(template)
        title = titles.get(info.get('main_category', '')) or titles.get(info.get('sub_category', ''))
        if not title:
            title = 'Modules' if game.is_equipment(template) else game.category_name(template) or 'Other'
        sections.setdefault(title, []).append(template)
    return [(title, members) for title, members in sections.items() if members]


def _stat(props: Properties, template: str) -> Properties | None:
    return next((stat for key, stat in items.get(props, BLUEPRINTS, []) if key == template), None)


def _short(values: Iterable[object]) -> list[str]:
    return [str(v).split('::')[-1] for v in values]


def unlocked_rarities(props: Properties, template: str) -> list[str]:
    stat = _stat(props, template)
    return _short(items.get(stat, 'UnlockedRarities', [])) if stat else []


def set_blueprint(props: Properties, template: str, rarity: str, on: bool) -> None:
    """Makes an item craftable at a rarity or not. A new record is modelled on one the save
    has, because how the fields are typed differs between versions of the game."""
    entries = items.get(props, BLUEPRINTS)
    if entries is None:
        raise KeyError('this save predates blueprints')
    stat = _stat(props, template)
    if stat is None:
        if not on:
            return
        if not entries:
            raise KeyError('this save has no blueprint to model a new one on')
        stat = copy.deepcopy(entries[0][1])
        for key, field in stat.items():
            field['value'] = template if key == 'ItemTemplateID' else []
        entries.append([template, stat])
    full = 'EItemRarity::' + rarity
    for key in ('UnlockedRarities', 'MarkedAsNewRarities'):
        if key not in stat:
            continue
        have = [v for v in stat[key]['value'] if _short([v]) != [rarity]]
        if on and key == 'UnlockedRarities':
            have.append(full)
        stat[key]['value'] = have
