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

"""What the editor shows for an item, worked out from the save and the game data.

Kept free of GTK so that the numbers on screen can be tested: the card for an item is a
plain structure of strings.
"""

import re
from collections.abc import Iterable, Sequence

from .. import chaos, items
from ..gamedata import GameData
from ..itemrules import (
    catalyst_fits_rarity,
    effective_affix,
    evolution_fits,
    flawless_fits,
    is_decoder,
    is_stackable,
    legendary_parts,
    radiant_blessings,
)
from ..items import Inventory, Item

# attributes that are bookkeeping rather than something to read or edit
HIDDEN = chaos.NOT_STATS | {items.SOVEREIGN_ATTRIBUTE}  # the last is shown with its catalyst
BACKGROUNDS = {  # main category -> tile background pattern
    'cat_weapon': 'weapon',
    'cat_launcher': 'weapon',
    'cat_energy_core': 'module',
    'cat_shield': 'module',
    'cat_plating': 'module',
    'cat_sensor': 'module',
    'cat_thrusters': 'module',
    'cat_cargo_unit': 'module',
    'cat_device': 'module',
    'cat_consumable': 'consumable',
    'cat_item': 'material',
    'cat_collectible': 'quest_item',
}
TYPE_ICONS = {  # main category -> type icon name fragment
    'cat_weapon': 'primary',
    'cat_launcher': 'secondary',
    'cat_energy_core': 'energy',
    'cat_shield': 'shield',
    'cat_plating': 'armor',
    'cat_sensor': 'sensors',
    'cat_thrusters': 'thrusters',
    'cat_cargo_unit': 'cargo',
    'cat_device': 'devices',
    'cat_consumable': 'consumable',
    'cat_item': 'material',
    'cat_collectible': 'commodity',
}
GRADE_MARK = {'Prototype': 'P', 'Starforged': 'S', 'Radiant': 'R'}


class Stat:
    """One editable attribute row.

    kind      'bonus' (level-scaled stat that can be swapped), 'ranged', 'fixed', or 'count'
              (a whole number of its own: an Evolution catalyst's kills)
    value     text of the current value, '' when it cannot be worked out
    factor    Chaos factor applied to it, or None
    locked    the item's prefix or a catalyst holds it at its maximum, so it cannot be slid
    sliding   its range has room to slide in: a bonus stat whose range is one value has not
    span      the values shown at the two ends of its range, None when they are not known
    catalyst  the Mining or Okkar catalyst that added it: the card reads better with these
              under the catalyst than among the item's own stats"""

    def __init__(self, name: str, label: str, position: float, kind: str) -> None:
        self.name = name
        self.label = label
        self.position = position
        self.kind = kind
        self.value = ''
        self.display = ''  # the value rounded as the game's card shows it
        self.base: float | None = None
        self.shown: float | None = None
        self.span: tuple[float, float] | None = None
        self.factor: float | None = None
        self.locked = False
        self.sliding = True
        self.catalyst: str | None = None  # the catalyst that added it, where the card says so under that catalyst
        self.text = ''  # the line the game's card shows for it
        self.modifier = 1.0  # what the item's prefix and catalysts multiply it by
        self.low = self.high = ''


class Catalyst:
    """An installed catalyst as the card shows it.

    rarity       the catalyst's own rarity, which the game colours its name by
    description  the catalyst's own text with its numbers filled in
    effects      one line for each attribute it added to this item, where the value is known"""

    def __init__(
        self, cid: str, name: str, description: str = '', effects: Iterable[str] = (), rarity: str = 'None'
    ) -> None:
        self.id = cid
        self.name = name
        self.rarity = rarity
        self.description = description
        self.effects = list(effects)


class Card:
    """Everything the detail panel shows for one item."""

    def __init__(self) -> None:
        self.template = ''
        self.name = ''
        self.type = ''
        self.rarity = 'None'
        self.grade = 'None'
        self.level = 0
        self.amount = 0
        self.ammo: int | None = None  # a secondary weapon's ammo, and what it holds when full
        self.max_ammo: int | None = None
        self.affix = items.EMPTY
        self.effect = ''  # legendary effect text with its numbers filled in
        self.description = ''
        self.group = ''  # item set or manufacturer
        self.stats: list[Stat] = []
        self.dps: list[tuple[str, str]] = []  # [(label, text)] for weapons
        self.rank = ''  # 'Ascendant Rank: 8/8'
        self.loot_quality: float | None = None  # the loot quality of a signal decoder, as a fraction (0.3 is +30%)
        self.loot_quality_text = ''  # 'Loot quality: +30%', the text that the game's card shows
        self.legendary_parts: int | None = None  # the parts count that a legendary decoder keeps
        self.catalysts: list[Catalyst] = []  # [Catalyst]
        self.warnings: list[str] = []
        self.chaos = False
        self.seed: int | None = None
        self.stackable = False
        self.equipment = False


def tile_background(game: GameData, template: str) -> str:
    return BACKGROUNDS.get(game.category(template), 'material')


def type_icon(game: GameData, template: str) -> str:
    if template.startswith('catalyst_'):
        return 'catalyst'
    return TYPE_ICONS.get(game.category(template), 'NULL')


def display_name(game: GameData, item: Item) -> str:
    """'Primed Shield': the template's name with the affix in front, as the game shows it."""
    name = game.name(items.template(item))
    affix = effective_affix(game, item)
    if affix and affix != items.EMPTY:
        name = '%s %s' % (game.affix_name(affix), name)
    return name


def build_card(game: GameData, item: Item) -> Card:
    card = Card()
    tpl = items.template(item)
    info = game.item(tpl)
    card.template = tpl
    card.name = display_name(game, item)
    card.type = game.category_name(tpl)
    card.rarity = items.rarity(item)
    card.grade = items.grade(item)
    card.level = items.get(item, 'ItemLevel', 0)
    card.amount = items.get(item, 'Amount', 0)
    card.affix = effective_affix(game, item)
    card.description = info.get('description', '')
    card.group = info.get('group', '')
    card.catalysts = [describe_catalyst(game, item, c) for c in items.catalysts(item)]
    card.chaos = items.has_chaos(item)
    card.seed = items.catalyst_seed(item)
    card.stackable = is_stackable(game, item)
    card.equipment = game.is_equipment(tpl)

    analysis = chaos.analyse(item, game.items, attribute_order=game.attributes)
    factors = chaos.factors(card.seed, len(analysis.attributes)) if card.chaos and card.seed is not None else None
    ranges = game.ranges(tpl)
    values = {}
    catalyst_ids = items.catalysts(item)
    for a in analysis.attributes:
        if a.name in HIDDEN:
            continue
        r = ranges.get(a.name)
        if r and r[1] == '-':
            kind = 'bonus'
        elif r and r[0] != r[2]:
            kind = 'ranged'
        else:
            kind = 'fixed'
        stat = Stat(a.name, game.attribute_name(a.name), a.position, kind)
        stat.base = a.base
        stat.locked = a.maximised
        stat.sliding = kind == 'ranged' or (kind == 'bonus' and r is not None and r[0] != r[2])
        stat.factor = factors[a.index] if factors else None
        if a.base is not None:
            stat.modifier = game.modifier(card.affix, catalyst_ids, a.name)
            multiplier = stat.modifier * (stat.factor if stat.factor is not None else 1.0)
            stat.shown = a.base * multiplier
            if a.span:
                stat.span = (a.span[0] * multiplier, a.span[1] * multiplier)
            stat.value = game.format_value(a.name, stat.shown)
            stat.display = game.format_value(a.name, stat.shown, precise=False)
            stat.text = game.stat_text(a.name, stat.shown)
            values[a.name] = stat.display
        if a.name in items.EVOLUTION_ATTRIBUTES and items.EVOLUTION in catalyst_ids:
            # the catalyst's own two: its kills, which can be set, and the bonus they have reached
            stat.catalyst = items.EVOLUTION
            stat.factor = None
            stat.sliding = False
            if a.name == items.EVOLUTION_KILL_COUNT:
                kills = items.evolution_kills(item) or 0
                stat.kind, stat.shown = 'count', kills
                stat.value = stat.display = '%d' % kills
            else:
                stat.kind, stat.shown = 'fixed', a.position
                stat.value = game.format_value(a.name, stat.shown)
                stat.display = game.format_value(a.name, stat.shown, precise=False)
            stat.text = game.attribute_text(a.name, stat.shown)
            card.stats.append(stat)
            continue
        if r and isinstance(r[0], (int, float)) and isinstance(r[2], (int, float)) and r[1] == '!':
            stat.low, stat.high = game.format_value(a.name, r[0]), game.format_value(a.name, r[2])
        if kind == 'fixed' and not (stat.shown or 0):
            continue  # a zero the item never uses (a shield with no capacity of its own)
        card.stats.append(stat)

    for entry in card.catalysts:
        if entry.id == items.MINING or entry.id.startswith(items.OKKAR):
            added = {name for name, _pos in items.catalyst_grants(entry.id)}
            mine = [s for s in card.stats if s.name in added and s.text]
            for s in mine:
                s.catalyst = entry.id
            entry.effects[:0] = [s.text for s in mine]

    full = next((s.shown for s in card.stats if s.name == items.MAX_AMMO), None)
    if full is not None and 'Ammo' in item:
        card.ammo, card.max_ammo = items.get(item, 'Ammo', 0), round(full)

    effect = info.get('legendary_effect', '')
    card.dps = weapon_dps(card)
    count = items.ascendant_count(item)
    if count is not None and card.equipment:
        card.rank = game.stat_text(items.ASCENDANT_RANK, count + 1)
    if is_decoder(game, tpl):
        card.loot_quality = game.loot_quality(name for name, _ in items.attributes(item))
    if card.loot_quality is not None:
        # WG_LootQualityBox shows a whole percentage with a sign. It rounds half to even.
        card.loot_quality_text = 'Loot quality: %+d%%' % round(card.loot_quality * 100)
        card.legendary_parts = items.legendary_parts(item)
    card.effect = re.sub(r'\{(\w+)\}', lambda m: values.get(m.group(1), m.group(0)), effect)

    # Only what the current game would not produce is worth a warning. Everything found on
    # items written by game 1.3 or later passes (catalyst-added attributes, odd resource
    # rarities). Saves older than that can hold attributes the game has since dropped, and
    # those are flagged, correctly: they are no longer valid.
    for a in analysis.rejected:
        card.warnings.append(
            '%s is not an attribute the current game gives this item; it may be '
            'replaced when the save is loaded.' % game.attribute_name(a.name)
        )
    if analysis.reordered and card.chaos:
        card.warnings.append("The attributes are stored out of the game's order, which decides the Chaos factors.")
    for c in catalyst_ids:
        if c in game.catalysts and not catalyst_fits_rarity(game, c, card.rarity):
            card.warnings.append(
                'The game does not install %s on %s items.' % (game.catalyst_name(c), card.rarity.lower())
            )
        elif not flawless_fits(item, c):
            card.warnings.append('The game only installs Flawless on Radiant items with legendary attributes.')
        elif not evolution_fits(game, item, c):
            card.warnings.append('The game does not install Evolution on a weapon that does no damage.')
    # The game keeps one catalyst on an item besides Hexaspatial, and its own installing
    # goes wrong on more: it undoes all of them but takes out one, and re-applying what is
    # left has crashed it on an equipped item.
    if len([c for c in catalyst_ids if c != items.HEXASPATIAL]) > 1:
        card.warnings.append(
            'The game expects at most one catalyst per item, besides Hexaspatial. Attempting to '
            'install another catalyst on this item within the game could cause a crash. Use the '
            'editor instead.'
        )
    allowed = game.rarities(tpl)
    if card.equipment and card.rarity not in allowed:
        card.warnings.append('The game only makes this item as %s.' % _span(allowed))
    if card.equipment and card.grade != 'None' and card.grade not in items.grades_for(card.rarity):
        card.warnings.append('The game never puts %s on a %s item.' % (card.grade, card.rarity))
    if radiant_blessings(game, tpl) and (card.grade == 'Radiant') != bool(items.radiant_blessing(item)):
        card.warnings.append('A reliquary is Radiant exactly when it has a radiant blessing.')
    if card.affix in game.affixes and card.affix not in game.affixes_for(tpl):
        card.warnings.append('The %s prefix does not apply to this kind of item.' % game.affix_name(card.affix))
    expected = legendary_parts(game, item)
    if card.loot_quality is not None and card.legendary_parts != expected:
        card.warnings.append(
            'The game would give this decoder %s, not %s.' % (_parts(expected), _parts(card.legendary_parts))
        )
    if card.chaos and card.rarity != 'Legendary':
        card.warnings.append('Chaos only works on legendary items.')
    return card


def _parts(count: int | None) -> str:
    return 'no legendary crafting parts' if count is None else '%d legendary crafting parts' % count


def describe_catalyst(game: GameData, item: Item, catalyst: str) -> Catalyst:
    """The card entry for a catalyst installed on an item.

    Its description comes from the catalyst's definition, and the effects said here are the
    progress the item itself holds for the catalyst. build_card puts the attributes a Mining
    or Okkar catalyst added in front of them."""
    out = Catalyst(
        catalyst,
        game.catalyst_name(catalyst),
        game.catalyst_description(catalyst),
        rarity=game.item(catalyst).get('max_rarity') or 'None',
    )
    if catalyst == items.SOVEREIGN:
        bonus = items.sovereign_bonus(item)
        out.effects.append(
            game.attribute_text(items.SOVEREIGN_ATTRIBUTE, bonus) or 'Current Level Bonus: +%.1f' % bonus
        )
    if catalyst == items.EVOLUTION:
        kills = items.custom(item, items.EVOLUTION_KILLS)
        if kills is not None:
            out.effects.append(game.attribute_text(items.EVOLUTION_KILL_COUNT, kills) or 'Kills: %d' % kills)
        reached = items.position(item, items.EVOLUTION_BONUS)
        if reached:
            # stored directly as a fraction: 0.2 after the two thresholds of 10% each
            out.effects.append(game.attribute_text(items.EVOLUTION_BONUS, reached))
        extra = items.custom(item, items.EVOLUTION)
        if extra and extra != items.EMPTY:
            out.effects.append('Bonus attribute: %s' % game.attribute_name(extra))
    return out


def weapon_dps(card: Card) -> list[tuple[str, str]]:
    """[('Kinetic DPS', '6879'), ('Energy DPS', '2321')]: damage per shot times fire rate, as
    the game's weapon cards head them. Only for weapons that simply fire at a rate."""
    stats = {s.name: s.shown for s in card.stats if s.shown is not None}
    rate = stats.get('weapon_fire_rate')
    if not rate or any(k in stats and stats[k] for k in ('weapon_charge_duration', 'weapon_shots_per_burst')):
        return []
    out = []
    for name, title in (('weapon_hull_damage', 'Kinetic DPS'), ('weapon_shield_damage', 'Energy DPS')):
        if stats.get(name):
            out.append((title, '%d' % round(stats[name] * rate)))
    return out


def _span(values: Sequence[str]) -> str:
    return values[0] if len(values) == 1 else '%s to %s' % (values[0], values[-1])


# The game's Resources page: (title, how the things are come by, sub-categories).
RESOURCE_SECTIONS = (
    ('Parts', 'Gathered by dismantling corresponding equipment', ('cat_crafting_resource',)),
    ('Resources', 'Gathered by mining or trading', ('cat_resource_ore', 'cat_resource_crystal', 'cat_resource_misc')),
    ('Components', 'Gathered by crafting or trading', ('cat_resource_tec',)),
    ('Catalysts', 'Created by crafting', ('cat_crafting_catalyst',)),
)


def resource_sections(game: GameData, inventory: Inventory) -> list[tuple[str, str, list[int]]]:
    """[(title, note, [cargo index])] laying the resources out as the game does: its four
    sections, each with the highest rarity first and, within a rarity, in the order of the
    game's item list. Empty slots are left out, and what the game data does not know (or
    files elsewhere) comes last under 'Other'."""
    order = {t: n for n, t in enumerate(game.items)}
    by_sub = {sub: n for n, (_, _, subs) in enumerate(RESOURCE_SECTIONS) for sub in subs}
    found: list[list[tuple[int, int, int]]] = [[] for _ in range(len(RESOURCE_SECTIONS) + 1)]
    for index, item in enumerate(inventory.cargo):
        if items.is_empty(item):
            continue
        tpl = items.template(item)
        section = by_sub.get(game.sub_category(tpl), len(RESOURCE_SECTIONS))
        rarity = items.rarity(item)
        rank = items.RARITIES.index(rarity) if rarity in items.RARITIES else 0
        found[section].append((-rank, order.get(tpl, len(order)), index))
    out = [(title, note, [i for _, _, i in sorted(found[n])]) for n, (title, note, _) in enumerate(RESOURCE_SECTIONS)]
    if found[-1]:
        out.append(('Other', '', [i for _, _, i in sorted(found[-1])]))
    return out


def cargo_summary(inventory: Inventory) -> str:
    """The line beside an inventory's title. Storage and a ship's cargo have a capacity;
    resources and quest items are one stack per kind in a list with room to spare, so only
    what is there is counted."""
    used = len([i for i in inventory.cargo if not items.is_empty(i)])
    if inventory.kind == 'resources':
        return '%d resource type%s' % (used, '' if used == 1 else 's')
    if inventory.kind == 'quest':
        return '%d quest item%s' % (used, '' if used == 1 else 's')
    return '%d of %d cargo slots used' % (used, len(inventory.cargo))


def filter_group(game: GameData, tpl: str) -> str:
    """Which of the cargo filter's groups (FILTERS in inventory_page.py) an item is in."""
    cat = game.category(tpl)
    if cat in ('cat_weapon', 'cat_launcher'):
        return 'weapons'
    if game.is_equipment(tpl):
        return 'modules'
    if tpl.startswith('catalyst_'):
        return 'catalysts'
    if cat in ('cat_consumable', 'cat_device'):
        return 'consumables'
    return 'other'


def matches(game: GameData, item: Item, text: str, category: str) -> bool:
    """Whether an item passes the search box and the category filter."""
    tpl = items.template(item)
    if category and category != 'all' and filter_group(game, tpl) != category:
        return False
    if text:
        hay = ' '.join(
            (
                tpl,
                display_name(game, item),
                game.category_name(tpl),
                items.rarity(item),
                game.item(tpl).get('group', ''),
            )
        ).lower()
        return all(word in hay for word in text.lower().split())
    return True
