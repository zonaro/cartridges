# twintail_source.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

import logging
from pathlib import Path
from shutil import rmtree
from sqlite3 import connect
from typing import NamedTuple

from cartridges import shared
from cartridges.game import Game
from cartridges.importer.location import Location, LocationSubPath
from cartridges.importer.source import (
    ExecutableFormatSource,
    SourceIterable,
)
from cartridges.launchers import resolve_twintail_command
from cartridges.utils.sqlite import copy_db


class TwintailSourceIterable(SourceIterable):
    source: "TwintailSource"

    def __iter__(self):
        db_path = copy_db(self.source.locations.data["storage.db"])
        try:
            connection = connect(db_path)
            try:
                cursor = connection.execute(
                    "SELECT id, name, directory, game_icon, "
                    "game_background FROM install ORDER BY sort_order ASC"
                )
                rows = cursor.fetchall()
            except Exception as error:  # pylint: disable=broad-exception-caught
                logging.debug("Couldn't query TwinTail storage.db", exc_info=error)
                return
            finally:
                connection.close()

            for row in rows:
                install_id, name, directory, game_icon, game_background = (
                    row[0] if len(row) > 0 else None,
                    row[1] if len(row) > 1 else None,
                    row[2] if len(row) > 2 else None,
                    row[3] if len(row) > 3 else None,
                    row[4] if len(row) > 4 else None,
                )
                if not install_id or not name:
                    continue

                values = {
                    "added": shared.import_time,
                    "source": self.source.source_id,
                    "name": name,
                    "game_id": self.source.game_id_format.format(
                        game_id=install_id
                    ),
                    "executable": self.source.make_executable(
                        install_id=install_id
                    ),
                }
                game = Game(values)

                additional_data = {}
                if game_icon:
                    if str(game_icon).startswith("http"):
                        additional_data["online_cover_url"] = str(game_icon)
                    elif Path(str(game_icon)).is_file():
                        additional_data["local_image_path"] = Path(str(game_icon))
                if (
                    game_background
                    and "online_cover_url" not in additional_data
                    and str(game_background).startswith("http")
                ):
                    additional_data["online_cover_url"] = str(game_background)

                yield (game, additional_data)
        finally:
            rmtree(str(db_path.parent), ignore_errors=True)


class TwintailLocations(NamedTuple):
    data: Location


class TwintailSource(ExecutableFormatSource):
    source_id = "twintail"
    name = _("TwinTail")
    iterable_class = TwintailSourceIterable
    executable_format = "twintaillauncher --install {install_id}"
    available_on = {"linux"}

    def make_executable(self, *args, **kwargs) -> str:
        install_id = kwargs.get("install_id", args[0] if args else "")
        return resolve_twintail_command(install_id)

    locations: TwintailLocations

    def __init__(self) -> None:
        super().__init__()
        self.locations = TwintailLocations(
            Location(
                schema_key="twintail-location",
                candidates=(
                    shared.data_dir / "twintaillauncher",
                    shared.home / ".local" / "share" / "twintaillauncher",
                    shared.flatpak_dir / "app.twintaillauncher.ttl" / "data"
                    / "twintaillauncher",
                    shared.host_data_dir / "twintaillauncher",
                    shared.config_dir / "twintaillauncher",
                ),
                paths={
                    "storage.db": LocationSubPath("storage.db"),
                },
                invalid_subtitle=Location.DATA_INVALID_SUBTITLE,
            )
        )
