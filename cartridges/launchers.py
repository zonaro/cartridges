"""Resolve optional native/Flatpak launcher backends."""

from __future__ import annotations

import re
import subprocess
from shutil import which
from typing import Callable


_STEAM_URI = re.compile(r"steam://rungameid/(\d+)", re.IGNORECASE)

_TWINTAIL_FLATPAK_ID = "app.twintaillauncher.ttl"
TWINTAIL_FLATPAK_ID = _TWINTAIL_FLATPAK_ID


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


def is_twintail_command(command: str) -> bool:
    """Whether a stored executable launches a TwinTail game.

    Matches both the native ``twintaillauncher`` form and the Flatpak
    fallback (``flatpak run app.twintaillauncher.ttl ...``).
    """
    if not command:
        return False
    lowered = command.lower()
    if "twintaillauncher" in lowered:
        return True
    return _TWINTAIL_FLATPAK_ID.lower() in lowered


def resolve_twintail_command(
    install_id: str,
    *,
    find_program: Callable[[str], str | None] = which,
    has_flatpak: Callable[[str], bool] = flatpak_installed,
) -> str:
    """Build a TwinTail launch command from whatever backend is installed.

    ``--install=<id>`` is the only launch flag upstream documents (it means
    "launch installation by its ID", not "install something") and it is the
    exact form TwinTail itself writes into generated ``.desktop`` shortcuts.
    The ``=`` form is used here to mirror upstream.

    TwinTail is often installed only as a Flatpak, in which case no
    ``twintaillauncher`` binary exists on the host and the host form would
    fail with exit status 127. Fall back to the Flatpak then; when neither
    backend is found keep the host form as a best effort.

    NOTE: the flag is only honored on a cold start. TwinTail's single-instance
    plugin discards argv when an instance is already running (it only
    shows/focuses the window), so callers must terminate any running
    TwinTail instance before launching — see ``run_executable``. Fixes #17.
    """
    if find_program("twintaillauncher") is None and has_flatpak(
        _TWINTAIL_FLATPAK_ID
    ):
        return f"flatpak run {_TWINTAIL_FLATPAK_ID} --install={install_id}"
    return f"twintaillauncher --install={install_id}"
