# logo_picker.py
#
# Copyright 2026 joaomgabaldi
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

"""A visual picker for the details page logo.

The automatic lookup ranks logos by style, transparency and community score,
which is the best guess that can be made from what SteamGridDB publishes — but
it cannot read them. A game whose top-scoring upload is a Japanese or Chinese
wordmark comes out with a header nobody asked for, and no amount of ranking
fixes that, because the API does not say what language an asset is in.

So the ranking stays a default and this dialog is the override: every logo the
site has for a game, shown legibly, and the user picks. The choice is then
locked, and the automatic lookup leaves that game alone for good.
"""

import logging
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import requests
from gi.repository import Adw, GdkPixbuf, Gio, GLib, Gtk

from cartridges import shared
from cartridges.game_cover import texture_from_pixbuf
from cartridges.utils.download import download_bytes
from cartridges.utils.game_logo import IMAGE_SUFFIXES, pick_logo
from cartridges.utils.na_tela import entregar_na_tela
from cartridges.utils.name_cleaner import clean_game_name
from cartridges.utils.steamgriddb import SgdbAuthError, SgdbError, SgdbHelper

# Enough to cover even a heavily uploaded game without turning the dialog into
# an endless download queue.
MAX_RESULTS = 24

# Preview cell, in logical pixels. Wide and short, like the header the logo is
# being chosen for, so what the dialog shows is close to what the page will.
PREVIEW_WIDTH = 300
PREVIEW_HEIGHT = 110


@Gtk.Template(resource_path=shared.PREFIX + "/gtk/logo-picker.ui")
class LogoPicker(Adw.Dialog):
    __gtype_name__ = "LogoPicker"

    header_bar: Adw.HeaderBar = Gtk.Template.Child()
    title_button: Gtk.Button = Gtk.Template.Child()
    search_bar: Gtk.SearchBar = Gtk.Template.Child()
    search_entry: Gtk.SearchEntry = Gtk.Template.Child()
    stack: Gtk.Stack = Gtk.Template.Child()
    status_page: Adw.StatusPage = Gtk.Template.Child()
    flowbox: Gtk.FlowBox = Gtk.Template.Child()

    def __init__(
        self,
        name: str,
        on_selected: Callable[[Path], None],
        on_cleared: Callable[[], None],
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)

        self.on_selected = on_selected
        self.on_cleared = on_cleared
        self.sgdb = SgdbHelper()

        # Same generation guard as the cover picker: a search in flight when a
        # new one starts (or when the dialog closes) must not add its results.
        self._generation = 0
        self._debounce_id = 0
        self._closed = False
        self._results: dict[Gtk.FlowBoxChild, str] = {}
        # Pré-visualizações que chegaram de fato à grade nesta geração; é o que
        # separa "resultados na tela" de "todo download/decode falhou".
        self._added = 0
        # Última consulta disparada, para o debounce não repetir a busca que o
        # set_text programático do __init__ emite com atraso.
        self._last_query: Optional[str] = None
        self._temp_dir = Path(tempfile.mkdtemp(prefix="jolven_logo_"))

        self.search_entry.set_text(clean_game_name(name))

        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("activate", lambda *_: self.search())
        self.flowbox.connect("child-activated", self._on_child_activated)
        self.title_button.connect("clicked", self._on_title_clicked)
        self.connect("closed", self._on_closed)

        self.search()

    # region Search

    def _on_search_changed(self, *_args: Any) -> None:
        if self._debounce_id:
            GLib.source_remove(self._debounce_id)
        self._debounce_id = GLib.timeout_add(500, self._debounce_fire)

    def _debounce_fire(self) -> bool:
        self._debounce_id = 0
        # GtkSearchEntry emite um search-changed atrasado para o set_text
        # programático do __init__, depois do connect: sem este guard, abrir o
        # diálogo buscava duas vezes a mesma coisa — API e downloads em dobro,
        # com a segunda passada varrendo os previews da primeira. Enter no
        # campo continua repetindo a busca (caminho do "activate", não daqui).
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
        threading.Thread(
            target=self._search_thread, args=(query, generation), daemon=True
        ).start()

    def _search_thread(self, query: str, generation: int) -> None:
        try:
            from cartridges.utils.name_cleaner import search_variants

            queries = search_variants(query) or [query]
            logos: list = []
            for variant in queries:
                games = self.sgdb.search_games(variant)
                # .get, não [ ]: um item sem "id" (200 de shape inesperado) matava
                # a thread com KeyError fora dos excepts e o spinner ficava eterno.
                first_id = (
                    games[0].get("id") if games and isinstance(games[0], dict) else None
                )
                if first_id is None:
                    continue
                logos = self.sgdb.get_logos(first_id)
                if logos:
                    break
        except SgdbAuthError:
            entregar_na_tela(
                self._show_empty,
                _("Chave da API inválida"),
                _("Verifique a chave da API do SteamGridDB nas Preferências."),
                generation,
            )
            return
        except (SgdbError, requests.RequestException) as error:
            logging.warning("Logo picker search failed: %s", error)
            entregar_na_tela(
                self._show_empty,
                _("Não foi possível concluir a busca"),
                _("Verifique a conexão e tente novamente."),
                generation,
            )
            return

        # Same order the automatic lookup would have used, so the logo the app
        # would have chosen on its own is the first one offered here. The list
        # is not filtered down to it, though — the whole point of the dialog is
        # to see the alternatives it passed over.
        ordered = self._ordered(logos)
        if not ordered:
            entregar_na_tela(
                self._show_empty,
                _("Nenhum logo encontrado"),
                _("Tente buscar por outro nome."),
                generation,
            )
            return

        for logo in ordered[:MAX_RESULTS]:
            if generation != self._generation:
                return
            full_url = str(logo.get("url") or "")
            if not full_url:
                continue
            preview_url = str(logo.get("thumb") or full_url)
            try:
                content = download_bytes(preview_url, timeout=15)
            except requests.RequestException as error:
                logging.warning("Logo picker: preview download failed (%s)", error)
                continue
            suffix = Path(urlparse(preview_url).path).suffix or ".png"
            preview_path = self._temp_dir / f"{logo.get('id', id(logo))}{suffix}"
            try:
                preview_path.write_bytes(content)
            except OSError:
                continue
            entregar_na_tela(self._add_result, preview_path, full_url, generation)

        # Depois de todos os _add_result (idles de mesma prioridade rodam em
        # ordem): se nenhum preview chegou à grade — todo download falhou, ou
        # todo decode falhou —, o loop acabava sem chamar nada e o diálogo
        # ficava em "loading" para sempre.
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

    def _ordered(self, logos: list[dict]) -> list[dict]:
        """Every usable logo, best first, judged the way the page judges them."""
        ordered = []
        remaining = list(logos)
        while remaining:
            best = pick_logo(remaining)
            if not best:
                break
            ordered.append(best)
            remaining.remove(best)
        return ordered

    # endregion
    # region Results

    def _add_result(self, preview_path: Path, full_url: str, generation: int) -> bool:
        if generation != self._generation or self._closed:
            return False

        picture = Gtk.Picture(
            content_fit=Gtk.ContentFit.SCALE_DOWN,
            hexpand=True,
            vexpand=True,
        )
        try:
            picture.set_paintable(
                texture_from_pixbuf(
                    GdkPixbuf.Pixbuf.new_from_file_at_scale(
                        str(preview_path),
                        PREVIEW_WIDTH * max(1, int(shared.scale_factor)),
                        PREVIEW_HEIGHT * max(1, int(shared.scale_factor)),
                        True,
                    )
                )
            )
        except GLib.Error:
            return False

        # Most logos are white or light artwork on transparency, which is
        # invisible against a light theme. The tile behind them is not
        # decoration: it is the only way the previews can be compared, and it
        # also stands in for the dark details page they are headed for.
        tile = Gtk.Box(
            width_request=PREVIEW_WIDTH,
            height_request=PREVIEW_HEIGHT,
            halign=Gtk.Align.FILL,
            valign=Gtk.Align.CENTER,
        )
        tile.add_css_class("logo-preview")
        tile.append(picture)

        self.flowbox.append(tile)
        if child := tile.get_parent():
            child.set_focusable(True)
            self._results[child] = full_url
        # Só agora, com o preview de fato na grade: trocar para "results" antes
        # do decode deixava uma grade em branco quando todos falhavam.
        self._added += 1
        self.stack.set_visible_child_name("results")
        return False

    def _clear_results(self) -> None:
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

    def _on_title_clicked(self, *_args: Any) -> None:
        self.on_cleared()
        self.close()

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
            logging.warning("Could not download the chosen logo: %s", error)
            entregar_na_tela(
                self._show_empty,
                _("Não foi possível baixar o logo"),
                _("Tente novamente."),
            )
            return

        suffix = Path(urlparse(full_url).path).suffix.lower()
        if suffix not in IMAGE_SUFFIXES:
            suffix = ".png"

        # Handed over outside this dialog's temp dir, which is deleted on close:
        # the caller holds the file until the edit is applied (or discarded).
        try:
            template = f"jolven_logo_XXXXXX{suffix}"
            arquivo, fluxo = Gio.File.new_tmp(template)
            # Fechado antes da escrita: aberto, o fluxo que `new_tmp` devolve
            # segura o arquivo até o coletor de lixo passar.
            fluxo.close()
            path = Path(arquivo.get_path())
            path.write_bytes(content)
        except (GLib.Error, OSError) as error:
            logging.warning("Could not store the chosen logo: %s", error)
            entregar_na_tela(
                self._show_empty,
                _("Não foi possível baixar o logo"),
                _("Tente novamente."),
            )
            return

        entregar_na_tela(self._select_done, path)

    def _select_done(self, path: Path) -> bool:
        if not self._closed:
            self.on_selected(path)
            self.close()
        else:
            # Ninguém mais vai recebê-lo, e então ninguém mais o apagaria.
            path.unlink(missing_ok=True)
        return False

    # endregion

    def _on_closed(self, *_args: Any) -> None:
        self._closed = True
        # A pending debounce would otherwise fire after the dialog is gone and
        # start a network thread nobody is waiting for.
        if self._debounce_id:
            GLib.source_remove(self._debounce_id)
            self._debounce_id = 0
        self._generation += 1
        self._clear_results()
        # The previews are scratch: one file per candidate, per search, per
        # time the dialog is opened. Nothing else ever removes them, so without
        # this the directory `mkdtemp` created stays in %TEMP% for good — and
        # `_select_thread` hands the chosen logo out through a *separate* temp
        # precisely because it expects this one to be gone.
        shutil.rmtree(self._temp_dir, ignore_errors=True)
