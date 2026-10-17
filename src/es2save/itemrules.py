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

"""What the game allows of an item: the catalysts that fit it, the prefix they give it, the
stats it can have, and the items that belong in each kind of slot.

These are the game's rules as its own code and the saves it writes show them, for anything
that changes items to stay within; items.py makes the changes themselves.
"""

from collections.abc import Iterable

from . import items
from .gamedata import GameData
from .items import Item


def catalyst_affix(game: GameData, item: Item) -> str | None:
    """The prefix the item's catalysts give it, or None. A prefix catalyst (Catalyst: Rapid)
    sets the prefix, and the game sets it again from the catalysts when it loads the item:
    the last of them is the one in force, and whatever prefix the save names is dropped.
    The others do nothing, so the editor installs one at a time (see install_catalyst)."""
    given = [game.catalysts.get(c, {}).get('adds_affix') for c in items.catalysts(item)]
    given = [a for a in given if a]
    return given[-1] if given else None


def effective_affix(game: GameData, item: Item) -> str:
    return catalyst_affix(game, item) or items.affix(item)


def sync_affix(game: GameData, item: Item) -> None:
    """Writes the prefix the catalysts give, so the save says what the game will make of it."""
    given = catalyst_affix(game, item)
    if given and given != items.affix(item):
        items.set_affix(item, given)


def install_catalyst(game: GameData, item: Item, catalyst: str) -> None:
    """Installs a catalyst. An item has one prefix, so a prefix catalyst takes the place of
    the one that is installed; the other catalysts stack."""
    if game.catalysts.get(catalyst, {}).get('adds_affix'):
        for other in items.catalysts(item):
            if other != catalyst and game.catalysts.get(other, {}).get('adds_affix'):
                items.remove_catalyst(item, other)
    items.add_catalyst(item, catalyst, list(game.attributes))
    sync_affix(game, item)


def catalyst_fits_rarity(game: GameData, catalyst: str, rarity: str) -> bool:
    """Whether the game installs a catalyst on an item of a rarity. A catalyst is an item with
    a rarity of its own, and the game's list of what fits (UItemLib::GetCompatibleCatalystsForItem)
    goes by it: legendary catalysts are for legendary items, which take no others but
    Hexaspatial, and ascendant catalysts (the Okkar ones) are for ascendant items, which take
    the common to superior ones as well."""
    own = game.item(catalyst).get('max_rarity')
    if own == 'Legendary':
        return rarity == 'Legendary'
    if rarity == 'Legendary':
        return catalyst == items.HEXASPATIAL
    if own == 'Ascendant':
        return rarity == 'Ascendant'
    return True


def installable_catalysts(game: GameData, item: Item) -> list[str]:
    """The catalysts to offer for an item: those for its kind and its rarity that it does not
    have."""
    have, rarity = items.catalysts(item), items.rarity(item)
    return [
        c
        for c in game.catalysts_for(items.template(item))
        if c not in have
        and catalyst_fits_rarity(game, c, rarity)
        and flawless_fits(item, c)
        and evolution_fits(game, item, c)
    ]


def flawless_fits(item: Item, catalyst: str = items.FLAWLESS) -> bool:
    """The Flawless catalyst is for Radiant items that have legendary attributes to raise: the
    game's list names it and asks for both. No other catalyst goes by the grade this way
    (Chaos is offered for any legendary)."""
    if catalyst != items.FLAWLESS:
        return True
    return items.grade(item) == 'Radiant' and any(n in items.FLAWLESS_ATTRIBUTES for n, _ in items.attributes(item))


DAMAGE = ('weapon_hull_damage', 'weapon_shield_damage')


def evolution_fits(game: GameData, item: Item, catalyst: str = items.EVOLUTION) -> bool:
    """The Evolution catalyst counts kills, and the game leaves it out for a weapon whose
    kinetic and energy DPS are both nothing: the missiles that only hinder (Destabilizer,
    Web and Exorcist), which have neither damage attribute."""
    if catalyst != items.EVOLUTION:
        return True
    ranges = game.ranges(items.template(item))
    return any(isinstance(ranges.get(a, [0, '', 0])[2], (int, float)) and ranges.get(a, [0, '', 0])[2] for a in DAMAGE)


def is_stackable(game: GameData, item: Item) -> bool:
    """Stackable things carry an amount; equipment is always a single piece."""
    tpl = items.template(item)
    if game:
        return not game.is_equipment(tpl) and game.category(tpl) != 'cat_device'
    return items.get(item, 'Amount', 1) > 1


def set_evolution_kills(game: GameData, item: Item, kills: float) -> None:
    """Sets an Evolution catalyst's kills. Reaching a thousand earns a bonus attribute, which
    the game rolls; here it is the first the item's template offers that it does not have."""
    earned = next(iter(addable_stats(game, item)), None)
    items.set_evolution_kills(item, kills, earned, list(game.attributes))


def swap_choices(game: GameData, item: Item, current: str) -> list[str]:
    """Bonus stats that `current` could be changed to: the template's pool minus those in use.
    A reliquary's blessing and its radiant blessing each stay among their own kind."""
    used = {n for n, _ in items.attributes(item)}
    radiant = items.is_radiant_blessing(current)
    return [
        a
        for a in game.bonus_attributes(items.template(item))
        if items.is_radiant_blessing(a) == radiant and (a == current or a not in used)
    ]


def radiant_blessings(game: GameData, template: str) -> list[str]:
    """The radiant blessings a template can carry: only reliquaries have any."""
    return [a for a in game.bonus_attributes(template) if items.is_radiant_blessing(a)]


def addable_stats(game: GameData, item: Item) -> list[str]:
    used = {n for n, _ in items.attributes(item)}
    return [a for a in game.bonus_attributes(items.template(item)) if a not in used]


# What each section holds in saves written by the game, as (main category, sub-categories or
# None for all of them). An equipment slot takes its own category; of the devices, the ones
# built into a ship class are never in a slot.
CARGO_HOLDS: dict[str, tuple[str, ...] | None] = {
    'cat_weapon': None,
    'cat_launcher': None,
    'cat_energy_core': None,
    'cat_shield': None,
    'cat_plating': None,
    'cat_sensor': None,
    'cat_thrusters': None,
    'cat_cargo_unit': None,
    'cat_consumable': None,
    'cat_item': ('cat_commodity', 'cat_coordinates', 'cat_reliquary', 'cat_software'),
}


SECTION_HOLDS: dict[str, dict[str, tuple[str, ...] | None]] = {
    'ship': CARGO_HOLDS,
    'station': CARGO_HOLDS,
    'resources': {
        'cat_item': (
            'cat_resource_ore',
            'cat_resource_crystal',
            'cat_resource_tec',
            'cat_resource_misc',
            'cat_crafting_resource',
            'cat_crafting_catalyst',
        )
    },
    'quest': {'cat_item': ('cat_mission',)},
}


SLOT_HOLDS: dict[str, dict[str, tuple[str, ...] | None]] = {
    'Devices': {'cat_device': ('cat_device_support', 'cat_device_warfare')}
}


def addable_templates(game: GameData, kind: str, array: str, held: Iterable[Item] = ()) -> list[str]:
    """The item templates that belong in an array of an inventory of a kind ('ship', 'station',
    'resources' or 'quest'), sorted by type and name. Resources and quest items are one stack
    per kind, so those among the items already `held` are left out: their amount is raised
    instead."""
    if array == items.CARGO:
        holds = SECTION_HOLDS.get(kind, CARGO_HOLDS)
    else:
        holds = SLOT_HOLDS.get(array) or {items.SLOT_CATEGORY.get(array, ''): None}
    taken = {items.template(i) for i in held} if kind in ('resources', 'quest') and array == items.CARGO else ()
    out = []
    for t, d in game.items.items():
        subs = holds.get(d['main_category'], ())  # None for all of them
        if t not in taken and (subs is None or d['sub_category'] in subs):
            out.append(t)
    return sorted(out, key=lambda t: (game.category_name(t), game.name(t)))
