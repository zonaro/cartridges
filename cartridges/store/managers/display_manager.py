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

from gi.repository import Gdk, Gtk

from cartridges import shared
from cartridges.game import Game
from cartridges.game_cover import GameCover
from cartridges.store.managers.manager import Manager
from cartridges.store.managers.sgdb_manager import SgdbManager
from cartridges.store.managers.steam_api_manager import SteamAPIManager
from cartridges.store.managers.thegamesdb_manager import TheGamesDBManager
from cartridges.utils import agrupamento
from cartridges.utils import launcher as launcher_utils


def is_main_thread() -> bool:
    return threading.current_thread() is threading.main_thread()


def _icone_da_fonte(source_id: str) -> str:
    """Melhor nome de ícone disponível para uma fonte/plataforma."""
    candidatos = launcher_utils.nomes_de_icone_da_fonte(source_id)
    try:
        display = Gdk.Display.get_default()
        tema = Gtk.IconTheme.get_for_display(display) if display else None
        if tema is not None:
            for nome in candidatos:
                if tema.has_icon(nome):
                    return nome
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    return candidatos[0] if candidatos else "application-x-executable-symbolic"


def _atualizar_badge_fontes(game: Game, members: list, grouped: bool) -> None:
    """Mostra lado a lado os ícones das plataformas do jogo.

    Um ícone por fonte distinta (base_source): jogo sozinho mostra um,
    jogo agrupado mostra um por plataforma disponível.
    """
    badge = game.source_badge
    while (filho := badge.get_first_child()) is not None:
        badge.remove(filho)

    alvos = members if grouped else [game]
    vistos: set[str] = set()
    nomes: list[str] = []
    icones: list[str] = []
    app = shared.win.get_application() if shared.win else None
    for membro in sorted(alvos, key=lambda m: (getattr(m, "source", "") or "")):
        base = getattr(membro, "base_source", None) or (membro.source or "").split("_")[0]
        if not base or base in vistos:
            continue
        try:
            nome = app.get_source_name(membro.source) if app else base
        except Exception:  # pylint: disable=broad-exception-caught
            nome = getattr(membro, "base_source", base)
        if not nome or nome == membro.source and membro.source == "imported":
            continue
        vistos.add(base)
        nomes.append(str(nome))
        icones.append(
            "__better_xcloud__"
            if base == "xcloud"
            else _icone_da_fonte(membro.source)
        )

    if not icones:
        badge.set_visible(False)
        badge.set_tooltip_text(None)
        return

    for nome_icone in icones:
        imagem = (
            Gtk.Image.new_from_resource(shared.PREFIX + "/xbox-cloud.png")
            if nome_icone == "__better_xcloud__"
            else Gtk.Image.new_from_icon_name(nome_icone)
        )
        imagem.set_pixel_size(16)
        badge.append(imagem)
    badge.set_tooltip_text(", ".join(nomes))
    badge.set_visible(True)


class DisplayManager(Manager):
    """Manager in charge of adding a game to the UI"""

    run_after = (SteamAPIManager, TheGamesDBManager, SgdbManager)
    signals = {"update-ready"}

    def main(self, game: Game, _additional_data: dict) -> None:
        if game.get_parent():
            game.get_parent().get_parent().remove(game)
            if game.get_parent():
                game.get_parent().set_child()

        if game.base_source == "xcloud" and not shared.schema.get_boolean(
            "xbox-cloud-gaming"
        ):
            if shared.win.get_application().state == shared.AppState.DEFAULT:
                shared.win.set_library_child()
                shared.win.create_source_rows()
            return

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
            while (filho := game.source_badge.get_first_child()) is not None:
                game.source_badge.remove(filho)
            game.source_badge.set_tooltip_text(None)
            game.source_badge.set_visible(False)
        else:
            _atualizar_badge_fontes(game, members if grouped else [game], grouped)

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
            game.get_parent().set_focusable(True)
        elif not game.removed and not game.blacklisted:
            if game.hidden:
                shared.win.hidden_library.append(game)
            else:
                shared.win.library.append(game)
            game.get_parent().set_focusable(True)

        shared.win.set_library_child()

        if shared.win.get_application().state == shared.AppState.DEFAULT:
            shared.win.create_source_rows()
