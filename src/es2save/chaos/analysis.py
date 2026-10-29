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

"""How the game applies the Chaos catalyst to an item.

With catalyst_legendary_chaos installed, the game shows each attribute as

    original value * (0.8 + 0.4 * f_n)

where f_n is the n-th fraction of Unreal's FRandomStream seeded with the item's
ChangedCatalystSeed and n is the attribute's place in the game's attribute order (see
FORMAT.md). factors() gives those multipliers for a seed, and analyse() works out which
attributes of an item the game keeps, in which order, and which way each is better.
"""

import re
import struct
from collections.abc import Collection, Iterable
from typing import Any

from .. import gamedata, items
from ..items import Item, get

LCG_MUL = 196314165
LCG_ADD = 907633515
FACTOR_MIN = 0.8
FACTOR_SPAN = 0.4
UNSET_SEEDS = (0, 2147483647)  # ChangedCatalystSeed on items without Chaos


NOT_STATS = {
    'blueprintable',
    'no_random_drop',
    'content_group',
    'min_level',
    'system_drop',
    'item_price',
    'item_price_okkar',
    'item_stack_amount',
    'ascendant_rank',
    'item_mission_id',
}


LOWER_IS_BETTER = re.compile(
    r'(recharge_delay|shutdown_duration|energy_consumption|_spread|cooldown|'
    r'charge_duration|ramp_up_duration|burst_delay|lock_duration)(_buff)?$'
)


Kind = tuple[int, str, int]  # (factor index, attribute, direction), as Analysis.kinds() gives them


class UsageError(Exception):
    pass


def fractions(seed: int, count: int) -> list[float]:
    """First `count` values of FRandomStream(seed).GetFraction()."""
    out = []
    s = seed & 0xFFFFFFFF
    for _ in range(count):
        s = (s * LCG_MUL + LCG_ADD) & 0xFFFFFFFF
        out.append(struct.unpack('<f', struct.pack('<I', 0x3F800000 | (s >> 9)))[0] - 1.0)
    return out


def factors(seed: int, count: int) -> list[float]:
    """The Chaos multiplier for each of the first `count` attribute positions."""
    return [FACTOR_MIN + FACTOR_SPAN * f for f in fractions(seed, count)]


def to_int32(seed: int) -> int:
    seed &= 0xFFFFFFFF
    return seed - (1 << 32) if seed >= 1 << 31 else seed


ALWAYS_ALLOWED = {'ascendant_rank'}  # added by the game outside the template's own list


# attributes a catalyst adds to the item it is installed on, where the game data does not say
CATALYST_GRANTS = {
    'catalyst_mining': {
        'weapon_mining_quality',
        'weapon_mining_refund',
        'weapon_mining_quantity',
        'weapon_mining_bonus',
    },
    'catalyst_evolution': {'evolution_catalyst_buff', 'evolution_catalyst_kill_count'},
    'catalyst_legendary_sovereign': {'sovereign_legendary_catalyst_buff_display'},
}


MINING_RANGES_FROM = 'beam_laser'
OPTIMIZED = 'optimized'
OPTIMIZED_CATALYST = 'catalyst_affix_optimized'


def maximised(item: Item, name: str, ranges: Collection[str]) -> bool:
    """Whether the game reads an attribute at the top of its range whatever position is
    stored: the Optimized prefix, or its catalyst under any prefix, does that to everything
    the item's template lists (`ranges`), and the Flawless catalyst to the legendary
    attributes."""
    catalysts = get(item, 'Catalysts', [])
    if name in ranges and (get(item, 'AffixID') == OPTIMIZED or OPTIMIZED_CATALYST in catalysts):
        return True
    return items.FLAWLESS in catalysts and name in items.FLAWLESS_ATTRIBUTES


def _borrowed_range(item: Item, name: str, game_items: dict[str, Any]) -> list[Any] | None:
    """The range of an attribute a catalyst added that the item's own template does not have:
    the game makes the Mining catalyst's attributes with the ranges of the beam laser, and an
    Okkar catalyst's definition carries the values of its two."""
    if items.MINING in get(item, 'Catalysts', []) and name in items.MINING_ATTRIBUTES:
        return game_items.get(MINING_RANGES_FROM, {}).get('attributes', {}).get(name)
    if name not in NOT_STATS:
        for catalyst in get(item, 'Catalysts', []):  # an Okkar catalyst's buff and debuff
            r = game_items.get(catalyst, {}).get('attributes', {}).get(name)
            if r:
                return r
    return None


def catalyst_attributes(item: Item, game_items: dict[str, Any]) -> set[str]:
    """Attributes the item's installed catalysts may have added to it: those the catalyst's
    own definition carries (the Okkar catalysts' buff and debuff) and the ones in
    CATALYST_GRANTS."""
    granted = set()
    for catalyst in get(item, 'Catalysts', []):
        granted |= CATALYST_GRANTS.get(catalyst, set())
        granted |= set(game_items.get(catalyst, {}).get('attributes', {})) - NOT_STATS
    return granted


class Attribute:
    """One attribute of an item as the game will treat it.

    index      position in the game's attribute order; selects the Chaos factor
    stored     position in the save's AttributeStates list
    position   ValuePositionInRange, or 1.0 when the item's prefix or a catalyst puts it there
    maximised  the prefix or a catalyst holds it at the top of its range
    direction  +1 larger is better, -1 smaller is better, 0 not scored
    base       the value before Chaos, derived from the position (None if not derivable)
    span       the values before Chaos at the two ends of the range (None if not derivable)"""

    def __init__(self, name: str, stored: int, position: float) -> None:
        self.name = name
        self.stored = stored
        self.position = position
        self.index = stored
        self.maximised = False
        self.direction = 1
        self.base: float | None = None
        self.span: tuple[float, float] | None = None


class Analysis:
    """An item's attributes in the order the game applies Chaos factors to them.

    attributes  [Attribute] the game keeps, in its own order
    rejected    [Attribute] the item's template does not allow; the game replaces these
    reordered   the stored order of the kept attributes differs from the game's
    validated   the template was found in the game data, so the above could be checked
    level_scale multiplier estimated for level-scaled stats when there is no growth metadata
    level       the item's effective level, which the growth metadata is applied for
    mismatches  [(name, recorded original, value from position)] where they disagree"""

    def __init__(self) -> None:
        self.attributes: list[Attribute] = []
        self.rejected: list[Attribute] = []
        self.reordered = False
        self.validated = False
        self.level_scale: float | None = None
        self.level: float | None = None  # effective level: ItemLevel + VirtualLevelOffset
        self.mismatches: list[tuple[str, float, float]] = []

    def kinds(self) -> list[Kind]:
        """[(factor index, name, direction)] as taken by search()."""
        return [(a.index, a.name, a.direction) for a in self.attributes]


def analyse(
    item: Item,
    game_items: dict[str, Any],
    lower: Collection[str] = (),
    higher: Collection[str] = (),
    ignore: Collection[str] = (),
    only: Collection[str] = (),
    attribute_order: Iterable[str] = (),
) -> Analysis:
    """Works out which attributes the game keeps, in which order, and how to score them.

    The Chaos factor of an attribute depends on its place in the game's attribute order (the
    order of attributes.CSV: `attribute_order` is that list, or the attributes table itself,
    which also supplies level growth; the template's own list is used
    when it is not given), not on its place in the save. Attributes that neither the item's
    template nor one of its catalysts provides are dropped by the game and replaced with one
    it picks itself, which moves every later attribute up one place."""
    out = Analysis()
    ranges = game_items.get(get(item, 'ItemTemplateID'), {}).get('attributes')
    out.validated = bool(ranges)
    ranges = ranges or {}
    order = {name: i for i, name in enumerate(ranges)}
    original = dict(get(item, 'AttributeOriginalValue', []))

    stored = [
        Attribute(state['AttributeTemplateID']['value'], i, state['ValuePositionInRange']['value'])
        for i, state in enumerate(get(item, 'AttributeStates', []))
    ]
    kept = stored
    if out.validated:
        granted = catalyst_attributes(item, game_items)
        out.rejected = [
            a for a in stored if a.name not in order and a.name not in ALWAYS_ALLOWED and a.name not in granted
        ]
        kept = [a for a in stored if a not in out.rejected]
        rank = {name: i for i, name in enumerate(attribute_order)} or order  # a list or a dict
        in_game_order = sorted(kept, key=lambda a: (rank.get(a.name, len(rank)), a.stored))
        # catalysts append what they add in their own order, so those do not count as misplaced
        own = [a.name for a in kept if a.name not in granted]
        out.reordered = own != [a.name for a in in_game_order if a.name not in granted]
        if out.reordered:
            kept = in_game_order
    for index, a in enumerate(kept):
        a.index = index
        if maximised(item, a.name, ranges):
            a.maximised, a.position = True, 1.0
    out.attributes = kept

    # Values before Chaos come from the positions, scaled for the item's level by the growth
    # each attribute has in attributes.CSV. Without that metadata the multiplier of the
    # level-scaled stats ('-' ranges) is estimated from the recorded original values instead.
    meta = attribute_order if isinstance(attribute_order, dict) else {}
    out.level = (get(item, 'ItemLevel', 1) or 1) + (get(item, 'VirtualLevelOffset', 0.0) or 0.0)
    if not meta:
        ratios = []
        for a in kept:
            r = ranges.get(a.name)
            v = gamedata.value_in_range(r, a.position)
            if r and r[1] == '-' and v and original.get(a.name):
                ratios.append(original[a.name] / v)
        if ratios:
            ratios.sort()
            out.level_scale = ratios[len(ratios) // 2]

    for a in kept:
        r = ranges.get(a.name) or _borrowed_range(item, a.name, game_items)
        v = gamedata.value_in_range(r, a.position)
        exact = False
        scale = None  # the multiplier for the values of the range
        if v is not None and a.name in meta:
            scale = gamedata.level_scale(meta[a.name], out.level)
            exact = meta[a.name].get('growth_op') == 'c' or meta[a.name].get('growth', 1) == 1
        elif v is not None and r is not None and r[1] == '!':
            scale, exact = 1.0, True
        elif v is not None and r is not None and r[1] == '-' and out.level_scale:
            scale = out.level_scale
        elif not out.validated:
            a.base = original.get(a.name)
        if v is not None and r is not None and scale is not None:
            a.base = v * scale
            a.span = (r[0] * scale, r[2] * scale)
        recorded = original.get(a.name)
        if (
            recorded is not None
            and a.base is not None
            and exact
            and abs(recorded - a.base) > 1e-3 * max(abs(recorded), abs(a.base), 1e-9)
        ):
            out.mismatches.append((a.name, recorded, a.base))

        zero = v == 0 and r[0] == 0 and r[2] == 0 if v is not None and r is not None else original.get(a.name) == 0
        if a.name in NOT_STATS or zero:
            a.direction = 0  # a flag, or a value Chaos cannot change because it is zero
        elif LOWER_IS_BETTER.search(a.name) or (v is not None and r is not None and r[0] > r[2]):
            a.direction = -1  # the data lists the better end of a range last
        if a.name in lower:
            a.direction = -1
        if a.name in higher:
            a.direction = 1
        if a.name in ignore or (only and a.name not in only):
            a.direction = 0
    return out


def classify(
    item: Item,
    game_items: dict[str, Any],
    lower: Collection[str] = (),
    higher: Collection[str] = (),
    ignore: Collection[str] = (),
    only: Collection[str] = (),
    attribute_order: Iterable[str] = (),
) -> list[Kind]:
    """[(factor index, attribute, direction)] for the attributes the game keeps."""
    return analyse(item, game_items, lower, higher, ignore, only, attribute_order).kinds()


def effective(factor: float, direction: int) -> float:
    """A factor expressed so that larger is always better."""
    return factor if direction > 0 else 2 * (FACTOR_MIN + FACTOR_SPAN / 2) - factor
