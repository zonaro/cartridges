# sunshine.py
#
# Copyright 2026 Jolven contributors
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

"""Export Jolven games to Sunshine (lizardbyte/sunshine).

Sunshine reads its application list from ``apps.json`` in its config
directory (``{"env": {...}, "apps": [...]}``). The entry schema mirrors
the one used by LutrisToSunshine: ``name``, ``cmd``, ``output``,
``detached``, ``prep-cmd``, ``working-dir``, ``image-path``,
``exclude-global-prep-cmd``, ``elevated``, ``auto-detach``, ``wait-all``
and ``exit-timeout``.

This module is intentionally GTK-free and has no translatable strings:
it raises :class:`SunshineError` with short English messages and the UI
layer converts them into user-facing toasts. All catalog mutations go
through a module-level lock and atomic file replacement, and foreign
entries (added by the user or other tools) are always preserved.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading

from pathlib import Path
from shlex import quote
from typing import Any, Iterable, Mapping


class SunshineError(Exception):
    """Raised when a Sunshine catalog cannot be read or updated."""


_LOCK = threading.Lock()

APPS_FILENAME = "apps.json"
CONFIG_DIRNAME = "sunshine"

#: Flatpak Sunshine keeps its config outside the Jolven sandbox, so the
#: native path stays the default and this is only documented for the
#: custom-path setting.
FLATPAK_APPS_PATH = (
    Path.home()
    / ".var/app/dev.lizardbyte.app.Sunshine/config/sunshine/apps.json"
)

SUNSHINE_FLATPAK_ID = "dev.lizardbyte.app.Sunshine"
SUNSHINE_FLATHUB_URL = "https://dl.flathub.org/repo/flathub.flatpakrepo"
SUNSHINE_BINARY = "sunshine"


def _flatpak_app_dirs() -> list[Path]:
    home = Path.home()
    return [
        home / ".local/share/flatpak/app" / SUNSHINE_FLATPAK_ID,
        Path("/var/lib/flatpak/app") / SUNSHINE_FLATPAK_ID,
    ]


def installation_type() -> str:
    """Tell how Sunshine is installed: ``native``, ``flatpak`` or ``""``."""
    if shutil.which(SUNSHINE_BINARY):
        return "native"
    try:
        if any(path.is_dir() for path in _flatpak_app_dirs()):
            return "flatpak"
    except OSError:
        pass
    return ""


def is_installed() -> bool:
    """Tell whether Sunshine is available on this machine."""
    return bool(installation_type())


def _pgrep_exact(name: str) -> bool:
    pgrep = shutil.which("pgrep")
    if not pgrep:
        return False
    try:
        result = subprocess.run(
            [pgrep, "-x", name],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _flatpak_running() -> bool:
    flatpak = shutil.which("flatpak")
    if not flatpak:
        return False
    try:
        result = subprocess.run(
            [flatpak, "ps", "--columns=application"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    return any(
        line.strip() == SUNSHINE_FLATPAK_ID
        for line in result.stdout.splitlines()
    )


def is_running() -> bool:
    """Tell whether a Sunshine server process is currently running.

    Native matches the exact process name; Flatpak lists its own
    sandboxed apps, avoiding the false positives of a loose match.
    """
    kind = installation_type()
    if kind == "native":
        return _pgrep_exact(SUNSHINE_BINARY)
    if kind == "flatpak":
        return _flatpak_running()
    return False


def start_server() -> bool:
    """Start Sunshine detached, returning True when it was launched.

    Returns False when already running (nothing to do). Raises
    :class:`SunshineError` when not installed or spawning fails.
    """
    kind = installation_type()
    if not kind:
        raise SunshineError("Sunshine is not installed")
    if is_running():
        return False
    if kind == "native":
        command = [SUNSHINE_BINARY]
    else:
        flatpak = shutil.which("flatpak")
        if not flatpak:
            raise SunshineError("Flatpak is not available")
        command = [flatpak, "run", SUNSHINE_FLATPAK_ID]
    try:
        subprocess.Popen(  # pylint: disable=consider-using-with
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SunshineError(f"Could not start Sunshine: {error}") from error
    logging.debug("Sunshine starting (%s)", kind)
    return True


def install_flatpak() -> None:
    """Install Sunshine from Flathub for the current user (no sudo).

    Adds the user Flathub remote when missing and installs the app.
    Raises :class:`SunshineError` with a short message on any failure.
    """
    flatpak = shutil.which("flatpak")
    if not flatpak:
        raise SunshineError("Flatpak is not available")
    steps = (
        [
            flatpak,
            "remote-add",
            "--user",
            "--if-not-exists",
            "flathub",
            SUNSHINE_FLATHUB_URL,
        ],
        [flatpak, "install", "--user", "-y", "flathub", SUNSHINE_FLATPAK_ID],
    )
    for command in steps:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise SunshineError(f"Sunshine install failed: {error}") from error
        if result.returncode != 0:
            raise SunshineError("Sunshine install did not complete")
    if not is_installed():
        raise SunshineError("Sunshine install did not complete")
    logging.debug("Sunshine Flatpak install finished")


def resolve_apps_path(custom: str = "") -> Path:
    """Return the apps.json path to use.

    A non-blank ``custom`` path (user setting) always wins. Otherwise
    the existing catalog wins (native first, then Flatpak); when
    neither exists, the default follows the detected installation.
    """
    cleaned = (custom or "").strip()
    if cleaned:
        return Path(cleaned).expanduser()
    native = Path.home() / ".config" / CONFIG_DIRNAME / APPS_FILENAME
    flatpak = (
        Path.home()
        / ".var/app"
        / SUNSHINE_FLATPAK_ID
        / "config"
        / CONFIG_DIRNAME
        / APPS_FILENAME
    )
    try:
        if native.exists():
            return native
        if flatpak.exists():
            return flatpak
    except OSError:
        pass
    if installation_type() == "flatpak":
        return flatpak
    return native


def _str_attr(game: Any, name: str, default: str = "") -> str:
    value = getattr(game, name, default)
    return value if isinstance(value, str) else default


def is_eligible(game: Any) -> bool:
    """Tell whether ``game`` can be exported to Sunshine.

    Xbox Cloud Gaming titles stream from Microsoft's servers instead of
    running a local executable, so they are always skipped. Removed games
    and entries without an executable are skipped as well.
    """
    if game is None:
        return False
    source = _str_attr(game, "source")
    base_source = _str_attr(game, "base_source") or source.split("_")[0]
    if base_source == "xcloud" or source.startswith("xcloud"):
        return False
    if bool(getattr(game, "removed", False)):
        return False
    executable = _str_attr(game, "executable")
    return bool(executable.strip())


def build_command(game: Any) -> str:
    """Compose the Sunshine ``cmd`` for ``game``.

    Mirrors :mod:`cartridges.utils.run_executable`: environment entries
    are prepended as ``env K=quote(V)`` and the stored executable string
    (which may already contain arguments) is kept verbatim.
    """
    executable = _str_attr(game, "executable").strip()
    environment = getattr(game, "launch_environment", None)
    assignments = ""
    if isinstance(environment, Mapping):
        parts = [
            f"{key}={quote(str(value))}"
            for key, value in environment.items()
            if str(key)
            and str(key).replace("_", "").isalnum()
            and not str(key)[0].isdigit()
        ]
        if parts:
            assignments = "env " + " ".join(parts) + " "
    return f"{assignments}{executable}"


def build_app_entry(game: Any, image_path: str | None) -> dict[str, Any]:
    """Build a Sunshine app entry dict from a Jolven game."""
    return {
        "name": _str_attr(game, "name") or _str_attr(game, "game_id"),
        "cmd": build_command(game),
        "output": "",
        "detached": [],
        "prep-cmd": [],
        "working-dir": _str_attr(game, "launch_working_directory"),
        "image-path": image_path or "",
        "exclude-global-prep-cmd": False,
        "elevated": False,
        "auto-detach": True,
        "wait-all": True,
        "exit-timeout": 5,
    }


def read_catalog(path: Path) -> dict[str, Any]:
    """Read a Sunshine catalog, raising :class:`SunshineError` if unusable.

    A missing file yields an empty catalog. Anything else that is not a
    ``{"apps": [...]}`` mapping raises instead of being silently replaced,
    so a foreign or hand-edited file is never clobbered.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"env": {}, "apps": []}
    except OSError as error:
        raise SunshineError(f"cannot read {path}: {error}") from error
    try:
        catalog = json.loads(raw)
    except ValueError as error:
        raise SunshineError(f"invalid JSON in {path}: {error}") from error
    if not isinstance(catalog, dict) or not isinstance(
        catalog.get("apps"), list
    ):
        raise SunshineError(f"unexpected catalog shape in {path}")
    return catalog


def write_catalog(path: Path, catalog: Mapping[str, Any]) -> None:
    """Write ``catalog`` atomically via temp file + rename."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise SunshineError(f"cannot create {path.parent}: {error}") from error
    try:
        descriptor, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as tmp:
                json.dump(catalog, tmp, indent=2, sort_keys=True)
                tmp.write("\n")
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    except SunshineError:
        raise
    except OSError as error:
        raise SunshineError(f"cannot write {path}: {error}") from error


def upsert_app(path: Path, entry: Mapping[str, Any]) -> str:
    """Insert or update ``entry`` (matched by exact name) in ``path``.

    Returns ``"added"``, ``"updated"`` or ``"unchanged"``. Every other app
    and every top-level key (such as ``env``) is preserved byte-wise in
    value terms.
    """
    name = entry.get("name") if isinstance(entry, Mapping) else ""
    cmd = entry.get("cmd") if isinstance(entry, Mapping) else ""
    if not isinstance(name, str) or not name.strip():
        raise SunshineError("Sunshine entry needs a non-empty name")
    if not isinstance(cmd, str) or not cmd.strip():
        raise SunshineError(f"Sunshine entry {name!r} needs a command")
    with _LOCK:
        catalog = read_catalog(path)
        apps = catalog["apps"]
        for index, existing in enumerate(apps):
            if isinstance(existing, dict) and existing.get("name") == name:
                if existing == dict(entry):
                    return "unchanged"
                apps[index] = dict(entry)
                write_catalog(path, catalog)
                return "updated"
        apps.append(dict(entry))
        write_catalog(path, catalog)
        return "added"


def _cover_of(game: Any) -> str:
    getter = getattr(game, "get_cover_path", None)
    if not callable(getter):
        return ""
    try:
        cover = getter()
    except Exception:  # pylint: disable=broad-exception-caught
        return ""
    if cover is None:
        return ""
    text = str(cover)
    try:
        if not Path(text).is_file():
            return ""
    except (OSError, ValueError):
        return ""
    return text


def add_game(
    game: Any, custom_path: str = "", image_path: str | None = None
) -> str:
    """Export a single game to Sunshine, returning the entry name."""
    if not is_eligible(game):
        raise SunshineError("game cannot be exported to Sunshine")
    cover = image_path if image_path else _cover_of(game)
    entry = build_app_entry(game, cover or None)
    status = upsert_app(resolve_apps_path(custom_path), entry)
    logging.debug(
        "Sunshine export %s for %s", status, _str_attr(game, "name")
    )
    return entry["name"]


def sync_library(
    games: Iterable[Any], custom_path: str = ""
) -> dict[str, int]:
    """Export every eligible game, skipping the rest silently."""
    path = resolve_apps_path(custom_path)
    result = {"added": 0, "updated": 0, "unchanged": 0, "skipped": 0}
    for game in games:
        if not is_eligible(game):
            result["skipped"] += 1
            continue
        try:
            entry = build_app_entry(game, _cover_of(game) or None)
            result[upsert_app(path, entry)] += 1
        except SunshineError as error:
            logging.warning(
                "Sunshine sync skipped %s: %s",
                _str_attr(game, "name") or _str_attr(game, "game_id"),
                error,
            )
            result["skipped"] += 1
    return result
