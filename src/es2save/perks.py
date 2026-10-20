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

"""The three kinds of perk in a main save (see "Perks" in FORMAT.md).

Player perks are a list of chosen IDs, one from each set the player's level has reached.
Companion perks are records that carry their own levels with what each costs and how much
of that has been handed in. A ship's passives are attributes of the ship's own item.

Everything here changes the save tree in place, like items.py.
"""

import math
import re

from . import items
from .gamedata import GameData
from .items import Item
from .savegame import Properties

PLAYER_PERKS = 'SelectedPerks'
COMPANION_PERKS = 'CompanionPerks'
LEVELS_PER_SET = 5  # a set of player perks opens every five player levels
CLASS_PASSIVE = '_passive_perk'  # suffix of the passive every ship of a class has


# ---------------------------------------------------------------- player perks


def player_sets(game: GameData) -> list[list[str]]:
    """[[perk ID, ...], ...]: the choices of each set, in the game's order."""
    sets: dict[str, list[str]] = {}
    for template, info in game.items.items():
        if info.get('main_category') == 'cat_perk':
            sets.setdefault(info.get('sub_category', ''), []).append(template)
    return [sets[key] for key in sorted(sets, key=_trailing_number)]


def _trailing_number(text: str) -> int:
    m = re.search(r'(\d+)$', text)
    return int(m.group(1)) if m else 0


def unlocked_sets(props: Properties) -> int:
    """How many sets the player's level has opened."""
    return (items.get(props, 'PlayerLevel', 0) or 0) // LEVELS_PER_SET


def selected(props: Properties) -> list[str]:
    return list(items.get(props, PLAYER_PERKS, []))


def select(props: Properties, game: GameData, perk: str) -> None:
    """Chooses a perk in place of the one chosen from the same set, keeping the list in the
    order of the sets as the game writes it."""
    sets = player_sets(game)
    index = next((n for n, choices in enumerate(sets) if perk in choices), None)
    if index is None:
        raise ValueError('%r is not a player perk' % perk)
    if PLAYER_PERKS not in props:
        raise KeyError('this save predates player perks')
    place = {p: n for n, choices in enumerate(sets) for p in choices}
    chosen = [p for p in selected(props) if place.get(p) != index] + [perk]
    chosen.sort(key=lambda p: place.get(p, len(sets)))
    items.put(props, PLAYER_PERKS, chosen)


# ---------------------------------------------------------------- companion perks


class CompanionPerk:
    """One entry of CompanionPerks: a perk and the levels it can be raised through."""

    def __init__(self, key: str, entry: Properties) -> None:
        self.id = key
        self.entry = entry

    @property
    def levels(self) -> list[Properties]:
        return items.get(self.entry, 'PerkLevels', [])

    @property
    def level_ids(self) -> list[str]:
        return [items.get(level, 'PerkLevelID') for level in self.levels]

    @property
    def level(self) -> int:
        """How many of the levels have been paid for in full."""
        return items.get(self.entry, 'CurrentLevel', 0)

    def progress(self) -> list[tuple[str, int, int]]:
        """[(resource, handed in, needed)] for the level being worked towards; [] at the top."""
        if self.level >= len(self.levels):
            return []
        level = self.levels[self.level]
        return list(
            zip(
                items.get(level, 'LevelRequirementIDs', []),
                items.get(level, 'AmountsInvested', []),
                items.get(level, 'LevelRequirementAmounts', []),
            )
        )

    def set_level(self, level: int) -> None:
        """Sets how many levels are complete. Those are marked paid in full and the rest as
        untouched, apart from what was already handed in towards the level that is next."""
        level = max(0, min(len(self.levels), int(level)))
        was = self.level
        for n, entry in enumerate(self.levels):
            needed = items.get(entry, 'LevelRequirementAmounts', [])
            if n < level:
                items.put(entry, 'AmountsInvested', list(needed))
            elif n > level or level != was:
                items.put(entry, 'AmountsInvested', [0] * len(needed))
        items.put(self.entry, 'CurrentLevel', level)


def companion_perks(props: Properties) -> list[CompanionPerk]:
    """The companion perks the save holds, in its order. Perks the game has not offered yet
    (their companion is not on the station) are not in the save."""
    return [CompanionPerk(key, entry) for key, entry in items.get(props, COMPANION_PERKS, [])]


def companion(game: GameData, perk_id: str) -> str:
    """'Engineer': whose perk it is, from its sub-category (cat_perk_engineer)."""
    sub = game.sub_category(perk_id)
    name = sub[len('cat_perk_') :] if sub.startswith('cat_perk_') else ''
    return {'homebase': 'Home base', 'elek': 'Elek', 'hive': 'HIVE'}.get(name, name.capitalize() or 'Other')


def by_companion(game: GameData, props: Properties) -> list[tuple[str, list[CompanionPerk]]]:
    """[(companion, [CompanionPerk])]: the save's perks gathered under whose they are, the
    companions and each one's perks in the order of the game's data. The save itself lists
    them in the order they were first offered, which mixes the companions."""
    order = {t: n for n, t in enumerate(game.items)}
    groups: dict[str, list[CompanionPerk]] = {}
    for perk in sorted(companion_perks(props), key=lambda p: order.get(p.id, len(order))):
        groups.setdefault(companion(game, perk.id), []).append(perk)
    return list(groups.items())


# ---------------------------------------------------------------- ship passives


def class_passive(ship_item: Item) -> str | None:
    """The passive every ship of the class has (scout_passive_perk), or None."""
    return next((n for n, _ in items.attributes(ship_item) if n.endswith(CLASS_PASSIVE)), None)


def passive_pool(game: GameData, ship_item: Item) -> list[str]:
    """The passives a ship of this class can roll."""
    return game.bonus_attributes(items.template(ship_item))


def passives(game: GameData, ship_item: Item) -> list[str]:
    """The rolled passives the ship has, in stored order."""
    pool = set(passive_pool(game, ship_item))
    return [n for n, _ in items.attributes(ship_item) if n in pool]


def passive_count(ship_item: Item) -> int:
    """How many passives the game gives a ship of this tier (stored as its ItemLevel): one
    more every second tier, as on every ship in the saves but the Okkar one, which has all
    five of its own."""
    return math.ceil((items.get(ship_item, 'ItemLevel', 1) or 1) / 2)
