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

"""The page of the pilot's own values: credits, level, and the name the load menu shows."""

from typing import TYPE_CHECKING

from .gtk import Gtk
from .widgets import INT_MAX, label

if TYPE_CHECKING:
    from .window import EditorWindow


PILOT_FIELDS = [  # (property, label, editable)
    ('credits', 'Credits', True),
    ('PlayerLevel', 'Level', True),
    ('XP', 'Experience', True),
    ('SaveGameVersion', 'Saved by game version', False),
    ('GameVersionOfStart', 'Started on version', False),
    ('CurrentLocation', 'Location', False),
    ('CurrentSystem', 'System', False),
    ('PlayingTimeSecondsDouble', 'Playing time', False),
]


def _duration(seconds: float) -> str:
    seconds = int(seconds)
    return '%d h %02d min' % (seconds // 3600, seconds % 3600 // 60)


class PilotPage:
    def __init__(self, window: EditorWindow) -> None:
        self.window = window

    def show(self) -> None:
        self.window.start_page('pilot', 'PILOT')
        grid = Gtk.Grid(column_spacing=16, row_spacing=8)
        grid.add_css_class('pilot-grid')
        row = 0
        with self.window.filling():
            if self.window.open_doc.save_name is not None:
                grid.attach(label('Name in the load menu', 'key'), 0, row, 1, 1)
                entry = Gtk.Entry(text=self.window.open_doc.save_name, max_length=100)
                entry.set_size_request(220, -1)
                entry.connect('changed', lambda e: self.set_save_name(e.get_text()))
                grid.attach(entry, 1, row, 1, 1)
                row += 1
            for key, title, editable in PILOT_FIELDS:
                if key not in self.window.open_doc.properties:
                    continue
                value = self.window.open_doc.scalar(key)
                grid.attach(label(title, 'key'), 0, row, 1, 1)
                if editable and isinstance(value, bool) is False and isinstance(value, (int, float)):
                    is_float = isinstance(value, float)
                    widget = self.window.spin(
                        value,
                        0,
                        4e9 if is_float else INT_MAX,
                        lambda v, k=key: self.set_pilot(k, v),
                        digits=1 if is_float else 0,
                        step=100 if key != 'PlayerLevel' else 1,
                    )
                    widget.set_size_request(220, -1)
                    widget.set_hexpand(False)
                else:
                    text = _duration(value) if key.startswith('PlayingTime') else str(value)
                    widget = label(text, selectable=True)
                grid.attach(widget, 1, row, 1, 1)
                row += 1
        self.window.page.append(grid)
        self.window.page.append(
            label(
                "The information shown in the game's Load menu comes from a separate preview "
                'file next to the main save file. The editor will keep the files in sync.',
                'dim',
                wrap=True,
            )
        )

    def set_save_name(self, name: str) -> None:
        if not self.window.busy:
            self.window.open_doc.set_save_name(name)
            self.window.update_title()

    def set_pilot(self, key: str, value: float) -> None:
        current = self.window.open_doc.scalar(key)
        self.window.open_doc.set_scalar(key, float(value) if isinstance(current, float) else int(value))
        self.window.update_title()
