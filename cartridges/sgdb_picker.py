# sgdb_picker.py
#
# Copyright 2024 redclaw
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

"""A visual SteamGridDB cover picker.

Lets the user search SteamGridDB and pick a cover from a grid of previews
instead of downloading and selecting one by hand. Honours the animated-cover
preference: animated covers are shown (and previewed) as such, otherwise still
images are listed.
"""

import logging
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import requests
from gi.repository import Adw, GLib, Gtk

from cartridges import shared
from cartridges.game_cover import GameCover
from cartridges.utils.download import download_bytes
from cartridges.utils.na_tela import entregar_na_tela
from cartridges.utils.name_cleaner import clean_game_name
from cartridges.utils.save_cover import convert_cover
from cartridges.utils.steamgriddb import SgdbAuthError, SgdbError, SgdbHelper

# Cap how many previews we load so the dialog stays light, even when animating
MAX_RESULTS = 30

# Portrait cover dimensions on SteamGridDB. Without this filter the API also
# returns horizontal capsules; restricting to 600x900 alone drops most animated
# grids, which commonly use the other portrait sizes.
PORTRAIT_DIMENSIONS = "600x900,342x482,660x930"


@Gtk.Template(resource_path=shared.PREFIX + "/gtk/sgdb-picker.ui")
class SgdbPicker(Adw.Dialog):
    __gtype_name__ = "SgdbPicker"

    animated_button: Gtk.ToggleButton = Gtk.Template.Child()
    search_bar: Gtk.SearchBar = Gtk.Template.Child()
    search_entry: Gtk.SearchEntry = Gtk.Template.Child()
    stack: Gtk.Stack = Gtk.Template.Child()
    status_page: Adw.StatusPage = Gtk.Template.Child()
    flowbox: Gtk.FlowBox = Gtk.Template.Child()

    def __init__(
        self, name: str, on_selected: Callable[[Path], None], **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)

        self.on_selected = on_selected
        self.sgdb = SgdbHelper()

        # A generation counter invalidates in-flight searches when a new one
        # starts or the dialog is closed.
        self._generation = 0
        self._debounce_id = 0
        self._closed = False
        self._covers: list[GameCover] = []
        self._results: dict[Gtk.FlowBoxChild, str] = {}
        # Previews que chegaram de fato à grade nesta geração, e a última
        # consulta disparada — o mesmo par do logo_picker, pelas mesmas razões:
        # "loading" eterno quando nada materializa, e a busca dupla que o
        # search-changed atrasado do set_text programático dispara.
        self._added = 0
        self._last_query: Optional[str] = None
        self._temp_dir = Path(tempfile.mkdtemp(prefix="jolven_sgdb_"))

        self.animated_button.set_active(shared.schema.get_boolean("sgdb-animated"))
        self.search_entry.set_text(clean_game_name(name))

        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("activate", lambda *_: self.search())
        self.animated_button.connect("toggled", lambda *_: self.search())
        self.flowbox.connect("child-activated", self._on_child_activated)
        self.connect("closed", self._on_closed)

        self.search()

    # region Search

    def _on_search_changed(self, *_args: Any) -> None:
        if self._debounce_id:
            GLib.source_remove(self._debounce_id)
        self._debounce_id = GLib.timeout_add(500, self._debounce_fire)

    def _debounce_fire(self) -> bool:
        self._debounce_id = 0
        if self.search_entry.get_text().strip() == self._last_query:
            return False
        self.search()
        return False

    def search(self) -> None:
        self._generation += 1
        generation = self._generation
        self._clear_results()

        query = self.search_entry.get_text().strip()
        self._last_query = query
        if not query:
            self._show_empty(_("Digite o nome de um jogo"))
            return
        if not shared.schema.get_string("sgdb-key"):
            self._show_empty(
                _("Chave da API não configurada"),
                _("Configure a chave da API do SteamGridDB nas Preferências."),
            )
            return

        self.stack.set_visible_child_name("loading")
        animated = self.animated_button.get_active()
        threading.Thread(
            target=self._search_thread,
            args=(query, animated, generation),
            daemon=True,
        ).start()

    def _search_thread(self, query: str, animated: bool, generation: int) -> None:
        try:
            games = self.sgdb.search_games(query)
            # .get, não [ ]: um item sem "id" matava a thread com KeyError
            # fora dos excepts e o diálogo ficava no spinner para sempre.
            first_id = (
                games[0].get("id") if games and isinstance(games[0], dict) else None
            )
            grids = (
                self.sgdb.get_grids(first_id, animated, dimensions=PORTRAIT_DIMENSIONS)
                if first_id is not None
                else []
            )
            logging.info("SGDB picker: %d grids (animated=%s)", len(grids), animated)
        except SgdbAuthError:
            entregar_na_tela(
                self._show_empty,
                _("Chave da API inválida"),
                _("Verifique a chave da API do SteamGridDB nas Preferências."),
                generation,
            )
            return
        except (SgdbError, requests.RequestException) as error:
            logging.warning("SGDB picker search failed: %s", error)
            entregar_na_tela(
                self._show_empty,
                _("Não foi possível concluir a busca"),
                _("Verifique a conexão e tente novamente."),
                generation,
            )
            return

        if not grids:
            entregar_na_tela(
                self._show_empty,
                _("Nenhuma capa encontrada"),
                _("Tente buscar por outro nome."),
                generation,
            )
            return

        # Animated previews download the full image, so keep the count modest
        limit = 12 if animated else MAX_RESULTS
        for grid in grids[:limit]:
            if generation != self._generation:
                return
            full_url = grid.get("url")
            if not full_url:
                continue
            # SGDB serves animated thumbnails as .webm videos, which GdkPixbuf
            # cannot decode. So preview animated grids from the full (animated
            # image) URL; still grids keep using the lightweight thumbnail.
            preview_url = full_url if animated else (grid.get("thumb") or full_url)
            try:
                content = download_bytes(preview_url, timeout=15)
            except requests.RequestException as error:
                logging.warning("SGDB picker: preview download failed (%s)", error)
                continue
            suffix = Path(urlparse(preview_url).path).suffix or ".png"
            preview_path = self._temp_dir / f"{grid.get('id', id(grid))}{suffix}"
            try:
                preview_path.write_bytes(content)
            except OSError:
                continue
            entregar_na_tela(
                self._add_result, preview_path, full_url, animated, generation
            )

        # Depois de todos os _add_result (idles de mesma prioridade rodam em
        # ordem): sem isto, todo download falhando deixava o diálogo em
        # "loading" pela vida inteira.
        entregar_na_tela(self._finish_results, generation)

    def _finish_results(self, generation: int) -> bool:
        if generation != self._generation or self._closed:
            return False
        if not self._added:
            self._show_empty(
                _("Não foi possível carregar as pré-visualizações"),
                _("Tente novamente."),
            )
        return False

    # endregion
    # region Results

    def _add_result(
        self, preview_path: Path, full_url: str, animated: bool, generation: int
    ) -> bool:
        if generation != self._generation or self._closed:
            return False

        picture = Gtk.Picture(
            width_request=140,
            height_request=210,
            content_fit=Gtk.ContentFit.COVER,
            overflow=Gtk.Overflow.HIDDEN,
        )
        picture.add_css_class("card")

        cover = GameCover({picture}, preview_path)
        if animated:
            cover.set_details_animation(True)
        self._covers.append(cover)

        self.flowbox.append(picture)
        if child := picture.get_parent():
            child.set_focusable(True)
            self._results[child] = full_url
        # Só com o preview de fato na grade, para "results" nunca mostrar uma
        # grade em branco.
        self._added += 1
        self.stack.set_visible_child_name("results")
        return False

    def _clear_results(self) -> None:
        for cover in self._covers:
            cover.set_hover_animation(False)
            cover.set_details_animation(False)
        self._covers.clear()
        self._results.clear()
        self._added = 0
        self.flowbox.remove_all()

    def _show_empty(
        self, titulo: str, descricao: str = "", generation: Optional[int] = None
    ) -> bool:
        if generation is not None and generation != self._generation:
            return False
        # Título e descrição juntos: com o título fixo, uma busca que falhou
        # dizia "Nenhum … encontrado".
        self.status_page.set_title(titulo)
        self.status_page.set_description(descricao)
        self.stack.set_visible_child_name("empty")
        return False

    # endregion
    # region Selection

    def _on_child_activated(
        self, _flowbox: Gtk.FlowBox, child: Gtk.FlowBoxChild
    ) -> None:
        if not (full_url := self._results.get(child)):
            return
        # Encerra a busca que ainda baixa prévias: cada prévia que chegasse
        # depois trazia a grade de volta por cima do carregamento, e parecia
        # que o clique tinha buscado tudo de novo.
        self._generation += 1
        self.stack.set_visible_child_name("loading")
        threading.Thread(
            target=self._select_thread, args=(full_url,), daemon=True
        ).start()

    def _select_thread(self, full_url: str) -> None:
        try:
            content = download_bytes(full_url, timeout=15)
        except requests.RequestException as error:
            logging.warning("Could not download chosen cover: %s", error)
            entregar_na_tela(
                self._show_empty,
                _("Não foi possível baixar a capa"),
                _("Tente novamente."),
            )
            return

        suffix = Path(urlparse(full_url).path).suffix or ".png"
        raw_path = self._temp_dir / f"selected{suffix}"
        try:
            raw_path.write_bytes(content)
            new_path = convert_cover(raw_path)
        except OSError as error:
            logging.warning("Could not process chosen cover: %s", error)
            new_path = None

        entregar_na_tela(self._select_done, new_path)

    def _select_done(self, new_path: Optional[Path]) -> bool:
        if new_path and not self._closed:
            self.on_selected(new_path)
            self.close()
        else:
            self.stack.set_visible_child_name("results")
        return False

    # endregion

    def _on_closed(self, *_args: Any) -> None:
        self._closed = True
        # A debounce still pending would fire up to half a second after the
        # dialog is gone, run `search()` against its disposed widgets and start
        # a network thread nobody is waiting for. The generation bump below does
        # not help: it only invalidates results, not the search itself.
        if self._debounce_id:
            GLib.source_remove(self._debounce_id)
            self._debounce_id = 0
        self._generation += 1
        self._clear_results()
        shutil.rmtree(self._temp_dir, ignore_errors=True)
