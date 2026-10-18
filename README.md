# Everspace 2 Save Editor

This program lets you edit your Everspace 2 saves, with a focus on tuning ship
equipment. If you've been grinding for weeks to get an exact item, this is the
easy way out, if you can live with yourself. :-)

## Features

* Add, and modify any ship items
  * Set level, rarity, attributes, catalysts, etc
  * Pick the best Chaos value for Legendaries
* Set amounts of crafting materials and trading goods
* Set ship passives
* Unlock blueprints, catalysts, and ship components
* Unlock companion perks
* Set player level, XP, and credits

## Impossible items

The game enforces a large number of conditions on items, so there are limits
on how crazy you can get. The main thing you can do is stack as many non-prefix
catalysts as you want. But not prefix catalysts, as these actually don't do
anything except set the prefix, which is then what grants the bonus - so if you
stack them, only the last one does anything.

## Backups

The editor will automatically create a backup each time you save your changes.
This behaviour can be controlled in the Settings.

## Game Resources

The editor relies on the game to provide icons and item descriptions, and will
extract these from the game files the first time it runs. It tries to
autodetect your game installation directory, but if you have a sufficiently
unusual configuration, you might have to specify it manually. This does mean
you cannot really run the editor on a system that doesn't have the game
installed, but why would you do that?

## Installation

### Windows

Download `es2save-<version>-windows.zip` from the latest release on the
[Releases](https://github.com/KiasuBurger/everspace2-save-editor/releases) page, unzip it
anywhere, and run `es2save-editor.exe` in the folder it contains. The folder has everything the
editor needs, so nothing else has to be installed.

The program is not signed, so Windows may warn about it the first time it is run; "More info"
and then "Run anyway" starts it.

### Linux

The editor needs Python 3.14 or later, GTK 4 and PyGObject. These normally come from the
distribution rather than from PyPI, for example:

```
sudo apt install python3-gi gir1.2-gtk-4.0          # Debian, Ubuntu
sudo dnf install python3-gobject gtk4               # Fedora
sudo pacman -S python-gobject gtk4                  # Arch
```

libadwaita (`gir1.2-adw-1`, `libadwaita`) is optional; with it the window follows the desktop's
light or dark setting and accent colour.

#### From a source checkout, with uv

```
git clone https://github.com/KiasuBurger/everspace2-save-editor.git
cd everspace2-save-editor
uv venv --system-site-packages --python /usr/bin/python3
uv sync
uv run es2save-editor
```

The environment is made on the system Python, with access to its packages, so that it sees
the distribution's PyGObject.

#### With pipx

```
pipx install --system-site-packages --python /usr/bin/python3 \
    git+https://github.com/KiasuBurger/everspace2-save-editor.git
es2save-editor
```

From a source checkout, `pipx install --system-site-packages --python /usr/bin/python3 .` does
the same.

Either way, `es2save-editor --install-desktop` adds the editor to the desktop's applications.
Run the editor once first, so that it has the game's icons to use for itself.

### macOS

The editor has not been tried on macOS; this is how it is expected to install. It needs
[Homebrew](https://brew.sh) for Python, GTK 4 and PyGObject:

```
brew install python@3.14 gtk4 pygobject3 libadwaita adwaita-icon-theme pipx
pipx install --system-site-packages --python "$(brew --prefix python@3.14)/bin/python3.14" \
    git+https://github.com/KiasuBurger/everspace2-save-editor.git
es2save-editor
```

Or from a source checkout, with uv:

```
git clone https://github.com/KiasuBurger/everspace2-save-editor.git
cd everspace2-save-editor
uv venv --system-site-packages --python "$(brew --prefix python@3.14)/bin/python3.14"
uv sync
uv run es2save-editor
```

`pygobject3` has to be built for the same Python that the editor runs on. If `brew info pygobject3`
names a later Python than 3.14, install that one instead and use it in the commands above.

## Troubleshooting

If you find the program crashes on Windows, you can find the log file at
`%LOCALAPPDATA%\es2save\editor.log`. On Linux or MacOS, if you run
from a terminal, you will see the output directly.

Please attach it when reporting problems.

## Licence

Copyright © 2026 KiasuBurger. This program is free software: you can redistribute it and
modify it under the terms of the GNU General Public License, version 3 or, at your option, any
later version. It comes with no warranty. The licence is in [LICENSE](LICENSE).

`kraken.py` is based on `kraken.cpp` of [ooz](https://github.com/powzix/ooz), Copyright © 2016
Powzix, which is under the same licence.
