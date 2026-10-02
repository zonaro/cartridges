# yuzu_source.py
#
# Copyright 2023 Geoffrey Coulaud
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

import os
from configparser import ConfigParser
from pathlib import Path
from shlex import quote as shell_quote
from typing import Generator, Iterable, NamedTuple

from cartridges import shared
from cartridges.game import Game
from cartridges.importer.location import Location, LocationSubPath
from cartridges.importer.source import (
    ExecutableFormatSource,
    SourceIterable,
    SourceIterationResult,
)


class YuzuSourceIterable(SourceIterable):
    source: "YuzuSource"

    extensions = (".xci", ".nsp", ".nso", ".nro")

    def iter_game_dirs(self) -> Iterable[tuple[bool, Path]]:
        """
        Get the rom directories from the parsed config

        The returned tuple indicates if the dir should be scanned recursively,
        then its path.
        """

        # Get the config data
        config = ConfigParser()
        if not config.read(
            self.source.locations.data["qt-config.ini"], encoding="utf-8"
        ):
            return

        # Iterate through the dirs
        n_dirs = config.getint("UI", r"Paths\gamedirs\size", fallback=0)
        for i in range(1, n_dirs + 1):
            deep = config.getboolean(
                "UI", f"Paths\\gamedirs\\{i}\\deep_scan", fallback=False
            )
            path_str = config.get("UI", f"Paths\\gamedirs\\{i}\\path", fallback="")
            if not path_str:
                continue
            yield deep, Path(path_str)

    def iter_rom_files(
        self, root: Path, recursive: bool = False
    ) -> Generator[Path, None, None]:
        """Generator method to iterate through rom files"""
        if recursive:
            paths = (
                Path(dir_path) / name
                for dir_path, _dirs, file_names in os.walk(root)
                for name in file_names
            )
        else:
            paths = root.iterdir()
        try:
            for path in paths:
                if not path.is_file():
                    continue
                if path.suffix not in self.extensions:
                    continue
                yield path
        except OSError:
            return

    def __iter__(self) -> Generator[SourceIterationResult, None, None]:
        """Generator method producing games"""

        # Get the games
        for recursive_search, game_dir in self.iter_game_dirs():
            for path in self.iter_rom_files(game_dir, recursive_search):
                values = {
                    "added": shared.import_time,
                    "source": self.source.source_id,
                    "name": path.stem,
                    "game_id": self.source.game_id_format.format(game_id=path.stem),
                    "executable": self.source.make_executable(rom_path=path),
                }
                game = Game(values)
                additional_data = {}
                yield game, additional_data


class YuzuLocations(NamedTuple):
    data: Location


class YuzuSource(ExecutableFormatSource):
    name = _("Yuzu")
    source_id = "yuzu"
    available_on = {"linux"}
    iterable_class = YuzuSourceIterable

    locations: YuzuLocations

    def __init__(self) -> None:
        super().__init__()
        self.locations = YuzuLocations(
            Location(
                schema_key="yuzu-location",
                candidates=[
                    shared.flatpak_dir / "org.yuzu_emu.yuzu" / "config" / "yuzu",
                    shared.config_dir / "yuzu",
                    shared.host_config_dir / "yuzu",
                ],
                paths={"qt-config.ini": LocationSubPath("qt-config.ini")},
                invalid_subtitle=Location.DATA_INVALID_SUBTITLE,
            )
        )

    @property
    def executable_format(self) -> str:
        self.locations.data.resolve()
        is_flatpak = self.locations.data.root.is_relative_to(shared.flatpak_dir)
        base = "flatpak run org.yuzu_emu.yuzu" if is_flatpak else "yuzu"
        return f"{base} -f {{rom_path}}"

    def make_executable(self, rom_path: Path) -> str:
        return self.executable_format.format(rom_path=shell_quote(str(rom_path)))
