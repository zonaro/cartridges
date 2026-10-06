# waydroid_source.py
#
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: Copyright 2025 Zoey Ahmed

from pathlib import Path
from typing import NamedTuple

from gi.repository import GLib

from cartridges import shared
from cartridges.game import Game
from cartridges.importer.source import Source, SourceIterable
from cartridges.utils.waydroid import package_id_from_exec


SYSTEM_APPS = frozenset(
    {
        "com.android.calculator2",
        "com.android.deskclock",
        "com.android.documentsui",
        "com.android.gallery3d",
        "com.android.settings",
        "com.android.vending",
        "com.google.android.apps.messaging",
        "com.google.android.apps.restore",
        "com.google.android.apps.safetyhub",
        "com.google.android.contacts",
        "com.google.android.googlequicksearchbox",
        "org.lineageos.aperture",
        "org.lineageos.eleven",
        "org.lineageos.etar",
        "org.lineageos.jelly",
        "org.lineageos.recorder",
    }
)


class WaydroidSourceIterable(SourceIterable):
    source: "WaydroidSource"

    def __iter__(self):
        """Produce user-installed Waydroid apps, excluding system utilities."""

        seen_packages: set[str] = set()
        application_dirs = dict.fromkeys(
            (
                shared.host_data_dir / "applications",
                shared.data_dir / "applications",
            )
        )

        for application_dir in application_dirs:
            if not application_dir.is_dir():
                continue

            for entry in sorted(application_dir.glob("waydroid*.desktop")):
                keyfile = GLib.KeyFile.new()
                try:
                    keyfile.load_from_file(str(entry), GLib.KeyFileFlags.NONE)
                    executable = keyfile.get_string("Desktop Entry", "Exec")
                    name = keyfile.get_string("Desktop Entry", "Name")
                except GLib.Error:
                    continue

                package_id = package_id_from_exec(executable)
                if (
                    package_id is None
                    or package_id in SYSTEM_APPS
                    or package_id in seen_packages
                ):
                    continue
                seen_packages.add(package_id)

                values = {
                    "source": self.source.source_id,
                    "added": shared.import_time,
                    "name": name,
                    "game_id": self.source.game_id_format.format(game_id=package_id),
                    "executable": executable,
                }
                game = Game(values)

                additional_data = {}
                try:
                    icon_path = Path(keyfile.get_string("Desktop Entry", "Icon"))
                except GLib.Error:
                    icon_path = (
                        shared.host_data_dir
                        / "waydroid"
                        / "data"
                        / "icons"
                        / f"{package_id}.png"
                    )
                if icon_path.is_file():
                    additional_data["local_icon_path"] = icon_path

                yield game, additional_data


class WaydroidLocations(NamedTuple):
    """Waydroid publishes apps in XDG data directories; no picker is needed."""


class WaydroidSource(Source):
    source_id = "waydroid"
    name = _("Waydroid")
    iterable_class = WaydroidSourceIterable
    available_on = {"linux"}

    locations: WaydroidLocations

    def __init__(self) -> None:
        super().__init__()
        self.locations = WaydroidLocations()
