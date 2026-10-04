# display_manager.py
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

import threading

from cartridges import shared
from cartridges.game import Game
from cartridges.game_cover import GameCover
from cartridges.store.managers.manager import Manager
from cartridges.store.managers.sgdb_manager import SgdbManager
from cartridges.store.managers.steam_api_manager import SteamAPIManager
from cartridges.store.managers.thegamesdb_manager import TheGamesDBManager
from cartridges.utils import agrupamento


def is_main_thread() -> bool:
    return threading.current_thread() is threading.main_thread()


class DisplayManager(Manager):
    """Manager in charge of adding a game to the UI"""

    run_after = (SteamAPIManager, TheGamesDBManager, SgdbManager)
    signals = {"update-ready"}

    def main(self, game: Game, _additional_data: dict) -> None:
        if game.get_parent():
            game.get_parent().get_parent().remove(game)
            if game.get_parent():
                game.get_parent().set_child()

        game.menu_button.set_menu_model(
            game.hidden_game_options if game.hidden else game.game_options
        )

        game.title.set_label(game.name)

        if game.is_launcher and not game.hidden:
            shared.win.set_library_child()
            if shared.win.get_application().state == shared.AppState.DEFAULT:
                shared.win.create_source_rows()
            return

        agrupamento.atualizar(game)

        members = agrupamento.membros(game)
        grouped = len(members) > 1
        primary = agrupamento.primario(members) if grouped else game
        is_primary = primary.game_id == game.game_id

        if grouped and not is_primary:
            agrupamento.garantir(game)
            shared.win.set_library_child()
            if shared.win.get_application().state == shared.AppState.DEFAULT:
                shared.win.create_source_rows()
            return

        if grouped and is_primary:
            for member in members:
                if member.game_id == game.game_id:
                    continue
                try:
                    parent = member.get_parent()
                    if parent is None:
                        continue
                    grand = parent.get_parent()
                    if grand is not None:
                        grand.remove(member)
                    if member.get_parent():
                        member.get_parent().set_child()
                except Exception:  # pylint: disable=broad-exception-caught
                    continue

        game.menu_button.set_menu_model(
            shared.win.build_card_menu(game, members if grouped else None)
        )

        try:
            source_name = shared.win.get_application().get_source_name(game.source)
        except Exception:  # pylint: disable=broad-exception-caught
            source_name = game.base_source
        if game.source == "imported" or not source_name or source_name == game.source:
            game.source_badge.set_visible(False)
        elif grouped:
            game.source_badge.set_label(
                f"{source_name} +{len(members) - 1}"
            )
            game.source_badge.set_visible(True)
        else:
            game.source_badge.set_label(str(source_name))
            game.source_badge.set_visible(True)

        game.menu_button.get_popover().connect(
            "notify::visible", game.toggle_play, None
        )
        game.menu_button.get_popover().connect(
            "notify::visible", shared.win.set_active_game, game
        )

        if game.game_id in shared.win.game_covers:
            game.game_cover = shared.win.game_covers[game.game_id]
            game.game_cover.add_picture(game.cover)
        else:
            game.game_cover = GameCover({game.cover}, game.get_cover_path())
            shared.win.game_covers[game.game_id] = game.game_cover

        if (
            shared.win.navigation_view.get_visible_page() == shared.win.details_page
            and shared.win.active_game == game
        ):
            shared.win.show_details_page(game)

        if game.zerado:
            shared.win.zerados_library.append(game)
            game.get_parent().set_focusable(False)
        elif not game.removed and not game.blacklisted:
            if game.hidden:
                shared.win.hidden_library.append(game)
            else:
                shared.win.library.append(game)
            game.get_parent().set_focusable(False)

        shared.win.set_library_child()

        if shared.win.get_application().state == shared.AppState.DEFAULT:
            shared.win.create_source_rows()
