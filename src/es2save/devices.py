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

"""The devices a pilot has unlocked, and how far each is upgraded (see "Devices" in FORMAT.md).

A device is not an item that is found and kept: once unlocked it is always there to be put
in a ship's device slots. The save lists the unlocked ones by name, and keeps the level of
each that has been upgraded with the tokens a level-up brings. Everything here changes the
save tree in place, like items.py.
"""

from . import items
from .gamedata import GameData
from .savegame import Properties

UNLOCKED = 'UnlockedDevices'
NEW = 'NewDevices'  # unlocked ones the game marks as new until they are looked at
LEVELS = 'DeviceMasteryLevels'
LEVELS_TYPE = 'MapProperty<NameProperty,IntProperty>'
TOKENS = 'DeviceUpgradeTokens'  # left out of the save while there are none
MAX_LEVEL = 5  # UItemLib::GetDeviceMasteryMaxLevel; the level that masters a device and opens its modes
# the devices a pilot can unlock, as the ship menu groups them; the others are built into a
# ship class or replace one of those
SECTIONS = [('cat_device_support', 'Support devices'), ('cat_device_warfare', 'Warfare devices')]


def sections(game: GameData) -> list[tuple[str, list[str]]]:
    """[(title, [device, ...]), ...] for the devices a pilot can unlock, in the game's order."""
    return [
        (title, [t for t in game.items if game.category(t) == 'cat_device' and game.sub_category(t) == sub])
        for sub, title in SECTIONS
    ]


def unlocked(props: Properties) -> list[str]:
    return list(items.get(props, UNLOCKED, []))


def set_unlocked(props: Properties, device: str, on: bool) -> None:
    """Unlocks a device, at the end of the list as the game does, or takes it away with its
    level and its mark as new."""
    have = unlocked(props)
    if on and device not in have:
        items.put(props, UNLOCKED, have + [device])
    elif not on and device in have:
        items.put(props, UNLOCKED, [d for d in have if d != device])
        if NEW in props:
            items.put(props, NEW, [d for d in items.get(props, NEW) if d != device])
        if LEVELS in props:
            items.put(props, LEVELS, [e for e in items.get(props, LEVELS) if e[0] != device])


def level(props: Properties, device: str) -> int:
    """The device's level, 1 to 5. One never upgraded has no entry and is at level 1
    (UItemLib::GetDeviceMasteryLevel)."""
    return next((int(v) for k, v in items.get(props, LEVELS, []) if k == device), 1)


def set_level(props: Properties, device: str, new: int) -> None:
    """Sets a device's level. A device at level 1 without an entry is left without one, as
    the game leaves it until the first upgrade."""
    if not 1 <= new <= MAX_LEVEL:
        raise ValueError('a device level is 1 to %d' % MAX_LEVEL)
    entries = items.get(props, LEVELS)
    if entries is None:
        if new == 1:
            return
        props[LEVELS] = {'type': LEVELS_TYPE, 'value': []}
        entries = props[LEVELS]['value']
    for entry in entries:
        if entry[0] == device:
            entry[1] = new
            return
    if new != 1:
        entries.append([device, new])


def tokens(props: Properties) -> int:
    """The device upgrade tokens the pilot has to spend."""
    return int(items.get(props, TOKENS, 0) or 0)


def set_tokens(props: Properties, count: int) -> None:
    """Sets the tokens to spend. None are written as no property at all, as the game writes
    them."""
    if count < 0:
        raise ValueError('a pilot cannot have fewer than no tokens')
    if not count:
        props.pop(TOKENS, None)
    elif TOKENS in props:
        items.put(props, TOKENS, count)
    else:
        props[TOKENS] = {'type': 'IntProperty', 'value': count}
