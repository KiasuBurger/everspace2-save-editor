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

"""What es2save-editor.exe runs."""

import faulthandler
import multiprocessing
import os
import sys

from es2save import __version__, gamepaths
from es2save.editor import main

LOG_NAME = 'editor.log'
OLD_LOG_NAME = 'editor.old.log'


def open_log() -> None:
    """Send stdout/err, GTK's warnings and the Python stack of a crash to
    editor.log in the cache directory. The editor has no console, so all of that would
    otherwise be lost. The log of the run before is kept as editor.old.log, so starting
    the editor again after a crash does not wipe the log of the crash."""
    directory = gamepaths.cache_dir()
    path = os.path.join(directory, LOG_NAME)
    try:
        os.makedirs(directory, exist_ok=True)
        if os.path.exists(path):
            os.replace(path, os.path.join(directory, OLD_LOG_NAME))
        log = open(path, 'w', buffering=1, encoding='utf-8', errors='replace')  # noqa: SIM115 (open until exit)
    except OSError:
        return
    sys.stdout = sys.stderr = log
    try:
        # GTK writes its warnings to the C library's stderr, not Python's
        os.dup2(log.fileno(), 2)
    except OSError:
        pass
    faulthandler.enable(log)
    print('es2save-editor %s' % __version__)


if __name__ == '__main__':
    # the seed search's worker processes are this program started again
    multiprocessing.freeze_support()
    open_log()
    sys.exit(main())
