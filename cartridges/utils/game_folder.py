# game_folder.py
#
# Copyright 2026 joaomgabaldi
#
# Linux port: the Windows package-registry lookup and AUMID handling are
# gone (no Microsoft Store here), paths are POSIX, and folders open in
# the desktop's file manager instead of Explorer.
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
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Where a game's files are, taken from the command that launches it.

A command names the executable it runs, and the folder is the one holding
it — even when that executable is a store client's own
(``steam -applaunch 440``): the folder offered is the client's, because
that is what the command runs, and telling "a game's exe" from "a
launcher's exe" would take a blocklist of every client ever shipped.

Everything else — ``steam://rungameid/…`` and the other launcher URIs —
is a request for some other program to start the game, and that program is
the only one that knows where it put it. There is nothing to guess from,
so those get None and the caller hides the button rather than opening the
wrong folder.
"""

import logging
import os
import re
from typing import Optional
from urllib.parse import quote as url_quote

from gi.repository import Gio

# The launch command's own paths, quoted (what the file chooser and the
# importer write) or bare (typed by hand), collected in the order they
# appear — so "the last launchable one is the game" keeps meaning that even
# when the two forms mix in one command. A bare path still stops at
# whitespace, because one with spaces cannot be told apart from its
# arguments.
_PATH = re.compile(r'"(?P<quoted>/[^"]*)"|(?<!\S)(?P<bare>/[^\s"\']*)(?!\S)')

# What counts as "the game's own executable" among the paths in a command.
# Linux game binaries are usually extensionless, so unlike the Windows
# original there is no suffix allowlist: a bare path is trusted when it is
# an actual file, quoted paths are trusted as written. Shell builtins and
# interpreter shims (`sh`, `flatpak`, `xdg-open`) carry no game folder, so
# well-known launcher wrappers are skipped.
_WRAPPER_NAMES = frozenset(
    {
        "sh",
        "bash",
        "dash",
        "env",
        "flatpak",
        "flatpak-spawn",
        "xdg-open",
        "steam",
        "lutris",
        "heroic",
        "bottles-cli",
    }
)


def game_folder(command: str) -> Optional[str]:
    """The folder a launch command points into, or None when it cannot be known.

    The folder is returned only when it exists on disk, so a game that has
    since been uninstalled or moved reads the same as one whose launcher
    hides it: no folder to offer.
    """
    if not (command := (command or "").strip()):
        return None

    return _executable_folder(command)


def _executable_folder(command: str) -> Optional[str]:
    """The folder holding the executable a command runs."""
    candidates = []
    for match in _PATH.finditer(command):
        if quoted := match.group("quoted"):
            candidates.append(quoted)
        else:
            bare = match.group("bare")
            # A bare path is only trusted whole: it must name an actual
            # file, otherwise a truncated fragment ("~/Games/Halo" out of
            # "~/Games/Halo CE/halo") would resolve to whatever neighbour
            # shares the prefix.
            if os.path.isfile(bare) and os.path.basename(bare) not in _WRAPPER_NAMES:
                candidates.append(bare)

    # The game's executable comes after whatever precedes it (a working
    # directory, a wrapper like `flatpak run`), and before any argument
    # that happens to be a path too — so the last file-backed one wins.
    for candidate in reversed(candidates):
        if os.path.isfile(candidate):
            return os.path.dirname(candidate) or "/"

    # The command names no file on disk. The game is gone (or launched
    # through a URI), and any folder still standing in the command is not
    # necessarily the game's: offering it would open somebody else's folder.
    if candidates:
        # ...unless a candidate is a directory itself (a working directory
        # the importer wrote): the first one that exists is offered.
        for candidate in candidates:
            if os.path.isdir(candidate):
                return candidate
        return None

    return None


def open_folder(directory: str) -> bool:
    """Show ``directory`` in the file manager. Returns whether it worked."""
    if not directory:
        return False

    logging.info("Opening folder `%s`", directory)
    try:
        Gio.AppInfo.launch_default_for_uri(
            f"file://{url_quote(directory)}", None
        )
    except Exception:
        # The folder was there when the button was shown; if it went away
        # since, the caller is what tells the user — this just gets it into
        # the log too.
        logging.exception("Could not open folder `%s`", directory)
        return False
    return True
