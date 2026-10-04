"""Resolve optional native/Flatpak launcher backends."""

from __future__ import annotations

import re
import subprocess
from shutil import which
from typing import Callable


_STEAM_URI = re.compile(r"steam://rungameid/(\d+)", re.IGNORECASE)

_TWINTAIL_FLATPAK_ID = "app.twintaillauncher.ttl"


def flatpak_installed(app_id: str) -> bool:
    try:
        return subprocess.run(
            ("flatpak", "info", app_id),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=3,
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def resolve_steam_command(
    command: str,
    *,
    find_program: Callable[[str], str | None] = which,
    has_flatpak: Callable[[str], bool] = flatpak_installed,
) -> str:
    """Use a discovered Steam backend in quiet mode for a Steam launch URI."""
    match = _STEAM_URI.search(command or "")
    if not match:
        return command
    uri = match.group(0)
    if steam := find_program("steam"):
        return f'"{steam}" -silent "{uri}"'
    if find_program("flatpak") and has_flatpak("com.valvesoftware.Steam"):
        return f'flatpak run com.valvesoftware.Steam -silent "{uri}"'
    return command


def resolve_twintail_command(
    install_id: str,
    *,
    find_program: Callable[[str], str | None] = which,
    has_flatpak: Callable[[str], bool] = flatpak_installed,
) -> str:
    """Build a TwinTail launch command from whatever backend is installed.

    TwinTail is often installed only as a Flatpak, in which case no
    ``twintaillauncher`` binary exists on the host and the host form would
    fail with exit status 127. Fall back to the Flatpak then; when neither
    backend is found keep the host form as a best effort.
    """
    if find_program("twintaillauncher") is None and has_flatpak(
        _TWINTAIL_FLATPAK_ID
    ):
        return f"flatpak run {_TWINTAIL_FLATPAK_ID} --install {install_id}"
    return f"twintaillauncher --install {install_id}"
