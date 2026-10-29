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

"""Reading and changing items in a save tree.

An item is the property list of an ItemState struct, as produced by savegame.load. The
functions here change it in place and keep the fields that depend on each other in step
(see "Item mechanics" in FORMAT.md): rarity, grade and VirtualLevelOffset; the Ascendant
rank; a reliquary's radiant blessing; the order of attributes; the Chaos catalyst and its seed; the Flawless catalyst and the
positions it replaces; the attributes a Mining, Okkar or Evolution catalyst adds; an Evolution catalyst's kills
and what they have earned; the Intuitive prefix and the flag that lifts the level requirement.

Inventories hold a fixed list of slots. An empty slot is an item whose ItemTemplateID is
"None", so removing an item blanks its slot and adding one fills a blank slot.
"""

import copy
import random
from collections.abc import Sequence
from typing import Any

from .gamedata import RARITIES, GameData, level_scale
from .savegame import Properties, Save, text_source

# the property list of an ItemState struct
Item = Properties

GRADES = ['None', 'Prototype', 'Starforged', 'Radiant']
# grades seen on each rarity in saves written by the game
GRADES_FOR = {
    'Rare': ['None', 'Prototype'],
    'Superior': ['None', 'Prototype', 'Starforged'],
    'Legendary': ['None', 'Radiant'],
}
# number of level-scaled bonus stats items of each rarity carry in saves written by the game
BONUS_STATS = {'Common': 0, 'Uncommon': 1, 'Rare': 2, 'Superior': 3, 'Ascendant': 3, 'Legendary': 4}
# VirtualLevelOffset by (rarity, grade); Ascendant adds 0.2 per upgrade
LEVEL_OFFSETS = {
    ('None', 'None'): 0.0,
    ('Common', 'None'): 0.0,
    ('Uncommon', 'None'): 1.4,
    ('Rare', 'None'): 2.2,
    ('Rare', 'Prototype'): 3.2,
    ('Superior', 'None'): 3.8,
    ('Superior', 'Prototype'): 4.8,
    ('Superior', 'Starforged'): 5.8,
    ('Legendary', 'None'): 6.0,
    ('Legendary', 'Radiant'): 7.0,
}
ASCENDANT_BASE_OFFSET = 4.6
ASCENDANT_MAX_COUNT = 7
ASCENDANT_RANK = 'ascendant_rank'
MAX_AMMO = 'secondary_max_ammo'  # a secondary weapon's ammo when full
ASCENDANT_COUNT_KEY = 'AscendantUpgradeCount'
RADIANT_BLESSING = 'radiant_blessing_'  # prefix of the extra attribute a Radiant reliquary carries
CHAOS = 'catalyst_legendary_chaos'
FLAWLESS = 'catalyst_legendary_flawless'
FLAWLESS_ATTRIBUTES = frozenset(
    (  # the ones the game's code names
        'legendary_kinetic_damage',
        'legendary_energy_damage',
        'legendary_energy',
        'legendary_effect',
        'legendary_effect_2',
        'legendary_effect_3',
        'legendary_count',
        'legendary_range',
        'legendary_time',
    )
)
MINING = 'catalyst_mining'
MINING_ATTRIBUTES = ('weapon_mining_quality', 'weapon_mining_refund', 'weapon_mining_quantity')
EVOLUTION = 'catalyst_evolution'
EVOLUTION_KILLS = 'evolution_catalyst_buff'  # the CustomData key of the kill count
EVOLUTION_BONUS = 'evolution_catalyst_buff'  # the attribute whose position is the damage bonus
EVOLUTION_KILL_COUNT = 'evolution_catalyst_kill_count'
EVOLUTION_ATTRIBUTES = (EVOLUTION_BONUS, EVOLUTION_KILL_COUNT)
EVOLUTION_STEPS = ((300, 0.2), (100, 0.1))  # kills, and the damage bonus from then on
EVOLUTION_EARNS = 1000  # kills at which the item gets one more bonus attribute
OKKAR = 'catalyst_okkar_'
HEXASPATIAL = 'catalyst_ancient_energy_shielding'  # the one a legendary takes that is not legendary
SOVEREIGN = 'catalyst_legendary_sovereign'
SOVEREIGN_ATTRIBUTE = 'sovereign_legendary_catalyst_buff_display'  # shows the levels it gives
SOVEREIGN_LEVELS = 2.0  # less half a level for each set bonus the ship has active
# The catalysts that change the item's attributes, which the game multiplies them by again for
# each copy the save lists. The others are switches, which the game only looks for in the list
# (or set something once), or add attributes, which an item has once.
REPEATABLE = frozenset(
    {
        'catalyst_damage_uncommon',
        'catalyst_damage_rare',
        'catalyst_damage_energy_rare',
        'catalyst_module_enhancement',
        'catalyst_repair_protocol',
    }
)
INTUITIVE = 'intuitive'
ANY_LEVEL = 'CanBeUsedWithAnyLevel'
ORIGINAL_POSITIONS = 'AttributeOriginalPositionInRange'
UNSET_SEED = 2147483647
EMPTY = 'None'

SLOT_NAMES = {
    'PrimaryWeapons': 'Primary weapons',
    'SecondaryWeapons': 'Secondary weapons',
    'EnergyCores': 'Energy core',
    'Shields': 'Shield',
    'Platings': 'Plating',
    'Sensors': 'Sensor',
    'thrusters': 'Booster',
    'CargoUnits': 'Cargo unit',
    'Devices': 'Devices',
    'Consumables': 'Consumables',
}
# the main category an equipment slot accepts
SLOT_CATEGORY = {
    'PrimaryWeapons': 'cat_weapon',
    'SecondaryWeapons': 'cat_launcher',
    'EnergyCores': 'cat_energy_core',
    'Shields': 'cat_shield',
    'Platings': 'cat_plating',
    'Sensors': 'cat_sensor',
    'thrusters': 'cat_thrusters',
    'CargoUnits': 'cat_cargo_unit',
    'Devices': 'cat_device',
    'Consumables': 'cat_consumable',
}
CARGO = 'Cargo'
DEVICES = 'Devices'
# a ship's Device Slots would round to five at level 7, but every ship in the saves has four
MAX_DEVICE_SLOTS = 4


# ---------------------------------------------------------------- fields


def get(item: Item, key: str, default: Any = None) -> Any:
    return item[key]['value'] if key in item else default


def put(item: Item, key: str, value: Any) -> None:
    """Sets an existing field. Fields are never invented: their type differs between saves."""
    if key not in item:
        raise KeyError('item has no %s field' % key)
    item[key]['value'] = value


def template(item: Item) -> str:
    return get(item, 'ItemTemplateID', EMPTY)


def is_empty(item: Item) -> bool:
    return template(item) == EMPTY


def _enum(item: Item, key: str) -> str:
    return str(get(item, key, '::None')).split('::')[-1]


def _put_enum(item: Item, key: str, short: str, enum_name: str) -> None:
    current = str(get(item, key, ''))
    prefix = current.split('::')[0] if '::' in current else enum_name
    put(item, key, '%s::%s' % (prefix, short))


def rarity(item: Item) -> str:
    return _enum(item, 'Rarity')


def grade(item: Item) -> str:
    return _enum(item, 'Grade') if 'Grade' in item else 'None'


def grades_for(rarity_name: str) -> list[str]:
    return GRADES_FOR.get(rarity_name, ['None'])


def level_offset(rarity_name: str, grade_name: str = 'None', ascendant_count: int = 0) -> float | None:
    """The VirtualLevelOffset the game gives this combination, or None if it was never seen."""
    if rarity_name == 'Ascendant':
        return round(ASCENDANT_BASE_OFFSET + 0.2 * ascendant_count, 1) if grade_name == 'None' else None
    return LEVEL_OFFSETS.get((rarity_name, grade_name))


def _sync_offset(item: Item, bonus: float) -> None:
    """Gives the item the offset of its rarity and grade, plus `bonus`, the Sovereign levels
    it had before they were changed (see sovereign_bonus)."""
    offset = level_offset(rarity(item), grade(item), ascendant_count(item) or 0)
    if offset is not None and 'VirtualLevelOffset' in item:
        put(item, 'VirtualLevelOffset', offset + bonus)


def sovereign_bonus(item: Item) -> float:
    """The levels the Sovereign catalyst is giving an item: what its VirtualLevelOffset holds
    beyond the offset of its rarity and grade. The game works it out again whenever it
    rebuilds the item, from the set bonuses then active."""
    if SOVEREIGN not in get(item, 'Catalysts', []):
        return 0.0
    base = level_offset(rarity(item), grade(item), ascendant_count(item) or 0)
    if base is None or 'VirtualLevelOffset' not in item:
        return SOVEREIGN_LEVELS
    return min(max(get(item, 'VirtualLevelOffset') - base, 0.0), SOVEREIGN_LEVELS)


def set_rarity(item: Item, rarity_name: str) -> None:
    """Changes the rarity, dropping a grade the new rarity cannot have and adding or removing
    the Ascendant rank."""
    if rarity_name not in RARITIES:
        raise ValueError('unknown rarity %r' % rarity_name)
    bonus = sovereign_bonus(item)
    _put_enum(item, 'Rarity', rarity_name, 'EItemRarity')
    if 'Grade' in item and grade(item) not in grades_for(rarity_name):
        _put_enum(item, 'Grade', 'None', 'EItemGrade')
    if rarity_name == 'Ascendant':
        set_ascendant_count(item, ascendant_count(item) or 0)
    else:
        remove_attribute(item, ASCENDANT_RANK)
        _remove_custom(item, ASCENDANT_COUNT_KEY)
    _sync_offset(item, bonus)


def set_grade(item: Item, grade_name: str) -> None:
    if grade_name not in GRADES:
        raise ValueError('unknown grade %r' % grade_name)
    if 'Grade' not in item:
        if grade_name != 'None':
            raise KeyError('this save predates item grades')
        return
    bonus = sovereign_bonus(item)
    _put_enum(item, 'Grade', grade_name, 'EItemGrade')
    _sync_offset(item, bonus)


# ---------------------------------------------------------------- custom data


def custom(item: Item, key: str) -> Any:
    """The decoded value of a CustomData entry, or None."""
    for k, v in get(item, 'CustomData', []):
        if k == key:
            return v.get('value') if isinstance(v, dict) else v
    return None


def _put_custom(item: Item, key: str, variant: str, value: Any) -> None:
    entries = get(item, 'CustomData')
    if entries is None:
        return
    for entry in entries:
        if entry[0] == key:
            entry[1] = {'variant': variant, 'value': value}
            return
    entries.append([key, {'variant': variant, 'value': value}])


def _remove_custom(item: Item, key: str) -> None:
    entries = get(item, 'CustomData')
    if entries is not None:
        entries[:] = [e for e in entries if e[0] != key]


def ascendant_count(item: Item) -> int | None:
    """Upgrade count (0 to 7) of an Ascendant item, or None for other items."""
    if rarity(item) != 'Ascendant':
        return None
    count = custom(item, ASCENDANT_COUNT_KEY)
    rank = position(item, ASCENDANT_RANK)
    if count is None and rank is not None:
        count = round(rank * ASCENDANT_MAX_COUNT)
    return count or 0


def set_ascendant_count(item: Item, count: float) -> None:
    """Sets the three values that describe an Ascendant item's rank so that they agree."""
    count = max(0, min(ASCENDANT_MAX_COUNT, int(count)))
    bonus = sovereign_bonus(item)
    if position(item, ASCENDANT_RANK) is None:
        _states(item).append(_state(ASCENDANT_RANK, 0.0))  # the game keeps it at the end
    set_position(item, ASCENDANT_RANK, count / ASCENDANT_MAX_COUNT)
    _put_custom(item, ASCENDANT_COUNT_KEY, 'int32', count)
    _sync_offset(item, bonus)


# ---------------------------------------------------------------- attributes


def _states(item: Item) -> list[Properties]:
    return get(item, 'AttributeStates', [])


def _state(name: str, pos: float) -> Properties:
    return {
        'AttributeTemplateID': {'type': 'NameProperty', 'value': name},
        'ValuePositionInRange': {'type': 'FloatProperty', 'value': float(pos)},
    }


def attributes(item: Item) -> list[tuple[str, float]]:
    """[(name, position)] in stored order."""
    return [(s['AttributeTemplateID']['value'], s['ValuePositionInRange']['value']) for s in _states(item)]


def position(item: Item, name: str) -> float | None:
    for s in _states(item):
        if s['AttributeTemplateID']['value'] == name:
            return s['ValuePositionInRange']['value']
    return None


def set_position(item: Item, name: str, pos: float) -> None:
    for s in _states(item):
        if s['AttributeTemplateID']['value'] == name:
            s['ValuePositionInRange']['value'] = float(pos)
            return
    raise KeyError('item has no attribute %s' % name)


def sort_attributes(item: Item, order: Sequence[str]) -> None:
    """Puts the attributes into the game's order (`order` lists attribute IDs as the game
    orders them); attributes not in it keep their relative order at the end."""
    rank = {name: i for i, name in enumerate(order)}
    states = _states(item)
    states.sort(key=lambda s: rank.get(s['AttributeTemplateID']['value'], len(rank)))


def add_attribute(item: Item, name: str, pos: float, order: Sequence[str] = ()) -> None:
    """Adds an attribute at its place in the game's order: in front of the first attribute
    that comes after it there. The others are left where they are."""
    if position(item, name) is not None:
        raise ValueError('item already has %s' % name)
    states = _states(item)
    rank = {n: i for i, n in enumerate(order)}
    index = len(states)
    if name in rank:
        for i, s in enumerate(states):
            if rank.get(s['AttributeTemplateID']['value'], -1) > rank[name]:
                index = i
                break
    states.insert(index, _state(name, pos))


def remove_attribute(item: Item, name: str) -> None:
    states = get(item, 'AttributeStates')
    if states is not None:
        states[:] = [s for s in states if s['AttributeTemplateID']['value'] != name]
    for key in ('AttributeOriginalValue', 'AttributeOriginalPositionInRange'):
        entries = get(item, key)
        if entries:
            entries[:] = [e for e in entries if e[0] != name]
    if custom(item, EVOLUTION) == name:
        _remove_custom(item, EVOLUTION)  # it was the one an Evolution catalyst earned


def replace_attribute(item: Item, old: str, new: str, order: Sequence[str] = ()) -> None:
    """Swaps one attribute for another, keeping its position value. The attribute an
    Evolution catalyst earned stays the earned one under its new name."""
    pos = position(item, old)
    if pos is None:
        raise KeyError('item has no attribute %s' % old)
    earned = custom(item, EVOLUTION) == old
    remove_attribute(item, old)
    add_attribute(item, new, pos, order)
    if earned:
        _put_custom(item, EVOLUTION, 'name', new)


def is_radiant_blessing(name: str) -> bool:
    return name.startswith(RADIANT_BLESSING)


def radiant_blessing(item: Item) -> str | None:
    """The radiant blessing of a reliquary, or None."""
    for name, _ in attributes(item):
        if is_radiant_blessing(name):
            return name
    return None


def set_radiant_blessing(item: Item, blessing: str | None, order: Sequence[str] = ()) -> None:
    """Makes a reliquary Radiant with the given radiant blessing, or plain again with None.
    The grade, the level offset and the one extra attribute are kept in step."""
    if blessing is not None and not is_radiant_blessing(blessing):
        raise ValueError('%r is not a radiant blessing' % blessing)
    set_grade(item, 'Radiant' if blessing else 'None')
    current = radiant_blessing(item)
    if blessing and current:
        if blessing != current:
            replace_attribute(item, current, blessing, order)
        return
    for name, _ in attributes(item):
        if is_radiant_blessing(name):
            remove_attribute(item, name)
    if blessing:
        # the range is a single value, so the position (which the game rolls) changes nothing
        add_attribute(item, blessing, random.random(), order)


# ---------------------------------------------------------------- affix and catalysts


def affix(item: Item) -> str:
    return get(item, 'AffixID', EMPTY)


def set_affix(item: Item, affix_id: str | None) -> None:
    """Sets the prefix. Intuitive is the one that lifts the level requirement, which the save
    holds as a flag of its own; leaving Intuitive takes the flag back."""
    was = affix(item)
    put(item, 'AffixID', affix_id or EMPTY)
    if ANY_LEVEL in item and INTUITIVE in (was, affix_id) and was != affix_id:
        put(item, ANY_LEVEL, affix_id == INTUITIVE)


def catalysts(item: Item) -> list[str]:
    return list(get(item, 'Catalysts', []))


def has_chaos(item: Item) -> bool:
    return CHAOS in get(item, 'Catalysts', [])


def catalyst_seed(item: Item) -> int | None:
    """The Chaos seed, or None when the item has none."""
    seed = get(item, 'ChangedCatalystSeed')
    return None if seed in (None, 0, UNSET_SEED) else seed


def set_catalyst_seed(item: Item, seed: int | None) -> None:
    put(item, 'ChangedCatalystSeed', UNSET_SEED if seed is None else int(seed))


def catalyst_grants(catalyst: str) -> list[tuple[str, float]]:
    """[(attribute, position)] a catalyst adds to the item it is installed on, as the game
    first makes them: the three mining attributes, an Okkar catalyst's buff and debuff, the
    Sovereign catalyst's display of the levels it gives, and the Evolution catalyst's damage bonus and kill count, which start at nothing. The others
    are single values or level-scaled, where the game rolls a position that changes little."""
    if catalyst == MINING:
        return [(a, 1.0) for a in MINING_ATTRIBUTES]
    if catalyst == EVOLUTION:
        return [(a, 0.0) for a in EVOLUTION_ATTRIBUTES]
    if catalyst == SOVEREIGN:
        return [(SOVEREIGN_ATTRIBUTE, 1.0)]
    if catalyst.startswith(OKKAR):
        name = 'okkar_catalyst_' + catalyst[len(OKKAR) :]
        return [(name + '_buff', 1.0), (name + '_debuff', 1.0)]
    return []


def repeatable(catalyst: str) -> bool:
    """Whether a catalyst can be installed more than once: those whose copies each multiply
    the item's attributes again (see REPEATABLE), though the game never installs one twice
    itself."""
    return catalyst in REPEATABLE


def add_catalyst(item: Item, catalyst: str, order: Sequence[str] = ()) -> None:
    """Installs a catalyst, with the attributes it adds to the item (at their place in the
    game's `order`) and the rest of what the game writes for it: Chaos needs a seed, so one
    is rolled if there is none; Evolution starts its kill count; Sovereign raises the
    item's level. One that is installed already is only installed again if it is
    repeatable."""
    entries = get(item, 'Catalysts')
    if entries is None:
        raise KeyError('this save predates catalysts')
    if catalyst in entries and not repeatable(catalyst):
        return
    entries.append(catalyst)
    for name, pos in catalyst_grants(catalyst):
        if position(item, name) is None and 'AttributeStates' in item:
            add_attribute(item, name, pos, order)
    if catalyst == EVOLUTION and custom(item, EVOLUTION_KILLS) is None:
        _put_custom(item, EVOLUTION_KILLS, 'int32', 0)
    if catalyst == SOVEREIGN and 'VirtualLevelOffset' in item:
        # the most it gives; the game lowers it for the set bonuses the ship has
        put(item, 'VirtualLevelOffset', get(item, 'VirtualLevelOffset') + SOVEREIGN_LEVELS)
    if catalyst == FLAWLESS:
        # as the game does: the legendary attributes go to the top of their ranges, and where
        # they were is kept
        legendary = [(n, p) for n, p in attributes(item) if n in FLAWLESS_ATTRIBUTES]
        if ORIGINAL_POSITIONS in item and not get(item, ORIGINAL_POSITIONS):
            put(item, ORIGINAL_POSITIONS, [[n, p] for n, p in legendary])
        for name, _pos in legendary:
            set_position(item, name, 1.0)
    if catalyst == CHAOS and catalyst_seed(item) is None and 'ChangedCatalystSeed' in item:
        set_catalyst_seed(item, random.randrange(1, UNSET_SEED))


def evolution_kills(item: Item) -> int | None:
    """The kills an Evolution catalyst has counted, or None when the item has none."""
    if EVOLUTION not in get(item, 'Catalysts', []):
        return None
    return custom(item, EVOLUTION_KILLS) or 0


def evolution_earned(item: Item) -> str | None:
    """The bonus attribute the catalyst's thousandth kill added, or None."""
    name = custom(item, EVOLUTION)
    return name if name and name != EMPTY else None


def set_evolution_kills(item: Item, kills: float, earned: str | None = None, order: Sequence[str] = ()) -> None:
    """Sets an Evolution catalyst's kill count and everything the game keeps in step with it:
    the count as the item shows it, the damage bonus the count has reached, and the bonus
    attribute a thousand kills earn. `earned` names the attribute to add when the count
    reaches that and the item has none recorded; below it, the recorded one is taken out."""
    if EVOLUTION not in get(item, 'Catalysts', []):
        raise KeyError('item has no Evolution catalyst')
    kills = max(0, int(kills))
    _put_custom(item, EVOLUTION_KILLS, 'int32', kills)
    bonus = next((b for least, b in EVOLUTION_STEPS if kills >= least), 0.0)
    for name, pos in ((EVOLUTION_BONUS, bonus), (EVOLUTION_KILL_COUNT, kills / float(EVOLUTION_EARNS))):
        if position(item, name) is None:
            add_attribute(item, name, pos, order)
        else:
            set_position(item, name, pos)
    have = evolution_earned(item)
    if kills < EVOLUTION_EARNS and have:
        remove_attribute(item, have)
    elif kills >= EVOLUTION_EARNS and not have and earned and position(item, earned) is None:
        add_attribute(item, earned, 1.0, order)
        _put_custom(item, EVOLUTION, 'name', earned)


def remove_catalyst(item: Item, catalyst: str) -> None:
    entries = get(item, 'Catalysts')
    if entries is None or catalyst not in entries:
        return
    if catalyst == SOVEREIGN and 'VirtualLevelOffset' in item:
        put(item, 'VirtualLevelOffset', get(item, 'VirtualLevelOffset') - sovereign_bonus(item))
    entries.remove(catalyst)
    for name, _pos in catalyst_grants(catalyst):
        remove_attribute(item, name)
    if catalyst == EVOLUTION:
        # the bonus attribute it earned after a thousand kills goes with it
        earned = evolution_earned(item)
        if earned:
            remove_attribute(item, earned)
        _remove_custom(item, EVOLUTION)
        _remove_custom(item, EVOLUTION_KILLS)
    if catalyst == FLAWLESS and get(item, ORIGINAL_POSITIONS):
        for name, pos in get(item, ORIGINAL_POSITIONS):
            if position(item, name) is not None:
                set_position(item, name, pos)
        put(item, ORIGINAL_POSITIONS, [])
    if catalyst == CHAOS:
        if 'ChangedCatalystSeed' in item:
            set_catalyst_seed(item, None)
        if get(item, 'AttributeOriginalValue'):
            put(item, 'AttributeOriginalValue', [])


# ---------------------------------------------------------------- whole items

_RESET = {  # field -> value in a blank slot
    'ItemTemplateID': EMPTY,
    'Seed': 0,
    'NameSeed': 0,
    'ItemLevel': 1,
    'VirtualLevelOffset': 0.0,
    'Amount': 0,
    'Ammo': 0,
    'RecentlyBoughtOrSold': False,
    'IsMarked': False,
    'IsNew': False,
    'IsFavorite': False,
    'WasModified': False,
    'CanBeUsedWithAnyLevel': False,
    'Condition': 1.0,
    'CooldownRemaining': 0.0,
    'CooldownLastSet': 0.0,
    'TimestampInventoryAdd': -1.0,
    'AffixID': EMPTY,
    'ChangedCatalystSeed': UNSET_SEED,
    'TimestampOverheated': -1.0,
}
_LISTS = (
    'InstalledChipIDs',
    'Catalysts',
    'AttributeStates',
    'AttributeOriginalPositionInRange',
    'AttributeOriginalValue',
    'CustomData',
    'CraftingActionCounts',
)


def blank(item: Item) -> Item:
    """Turns an item into an empty slot, keeping the set of fields the save uses."""
    for key, value in _RESET.items():
        if key in item:
            item[key]['value'] = value
    for key in _LISTS:
        if key in item:
            item[key]['value'] = []
    for key, enum in (('Rarity', 'EItemRarity'), ('Grade', 'EItemGrade')):
        if key in item:
            _put_enum(item, key, 'None', enum)
    return item


def duplicate(item: Item) -> Item:
    """A copy with its own seed, as the game never has two items sharing one."""
    new = copy.deepcopy(item)
    if 'Seed' in new:
        put(new, 'Seed', random.randrange(1, 8_000_000))
    if 'IsNew' in new:
        put(new, 'IsNew', True)
    return new


def create(
    like: Item,
    template_id: str,
    game: GameData,
    rarity_name: str | None = None,
    level: int = 1,
    bonus: Sequence[str] | None = None,
    position_value: float = 0.5,
    amount: int = 1,
) -> Item:
    """A new item of a template, built on a copy of `like` so that it has the fields this save
    uses. It gets every fixed attribute of the template plus `bonus` level-scaled stats
    (default: the first few the template allows, as many as the rarity carries)."""
    info = game.item(template_id)
    if not info:
        raise ValueError('unknown item template %r' % template_id)
    allowed = game.rarities(template_id)
    if rarity_name is None:
        rarity_name = allowed[0]
    item = blank(copy.deepcopy(like))
    put(item, 'ItemTemplateID', template_id)
    for key in ('Seed', 'NameSeed'):
        if key in item:
            put(item, key, random.randrange(1, 8_000_000))
    for key, value in (
        ('ItemLevel', int(level)),
        ('Amount', int(amount)),
        ('IsNew', True),
        ('TimestampInventoryAdd', 0.0),
    ):
        if key in item:
            put(item, key, value)
    if bonus is None:
        bonus = game.bonus_attributes(template_id)[: BONUS_STATS.get(rarity_name, 0)]
    names = game.fixed_attributes(template_id) + [b for b in bonus]
    put(item, 'AttributeStates', [_state(n, position_value) for n in names])
    sort_attributes(item, list(game.ranges(template_id)))
    set_rarity(item, rarity_name)
    full = game.ranges(template_id).get(MAX_AMMO)
    if full and 'Ammo' in item:
        put(item, 'Ammo', int(full[2]))  # a new secondary weapon has full ammo, as in the game
    return item


# ---------------------------------------------------------------- inventories


def _fit(lst: list[Item], capacity: int) -> None:
    """Grows a list of slots to a capacity with empty ones at the end, or shrinks it by taking
    away empty slots, last ones first. A slot with something in it is never removed."""
    while len(lst) < capacity:
        lst.append(blank(copy.deepcopy(lst[0])))
    for index in range(len(lst) - 1, -1, -1):
        if len(lst) <= capacity:
            break
        if is_empty(lst[index]):
            del lst[index]


def slot_count(game: GameData, item: Item, attribute: str) -> int | None:
    """A number of slots an item has or gives (cargo_slots, device_slots, cargo_slots_buff),
    scaled for its level and rounded as the game stores whole-number attributes. 0 when the
    item does not have the attribute, None when the game data cannot tell."""
    pos = position(item, attribute)
    if pos is None:
        return 0
    base = game.range_value(template(item), attribute, pos)
    meta = game.attributes.get(attribute)
    if base is None or not meta or 'growth_exp' not in meta:
        return None
    level = (get(item, 'ItemLevel', 1) or 1) + (get(item, 'VirtualLevelOffset', 0.0) or 0.0)
    return int(base * level_scale(meta, level) + 0.5)


class Inventory:
    """One InventoryState in the save: station storage, a ship, or a resource store.

    slots  {array name: list of items}; the lists are the save's own, so changes land in it"""

    def __init__(
        self, key: str, label: str, kind: str, state: Properties, path: str, ship: Properties | None = None
    ) -> None:
        self.key = key
        self.label = label
        self.kind = kind  # 'station', 'ship', 'resources' or 'quest'
        self.path = path  # dotted path of the InventoryState in the save tree
        self.ship = ship  # the ShipDataState property list, for ships
        self.slots = {name: prop['value'] for name, prop in state.items() if isinstance(prop['value'], list)}

    @property
    def cargo(self) -> list[Item]:
        return self.slots.get(CARGO, [])

    def equipment(self) -> list[tuple[str, str, list[Item]]]:
        """[(array name, label, items)] for the equipment slots this inventory has, in the
        game's order. Station and resource stores carry unused one-slot arrays; skipped."""
        if self.kind != 'ship':
            return []
        return [(name, SLOT_NAMES[name], self.slots[name]) for name in SLOT_NAMES if self.slots.get(name)]

    def items(self) -> list[tuple[str, int, Item]]:
        """[(array name, index, item)] for every filled slot."""
        return [(name, i, it) for name, lst in self.slots.items() for i, it in enumerate(lst) if not is_empty(it)]

    def free_cargo(self) -> int:
        return sum(1 for it in self.cargo if is_empty(it))

    @property
    def limited(self) -> bool:
        """Whether the cargo has a capacity: a ship's hold and the station's storage are as
        large as the game made them, while resources and quest items take what comes."""
        return self.kind in ('ship', 'station')

    def is_full(self) -> bool:
        return self.limited and not self.free_cargo()

    def add(self, item: Item, array: str = CARGO) -> int:
        """Puts an item into the first empty slot of an array and returns the slot index.
        Without one it fails, except that the lists with no capacity grow."""
        lst = self.slots[array]
        for i, existing in enumerate(lst):
            if is_empty(existing):
                lst[i] = item
                return i
        if array != CARGO:
            raise ValueError('no free %s slot' % SLOT_NAMES.get(array, array))
        if self.limited:
            raise ValueError('%s has no free cargo slot' % self.label)
        lst.append(item)
        return len(lst) - 1

    @property
    def ship_item(self) -> Item | None:
        """The ship's own item, which holds its level and passives; None for anything but a ship."""
        if self.kind != 'ship' or not self.ship or 'ShipItemState' not in self.ship:
            return None
        return self.ship['ShipItemState']['value']

    def cargo_capacity(self, game: GameData) -> int | None:
        """How many cargo slots the game gives this ship: the ship's own Cargo Slots plus the
        Cargo Slots bonus of everything equipped (only cargo units have one), each scaled for
        its level and rounded as the game stores whole-number attributes
        (UInventoryLib::GetTotalCargoSpaceOfShip). None for anything but a ship, or when the
        game data cannot tell."""
        if self.ship_item is None:
            return None
        parts = [slot_count(game, self.ship_item, 'cargo_slots')]
        parts += [
            slot_count(game, it, 'cargo_slots_buff')
            for name in SLOT_NAMES
            for it in self.slots.get(name, [])
            if not is_empty(it)
        ]
        if None in parts or not parts[0]:
            return None
        return sum(p for p in parts if p is not None)

    def device_capacity(self, game: GameData) -> int | None:
        """How many device slots the game gives this ship: its Device Slots, which grow with
        its level, and never more than four. None for anything but a ship, or when the game
        data cannot tell."""
        if self.ship_item is None:
            return None
        count = slot_count(game, self.ship_item, 'device_slots')
        return min(count, MAX_DEVICE_SLOTS) if count else None

    def fit_devices(self, game: GameData) -> list[Item]:
        """Gives the ship the number of device slots the game would. Slots are added at the
        end; to take some away, empty ones go first, last ones first, and then the devices in
        the last slots are moved into the hold. A device that does not fit there stays where
        it is. Returns the devices that were moved."""
        capacity = self.device_capacity(game)
        lst = self.slots.get(DEVICES)
        if capacity is None or not lst:
            return []
        _fit(lst, capacity)
        moved = []
        while len(lst) > capacity and self.free_cargo():
            item = lst.pop()
            self.add(item)
            moved.append(item)
        return moved

    def fit_cargo(self, game: GameData) -> bool:
        """Brings the hold to the size the game gives the ship. Empty slots are added at the
        end or taken away, last ones first; a slot with something in it is never removed, so
        an overfull hold stays longer than it should be. Returns whether anything changed."""
        capacity = self.cargo_capacity(game)
        lst = self.cargo
        if capacity is None or not lst or len(lst) == capacity:
            return False
        before = len(lst)
        _fit(lst, capacity)
        return len(lst) != before

    def remove(self, array: str, index: int) -> Item:
        """Empties a slot and returns the item that was in it."""
        lst = self.slots[array]
        item = lst[index]
        lst[index] = blank(copy.deepcopy(item))
        return item


def inventories(save: Save) -> list[Inventory]:
    """The inventories of a main save, in the order the game's menus show them."""
    props = save['properties']
    out = []
    current = get(props, 'CurrentShip')
    for i, ship in enumerate(get(props, 'ShipsSaveState', [])):
        ship_template = get(get(ship, 'ShipItemState', {}), 'ItemTemplateID', EMPTY)
        if 'Inventory' not in ship or ship_template == EMPTY:
            continue  # an unused ship slot
        name = text_source(get(ship, 'Name')) or ship_template
        label = name + (' (current)' if i == current else '')
        out.append(
            Inventory('ship%d' % i, label, 'ship', ship['Inventory']['value'], 'ShipsSaveState[%d].Inventory' % i, ship)
        )
    out.sort(key=lambda inv: 0 if inv.label.endswith('(current)') else 1)
    for key, label, kind in (
        ('StationCargoSaveState', 'Storage', 'station'),
        ('CraftingResourcesSaveState', 'Resources', 'resources'),
        ('QuestItemsSaveState', 'Quest items', 'quest'),
    ):
        if key in props:
            out.append(Inventory(key, label, kind, props[key]['value'], key))
    return out


def exemplar(save: Save) -> Item:
    """Any item from the save, to copy the field layout from when creating one."""
    for inv in inventories(save):
        for lst in inv.slots.values():
            if lst:
                return lst[0]
    raise ValueError('the save has no item slots at all')
