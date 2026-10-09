"""Optional GPU Screen Recorder launcher.

The desktop application is preferred because it covers native and Flatpak
installs without Jolven needing to know how either package is laid out. The
known native executables are conservative fallbacks for installs without a
desktop entry.
"""

from shutil import which
from typing import Any, Iterable

from gi.repository import Gio


DESKTOP_IDS = (
    "com.dec05eba.gpu_screen_recorder.desktop",
    "gpu-screen-recorder-gtk.desktop",
)
EXECUTABLES = ("gpu-screen-recorder-gtk", "gsr-ui")


def find_desktop_application(applications: Iterable[Any]) -> Any | None:
    """Select a supported desktop application, preferring the official ID."""
    by_id = {
        app.get_id(): app for app in applications if app.get_id() is not None
    }
    return next((by_id[item] for item in DESKTOP_IDS if item in by_id), None)


def launch() -> bool:
    """Launch GPU Screen Recorder, returning whether an install was found."""
    if app := find_desktop_application(Gio.AppInfo.get_all()):
        app.launch([], None)
        return True

    for executable in EXECUTABLES:
        if path := which(executable):
            Gio.Subprocess.new([path], Gio.SubprocessFlags.NONE)
            return True

    return False
