# sunshine_manager.py
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

import logging

from cartridges import shared
from cartridges.game import Game
from cartridges.store.managers.async_manager import AsyncManager
from cartridges.store.managers.file_manager import FileManager
from cartridges.utils.sunshine import add_game, is_eligible


class SunshineManager(AsyncManager):
    """Manager exporting games to Sunshine when auto-sync is enabled.

    Runs after :class:`FileManager` so the local library is persisted
    first. The integration is strictly optional: when the
    ``sunshine-auto-sync`` setting is off (the default) or a game is not
    exportable (xCloud, removed, no executable), it returns silently.
    Any failure is logged and never breaks the pipeline.
    """

    run_after = (FileManager,)
    signals = {"save-ready"}

    def main(self, game: Game, _additional_data: dict) -> None:
        try:
            auto_sync = shared.schema.get_boolean("sunshine-auto-sync")
        except Exception as error:  # pylint: disable=broad-exception-caught
            logging.debug("Sunshine auto-sync check failed: %s", error)
            return
        if not auto_sync:
            return
        if not is_eligible(game):
            return
        try:
            custom = shared.schema.get_string("sunshine-apps-path")
        except Exception as error:  # pylint: disable=broad-exception-caught
            logging.debug("Sunshine custom path read failed: %s", error)
            custom = ""
        try:
            add_game(game, custom)
        except Exception as error:  # pylint: disable=broad-exception-caught
            logging.warning(
                "Sunshine auto-sync skipped for %s (%s): %s",
                game.name,
                game.game_id,
                error,
            )
