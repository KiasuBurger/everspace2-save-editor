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

"""A ship's level and the parts it is built from (see "Ships" in FORMAT.md).

A ship is a ShipDataState: its level is the ItemLevel of its own item, which also holds its
passives. The game shows the level as I, I+, II, ... IV: every second level is a tier, which
brings a passive and a form of the wings of its own. The parts a ship was built with are the
modules of its original style; the model the game shows is made of the names of four of
them, as <wings>-<front><rear>-<cockpit>. The custom styles change how the ship looks and
are left to the game, but for wings above the ship's tier, which they cannot have. The Okkar
ship, the Wraith, is fixed: it comes at the highest level with all five of its passives and
has no parts to choose.

Everything here changes the save tree in place, like items.py.
"""

import math
import random

from . import itemrules, items, perks, unlocks
from .gamedata import GameData
from .items import Inventory, Item
from .savegame import Properties

MAX_LEVEL = 7
# the kinds of part in a ShipModules list that can be changed, as (EShipModule, the kind of
# row in DT_ShipModules_<class>_EA, what the customisation menu calls it)
PARTS = [
    ('Body', 'Body', 'Front'),
    ('Rear', 'Rear', 'Rear'),
    ('Cockpit', 'Cockpit_Exterior', 'Cockpit'),
    ('Wings', unlocks.WINGS, 'Wings'),
]
WINGS = 'Wings'
EXTENSION = 'Extension'
ORIGINAL = 'OriginalAppearance'
OKKAR = 'cat_ship_okkar'


def ship_item(ship: Properties) -> Item:
    return items.get(ship, 'ShipItemState', {})


def level(ship: Properties) -> int:
    return items.get(ship_item(ship), 'ItemLevel', 1) or 1


def tier(ship_level: int) -> int:
    """The tier of ship at a level, which decides its passives and the form of its wings.
    UGameplayLib::GetShipTierAtShipLevel rounds up the level's share of the highest level
    times the four tiers; every ship in the saves has the tier of a highest level of 8."""
    return max(1, min(len(unlocks.TIERS), math.ceil(ship_level / 2)))


def level_name(ship_level: int) -> str:
    """The level as the game shows it: the tier, with a + on the second level of it."""
    return unlocks.TIERS[tier(ship_level) - 1] + ('+' if ship_level % 2 == 0 else '')


def ship_class(game: GameData, ship: Properties) -> str | None:
    """Light, Medium or Heavy; None for a ship without parts to choose (the Okkar one)."""
    sub = game.item(items.template(ship_item(ship))).get('sub_category', '')
    name = sub.removeprefix('cat_ship_').capitalize()
    return name if name in unlocks.SHIP_CLASSES and name in game.ship_modules else None


def fixed(game: GameData, ship: Properties) -> bool:
    """Whether the ship comes only as it is: the Wraith, of the Okkar class."""
    sub = game.item(items.template(ship_item(ship))).get('sub_category', '')
    return sub.startswith(OKKAR)


def hull(game: GameData, ship: Properties) -> str | None:
    """The game's name for the ship model in its tables (Gunship, Defender), which its
    wings must be made for."""
    name = game.name(items.template(ship_item(ship)))
    return next((key for key, shown in unlocks.SHIP_MODELS.items() if shown == name), None)


# ---------------------------------------------------------------- parts


def _wears_original(ship: Properties) -> bool:
    return items.get(ship, 'AppearanceID') == ORIGINAL


def _built(ship: Properties) -> list[list[Properties]]:
    """The ShipModules lists that hold the parts the ship was built with: its original
    style's, and its own while it wears that style. Saves from before styles held modules
    have empty lists in them and only the ship's own."""
    original = items.get(items.get(ship, ORIGINAL) or {}, 'ShipModules')
    if not original:
        return [items.get(ship, 'ShipModules', [])]
    return [original] + ([items.get(ship, 'ShipModules', [])] if _wears_original(ship) else [])


def _worn(ship: Properties) -> list[list[Properties]]:
    """Every other ShipModules list: the custom styles' and the ship's own unless it wears
    its original style."""
    out = [items.get(s, 'ShipModules') for s in items.get(ship, 'CustomAppearances') or []]
    built = _built(ship)
    own = items.get(ship, 'ShipModules', [])
    return [lst for lst in out + [own] if lst and not any(lst is b for b in built)]


def _kind(entry: Properties) -> str:
    return str(items.get(entry, 'Type', '')).split('::')[-1]


def _parts(lst: list[Properties]) -> dict[str, str]:
    return {_kind(m): items.get(m, 'ID') for m in lst}


def modules(ship: Properties) -> dict[str, str]:
    """{EShipModule: module ID} of the parts the ship was built with."""
    return _parts(_built(ship)[0])


def _put(lst: list[Properties], kind: str, module: str) -> None:
    for entry in lst:
        if _kind(entry) == kind:
            items.put(entry, 'ID', module)


def part_name(game: GameData, ship: Properties, kind: str) -> str:
    """What the customisation menu calls the part of a kind the ship was built with."""
    cls = ship_class(game, ship)
    module = modules(ship).get(kind)
    return unlocks.module_name(game, cls, module) if cls and module else ''


def model_name(game: GameData, ship: Properties) -> str | None:
    """The ship's model as the game shows it, Dominator-G4-3 IV, or None if it has no parts."""
    wings, front, rear, cockpit = (part_name(game, ship, kind) for kind in ('Wings', 'Body', 'Rear', 'Cockpit'))
    if not (wings and front and rear and cockpit):
        return None
    return '%s-%s%s-%s %s' % (wings, front, rear, cockpit, level_name(level(ship)))


def part_choices(game: GameData, ship: Properties, kind: str) -> list[str]:
    """The modules a ship can be built with as a part, in the order of the customisation
    menu. Wings are made for one ship model (UItemLib::GetCompatibleShipModuleIDsForShip)."""
    cls = ship_class(game, ship)
    row_type = next((t for k, t, _ in PARTS if k == kind), None)
    if not cls or not row_type:
        return []
    choices = unlocks.module_choices(game, cls, row_type)
    model = hull(game, ship)
    if kind == WINGS and model:
        table = game.ship_modules[cls]
        choices = [w for w in choices if model in table[w].get('ships', [])]
    return choices


def wing_tier(ship: Properties) -> int | None:
    """The tier of the form of the wings the ship was built with."""
    extension = modules(ship).get(EXTENSION)
    return unlocks.extension_tier(extension) if extension else None


def _extension(game: GameData, cls: str, wing: str, wanted: int) -> str | None:
    """The form of a wing for a tier, or the highest one below it there is."""
    forms = [(unlocks.extension_tier(e) or 0, e) for _t, e in unlocks.wing_tiers(game, cls, wing)]
    fitting = [(t, e) for t, e in forms if t <= wanted]
    return max(fitting)[1] if fitting else None


def set_part(game: GameData, ship: Properties, kind: str, module: str) -> None:
    """Builds the ship with another part. Wings take the form of the ship's tier."""
    cls = ship_class(game, ship)
    if not cls:
        raise ValueError('this ship has no parts to choose')
    extension = _extension(game, cls, module, tier(level(ship))) if kind == WINGS else None
    for lst in _built(ship):
        _put(lst, kind, module)
        if extension:
            _put(lst, EXTENSION, extension)


# ---------------------------------------------------------------- level


def set_level(game: GameData, ship: Properties, inventory: Inventory, new: int) -> list[str]:
    """Changes the ship's level and what follows from it, as the game would have it on a
    ship bought at that level: one passive more or less for every tier gained or lost, the
    wings it was built with in the form of its new tier and none above it in its styles, and
    the device slots and hold it gets. Returns notes on what else changed."""
    if fixed(game, ship):
        raise ValueError('the Wraith comes only at level %s' % level_name(MAX_LEVEL))
    if not 1 <= new <= MAX_LEVEL:
        raise ValueError('a ship level is 1 to %d' % MAX_LEVEL)
    item = ship_item(ship)
    before = perks.passive_count(item)
    items.put(item, 'ItemLevel', new)
    notes = []
    change = perks.passive_count(item) - before
    for _ in range(change):
        spare = itemrules.addable_stats(game, item)
        if spare:
            passive = random.choice(spare)
            items.add_attribute(item, passive, 0.5, list(game.attributes))
            notes.append('gained the passive %s' % game.attribute_name(passive))
    for _ in range(-change):
        have = perks.passives(game, item)
        if have:
            items.remove_attribute(item, have[-1])
            notes.append('lost the passive %s' % game.attribute_name(have[-1]))
    cls = ship_class(game, ship)
    if cls:
        notes += _fit_wings(game, ship, cls, tier(new))
    before_devices = len(inventory.slots.get(items.DEVICES, []))
    for moved in inventory.fit_devices(game):
        notes.append('%s moved to the hold' % game.name(items.template(moved)))
    after_devices = len(inventory.slots.get(items.DEVICES, []))
    if after_devices != before_devices:
        notes.append('%d device slots' % after_devices)
    capacity = inventory.cargo_capacity(game)
    if inventory.fit_cargo(game):
        notes.append('%d cargo slots' % len(inventory.cargo))
    if capacity is not None and len(inventory.cargo) > capacity:
        notes.append('%d more in the hold than the ship holds' % (len(inventory.cargo) - capacity))
    return notes


def _fit_wings(game: GameData, ship: Properties, cls: str, new: int) -> list[str]:
    """After a change of level: the wings the ship was built with take the form of its tier,
    and the styles lose any above it."""
    before = wing_tier(ship)
    for lst, built in [(lst, True) for lst in _built(ship)] + [(lst, False) for lst in _worn(ship)]:
        parts = _parts(lst)
        wing, extension = parts.get(WINGS), parts.get(EXTENSION)
        have = unlocks.extension_tier(extension) if extension else None
        if not wing or not have or (not built and have <= new):
            continue
        fitting = _extension(game, cls, wing, new)
        if fitting:
            _put(lst, EXTENSION, fitting)
    after = wing_tier(ship)
    return ['wings of tier %s' % unlocks.TIERS[after - 1]] if after and after != before else []
