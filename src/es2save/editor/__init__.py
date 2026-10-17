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

"""A GTK 4 editor for Everspace 2 saves (the es2save-editor command).

It needs PyGObject and GTK 4, which this module imports only when the editor starts so that
the rest of the package works without them.
"""

import argparse
import os
import sys
from collections.abc import Sequence

from .. import __version__
from .icons import find_icons

APP_ID = 'de.rockfishgames.es2save.editor'
COPYRIGHT = 'Copyright © 2026 KiasuBurger'
LICENCE = 'Free software under the GNU General Public License, version 3 or later.'
# the game's inventory menu icon, below the directory of extracted icons
APP_ICON = os.path.join('Icons', 'Menu', 'icon_menu_inventory_active_60x60_2x.png')
# whether the desktop lists its applications from .desktop files: Windows and macOS do not
DESKTOP_FILES = sys.platform not in ('win32', 'darwin')


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='es2save-editor', description="Edit an Everspace 2 save in a window laid out like the game's inventory."
    )
    p.add_argument('--version', action='version', version='%(prog)s ' + __version__)
    p.add_argument('save', nargs='?', help='save to open (otherwise use the Open button)')
    p.add_argument(
        '--game-data',
        metavar='PATH',
        help='game_identifiers.json (default: the one in the cache '
        'directory, which the editor builds from the installed game when it first runs)',
    )
    p.add_argument(
        '--icons',
        metavar='DIR',
        help='directory of extracted icons (default: the one in the '
        'cache directory, which the editor fills from the installed game when it first runs)',
    )
    p.set_defaults(install_desktop=False)
    if DESKTOP_FILES:
        p.add_argument(
            '--install-desktop',
            action='store_true',
            help="add the editor to the desktop's "
            'applications, which also gives its window an icon in the dock, and exit',
        )
    p.add_argument('--page', metavar='NAME', help=argparse.SUPPRESS)
    p.add_argument('--select', type=int, metavar='SEED', help=argparse.SUPPRESS)
    p.add_argument('--screenshot', metavar='PNG', help=argparse.SUPPRESS)
    return p


def desktop_entry(command: str, icon: str | None = None) -> str:
    """The text of the editor's .desktop file, which starts the editor. A desktop shows a window under the entry named after its application ID, so this is also
    what gives the running editor its icon."""
    lines = [
        '[Desktop Entry]',
        'Type=Application',
        'Name=Everspace 2 Save Editor',
        'Comment=Edit the items, ships and pilot of an Everspace 2 save',
        'Exec="%s" %s' % (command.replace('\\', '\\\\').replace('"', '\\"'), '%f'),
    ]
    if icon:
        lines.append('Icon=%s' % icon)
    lines += ['Terminal=false', 'Categories=Game;', 'StartupWMClass=%s' % APP_ID, 'StartupNotify=true']
    return '\n'.join(lines) + '\n'


def editor_command() -> str:
    """The es2save-editor command of this installation, beside the program that is running."""
    return os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), 'es2save-editor')


def install_desktop(
    icons: str | None = None, command: str | None = None, data_home: str | None = None
) -> tuple[str, str | None]:
    """Writes the .desktop file for this installation. Returns (its path, the icon used or None)."""
    command = command or editor_command()
    directory = find_icons(icons)
    icon = os.path.join(directory, APP_ICON) if directory else None
    if icon and not os.path.exists(icon):
        icon = None
    data_home = data_home or os.environ.get('XDG_DATA_HOME') or os.path.join(os.path.expanduser('~'), '.local', 'share')
    target = os.path.join(data_home, 'applications', APP_ID + '.desktop')
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, 'w') as f:
        f.write(desktop_entry(command, icon))
    return target, icon


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.install_desktop:
        target, icon = install_desktop(args.icons)
        print('wrote %s' % target)
        if not icon:
            print(
                "no icon: the game's icons have not been extracted, which the editor does when it "
                'first runs; run this again after that',
                file=sys.stderr,
            )
        return 0
    try:
        from .application import EditorApp
    except (ImportError, ValueError) as e:
        print(
            'es2save-editor: GTK 4 and PyGObject are needed to run the editor (%s).\n'
            "Install your distribution's python3-gobject and gtk4 packages and make the\n"
            'environment see them, e.g.: uv venv --system-site-packages --python /usr/bin/python3' % e,
            file=sys.stderr,
        )
        return 2
    app = EditorApp(args.save, args.game_data, args.icons, args.screenshot, args.select, args.page)
    return app.run([])


if __name__ == '__main__':
    sys.exit(main())
