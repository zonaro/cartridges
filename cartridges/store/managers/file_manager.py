# file_manager.py
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

import json

from cartridges import shared
from cartridges.game import Game
from cartridges.store.managers.async_manager import AsyncManager
from cartridges.store.managers.steam_api_manager import SteamAPIManager
from cartridges.store.managers.thegamesdb_manager import TheGamesDBManager


class FileManager(AsyncManager):
    """Manager in charge of saving a game to a file"""

    run_after = (SteamAPIManager, TheGamesDBManager)
    signals = {"save-ready"}

    def main(self, game: Game, additional_data: dict) -> None:
        if additional_data.get("skip_save"):  # Skip saving when loading games from disk
            return

        shared.games_dir.mkdir(parents=True, exist_ok=True)

        attrs = (
            "added",
            "executable",
            "game_id",
            "source",
            "hidden",
            "last_played",
            "playtime",
            "name",
            "developer",
            "publisher",
            "release_date",
            "metacritic",
            "steam_review",
            "genre",
            "controller_support",
            "gamepad_recommended",
            "description",
            "tgdb_id",
            "tgdb_checked",
            "tgdb_platform",
            "tgdb_players",
            "tgdb_age_rating",
            "tgdb_coop",
            "tgdb_screenshots",
            "tgdb_fanart",
            "steam_checked",
            "steam_appid",
            "hltb_id",
            "hltb_main",
            "hltb_main_extra",
            "hltb_completionist",
            "hltb_chapters",
            "track_updates",
            "update_available_ts",
            "update_dismissed_ts",
            "update_url",
            "install_size",
            "install_size_ts",
            "status",
            "rating",
            "notes",
            "removed",
            "blacklisted",
            "version",
            "game_mode_use_gamemode",
            "game_mode_use_mangohud",
            "launch_working_directory",
            "launch_environment",
            "gamescope_options",
            "fps_limit",
            "game_resolution",
            "scaling_mode",
            "track_process",
            "process_executable",
            "is_launcher",
            "fundo_biblioteca",
        )

        json.dump(
            {attr: getattr(game, attr) for attr in attrs if attr},
            (shared.games_dir / f"{game.game_id}.json").open("w", encoding="utf-8"),
            indent=4,
            sort_keys=True,
        )
