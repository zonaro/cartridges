# run_executable.py
#
# Copyright 2023 redclaw
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
import time
from shlex import quote
from shutil import which

from cartridges import shared
from cartridges.game_launch import build_game_command
from cartridges.launchers import (
    TWINTAIL_FLATPAK_ID,
    is_twintail_command,
    resolve_steam_command,
)


_AUMID_CHARS = re.compile(r"[\w.!+\-]+")

_MARKER_RE = re.compile(re.escape("shell:AppsFolder\\"), re.IGNORECASE)


def _available_program(name: str):
    if os.getenv("FLATPAK_ID") != shared.APP_ID:
        return which(name)
    try:
        result = subprocess.run(
            ("flatpak-spawn", "--host", "sh", "-c", 'command -v -- "$1"', "sh", name),
            capture_output=True,
            encoding="utf-8",
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() or None


def _terminate_existing_twintail(timeout: float = 2.0) -> None:
    """Terminate running TwinTail instances so the launch argv is honored.

    TwinTail's single-instance plugin discards ``--install=<id>`` when an
    instance is already running and only shows/focuses its window (upstream
    issues #274/#280). The flag is only honored on a cold start, so a stale
    background/tray instance makes every Jolven launch "open the launcher
    underneath" without starting the game (jolven #17).

    Best effort and non-fatal: failures are logged, never raised.
    """
    in_flatpak = os.getenv("FLATPAK_ID") == shared.APP_ID
    prefix: tuple[str, ...] = (
        ("flatpak-spawn", "--host") if in_flatpak else ()
    )

    def _run(*args: str) -> None:
        try:
            subprocess.run(
                (*prefix, *args),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            logging.debug("TwinTail pre-launch cleanup %s: %s", args, error)

    # Native binary (exact name match only) and Flatpak instance.
    _run("pkill", "-x", "twintaillauncher")
    _run("flatpak", "kill", TWINTAIL_FLATPAK_ID)

    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(
                (*prefix, "pgrep", "-x", "twintaillauncher"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            break
        if result.returncode != 0:
            break
        time.sleep(0.1)


def aumid_from_command(executable: str) -> str:
    found = _MARKER_RE.search(executable or "")
    if found is None:
        return ""

    match = _AUMID_CHARS.match(executable, found.end())
    return match.group() if match else ""


def run_executable(
    executable,
    *,
    use_gamemode=None,
    use_mangohud=None,
    working_directory=None,
    environment=None,
    gamescope_options="",
    fps_limit=0,
    resolution="",
    scaling_mode="",
):
    """Launch a game in its own process group and return the launcher process."""
    if use_gamemode is None:
        use_gamemode = shared.schema.get_boolean("game-mode-use-gamemode")
    if use_mangohud is None:
        use_mangohud = shared.schema.get_boolean("game-mode-use-mangohud")

    resolved_executable = (
        resolve_steam_command(executable)
        if shared.runtime.is_game_mode and os.name == "posix"
        else executable
    )
    if is_twintail_command(resolved_executable):
        _terminate_existing_twintail()
    command, integrations = build_game_command(
        resolved_executable,
        use_gamemode=use_gamemode,
        use_mangohud=use_mangohud,
        gamescope_options=gamescope_options,
        fps_limit=fps_limit,
        resolution=resolution,
        scaling_mode=scaling_mode,
        allow_gamescope=not shared.runtime.is_session,
        find_program=_available_program,
    )
    clean_environment = {
        str(key): str(value)
        for key, value in (environment or {}).items()
        if str(key)
        and str(key).replace("_", "").isalnum()
        and not str(key)[0].isdigit()
    }
    if clean_environment:
        assignments = " ".join(
            f"{key}={quote(value)}" for key, value in clean_environment.items()
        )
        command = f"env {assignments} {command}"
    args = (
        "flatpak-spawn --host /bin/sh -c " + quote(command)  # Flatpak
        if os.getenv("FLATPAK_ID") == shared.APP_ID
        else command  # Others
    )

    logging.info(
        "Launching `%s`%s",
        str(args),
        f" with {', '.join(integrations)}" if integrations else "",
    )
    # pylint: disable=consider-using-with
    return subprocess.Popen(
        args,
        cwd=working_directory or shared.home,
        env={**os.environ, **clean_environment},
        shell=True,
        start_new_session=True,
    )
