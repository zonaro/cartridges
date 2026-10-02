# process_monitor.py
#
# Copyright 2026 joaomgabaldi (Windows version)
# Linux port: replaces Win32 Toolhelp/package APIs with /proc scanning.
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

"""Answer "is this game running?" from /proc.

Linux counterpart of the fork's Win32 process monitor. The public surface is
kept identical — :func:`is_process_running`,
:func:`is_process_running_under`, :func:`install_dir_from_command` and
:func:`is_package_running` — so :class:`ProcessSession` needs no
platform branches. Only the enumeration mechanism changes: instead of a
Toolhelp snapshot plus ``OpenProcess``/``QueryFullProcessImageNameW`` per pid,
each ``/proc/<pid>`` entry is read directly (``comm`` for the name,
``exe`` symlink for the full image path).

Notes on the mapping:

* ``is_package_running`` has no Linux meaning (no MSIX/UWP packages) and
  always answers False. It is kept as a stub so the session tracker keeps a
  single code path on both platforms.
* Name matching keeps the fork's casefold semantics. The ".exe" variant
  handling is kept too: Proton/Wine games genuinely show up as ``game.exe``,
  and a user typing ``game`` should still match.
* ``install_dir_from_command`` parses POSIX paths (quoted or bare) instead of
  ``C:\\`` paths, then applies the same game-root climb and container-dir
  guards, adapted to Linux library layouts (``steamapps``, ``common``,
  ``compatdata``, Lutris/Heroic/Bottles prefixes, ``drive_c``).
"""

import logging
import os
import re
from pathlib import Path
from typing import Callable, Optional


def _iter_processes() -> list[tuple[int, str, Optional[str]]]:
    """Snapshot of (pid, comm name, exe path or None) for every process.

    ``comm`` is the kernel's 15-char truncated name; ``exe`` is resolved
    through the ``/proc/<pid>/exe`` symlink and may be None for kernel
    threads or processes we are not allowed to inspect.
    """
    result = []
    try:
        pids = [entry for entry in os.listdir("/proc") if entry.isdigit()]
    except OSError as error:
        logging.warning("Could not list /proc: %s", error)
        return result
    for pid_str in pids:
        pid = int(pid_str)
        try:
            with open(f"/proc/{pid_str}/comm", encoding="utf-8") as handle:
                comm = handle.read().strip()
        except OSError:
            continue
        try:
            exe = os.readlink(f"/proc/{pid_str}/exe")
        except OSError:
            exe = None
        result.append((pid, comm, exe))
    return result


def _matches(candidate: str, targets: set[str]) -> bool:
    return candidate.casefold() in targets


def _name_variants(name: str) -> set[str]:
    """Names to accept, so "game" also matches "game.exe" and vice versa."""
    lowered = name.casefold()
    variants = {lowered}
    if lowered.endswith(".exe"):
        variants.add(lowered[: -len(".exe")])
    else:
        variants.add(lowered + ".exe")
    return variants


def is_process_running(exe_name: str) -> bool:
    """Return True if a process with the given executable name is running.

    ``exe_name`` may be a bare name ("game", "game.exe") or a full path;
    only the file name is compared.
    """
    if not exe_name:
        return False

    name = os.path.basename(exe_name.strip().strip('"'))
    if not name:
        return False

    targets = _name_variants(name)
    for _pid, comm, exe in _iter_processes():
        if _matches(comm, targets):
            return True
        if exe is not None and _matches(os.path.basename(exe), targets):
            return True
    return False


def is_package_running(_package_family: str) -> bool:
    """Stub: MSIX/UWP packages do not exist on Linux."""
    return False


def is_process_running_under(directory: str) -> bool:
    """Return True if a running process' executable lives inside ``directory``.

    Matches at any depth: the executable that is alive changes during a
    session (a launcher handing off to the real game binary), but all of
    them live under the game's folder.
    """
    if not (prefix := _as_path_prefix(directory)):
        return False

    for _pid, _comm, exe in _iter_processes():
        if exe is not None and exe.casefold().startswith(prefix):
            return True
    return False


# An absolute POSIX path, quoted the way launch commands quote one. Anything
# between the quotes goes — spaces included, since quoting is exactly what
# makes a path with spaces unambiguous.
_QUOTED_BIN = re.compile(r'"(/[^"]+)"')
_BARE_BIN = re.compile(r"(?<!\S)(/[^\s'\"]+)(?!\S)")

# Folders that hold many unrelated games rather than one. Watching any of
# these would count every other game's processes as this game's playtime, so
# a command that resolves to one is treated as giving us no folder at all.
_CONTAINER_DIRS = frozenset(
    {
        "steamapps",
        "common",
        "compatdata",
        "steam",
        "games",
        "lutris",
        "heroic",
        "bottles",
        "drive_c",
        "program files",
        "program files (x86)",
        "xboxgames",
    }
)


def exe_name_from_command(executable: str) -> str:
    """The executable file name taken from a launch command, or "".

    Same candidate parsing as :func:`install_dir_from_command`: the last
    quoted or bare absolute path wins. Only the file name is returned —
    this is the cheap identity used before falling back to the folder
    watch. Launcher URIs and wrapper lines yield "".
    """
    if not executable:
        return ""

    candidates = _QUOTED_BIN.findall(executable) or _BARE_BIN.findall(executable)
    if not candidates:
        return ""

    return os.path.basename(candidates[-1])


def install_dir_from_command(executable: str) -> str:
    """The folder to watch for a game's processes, taken from its launch command.

    Returns "" when the command holds no executable path we are sure of (a
    launcher URI such as ``steam://``, ``xdg-open`` wrappers, ``flatpak run``
    lines) or when the folder it points at is too broad to identify one game.
    Being sure matters more than being clever here: watching the wrong folder
    credits someone else's process as playtime, which is worse than not
    watching at all.
    """
    if not executable:
        return ""

    candidates = _QUOTED_BIN.findall(executable) or _BARE_BIN.findall(executable)
    if not candidates:
        return ""

    # Last match, like the launch command itself: the game's executable comes
    # after any working-directory or launcher argument in front of it.
    directory = _game_root(os.path.dirname(candidates[-1]))
    return directory if _is_watchable_dir(directory) else ""


# How far up to look for the game's root before giving up. Deeper than any
# real layout needs; it only stops a malformed path from walking forever.
_MAX_ROOT_WALK = 8


def _game_root(directory: str) -> str:
    """The game's own folder, climbing out of any launcher or engine subfolder.

    A launch command points at whatever executable starts the game, which is
    frequently not at the game's root: Unreal titles launch out of
    ``Binaries/Linux``, Unity titles out of a subfolder, Proton games out of
    ``drive_c/...``. The climb stops at the first folder carrying an
    uninstaller marker, or else at the folder whose parent is a well-known
    container of many games. Returns "" when neither is reached.
    """
    if not directory:
        return ""

    current = os.path.normpath(directory)
    below = ""  # the folder one level under `current` on the way up
    for _ in range(_MAX_ROOT_WALK):
        parent = os.path.dirname(current)
        if parent == current:
            return ""  # hit the filesystem root without finding a root marker
        if _has_uninstaller(current):
            return current
        container = os.path.basename(parent).casefold()
        if container in _CONTAINER_DIRS:
            if below:
                current = below
            return current
        below, current = current, parent
    return ""


def _has_uninstaller(directory: str) -> bool:
    """Was ``directory`` installed as one program, rather than holding several?"""
    try:
        with os.scandir(directory) as entries:
            return any(
                entry.name.casefold()
                in {
                    "unins000.exe",
                    "uninstall.exe",
                    "uninstall",
                    "__installer",
                }
                or (
                    entry.name.casefold().startswith("unins")
                    and entry.name.casefold().endswith(".exe")
                )
                for entry in entries
            )
    except OSError:
        return False


def _is_watchable_dir(directory: str) -> bool:
    """Is ``directory`` specific enough to stand for exactly one game?"""
    if not directory:
        return False

    resolved = os.path.normpath(directory)
    if resolved in ("/", ""):
        return False  # a filesystem root matches every process on the disk
    if os.path.basename(resolved).casefold() in _CONTAINER_DIRS:
        return False
    if resolved in ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/opt"):
        return False  # system locations never identify one game
    return True


def _as_path_prefix(directory: str) -> str:
    """``directory`` as a casefolded prefix ending in exactly one separator.

    Symlinks are resolved first: ``/proc/<pid>/exe`` reports the path a
    process was really loaded from, so a folder reached through a symlink
    would otherwise never compare equal to it. The trailing separator is
    what stops ".../Battlefield 1" from matching ".../Battlefield 11/game".
    """
    if not (cleaned := directory.strip().strip('"')):
        return ""

    try:
        resolved = os.path.realpath(cleaned)
    except (OSError, ValueError):
        return ""

    return os.path.join(resolved, "").casefold()


def _any_process(predicate: Callable[[int, str], bool]) -> bool:
    """True if ``predicate(pid, exe name)`` holds for any running process."""
    try:
        for pid, comm, _exe in _iter_processes():
            if predicate(pid, comm):
                return True
    except Exception as error:  # pylint: disable=broad-exception-caught
        logging.warning("Could not enumerate running processes: %s", error)
    return False
