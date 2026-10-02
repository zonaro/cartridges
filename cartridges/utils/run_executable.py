# run_executable.py
#
# Copyright 2023 kramo
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

import logging
import os
import re
import subprocess
from shlex import quote

from cartridges import shared


_AUMID_CHARS = re.compile(r"[\w.!+\-]+")

_MARKER_RE = re.compile(re.escape("shell:AppsFolder\\"), re.IGNORECASE)


def aumid_from_command(executable: str) -> str:
    found = _MARKER_RE.search(executable or "")
    if found is None:
        return ""

    match = _AUMID_CHARS.match(executable, found.end())
    return match.group() if match else ""


def run_executable(executable) -> None:
    args = (
        "flatpak-spawn --host /bin/sh -c " + quote(executable)  # Flatpak
        if os.getenv("FLATPAK_ID") == shared.APP_ID
        else executable  # Others
    )

    logging.info("Launching `%s`", str(args))
    # pylint: disable=consider-using-with
    subprocess.Popen(
        args,
        cwd=shared.home,
        shell=True,
        start_new_session=True,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,  # type: ignore
    )
