# image_picker.py
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

"""A escolha manual de imagens em cinco fontes.

Um diálogo só para capas e fundos da biblioteca, com as fontes lado a lado:
SteamGridDB, IGDB, TheGamesDB, Wallhaven (com a régua de filtros completa,
a mesma da tela de papel de parede) e arquivo local. O modo decide o formato
— ``capa`` (retrato) ou ``fundo`` (paisagem) — e cada fonte entrega o que tem
de melhor naquele formato: grids ou heroes no SGDB, covers ou screenshots no
IGDB, boxart ou screenshots/fanarts no TheGamesDB.

Segue o protocolo das outras telas de escolha: busca com debounce fora da UI,
guarda de geração, prévias numa grade e o arquivo final entregue fora da pasta
temporária do diálogo (apagada ao fechar), para quem chamou segurar até o
Aplicar — ou descartar.
"""

import logging
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import requests
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from cartridges import shared
from cartridges.game_cover import GameCover
from cartridges.utils.download import download_bytes
from cartridges.utils.na_tela import entregar_na_tela
from cartridges.utils.name_cleaner import clean_game_name
from cartridges.utils.save_cover import convert_cover
from cartridges.utils.steamgriddb import SgdbAuthError, SgdbError, SgdbHelper
from cartridges.utils.wallhaven import Filtros, WallhavenError, buscar
from cartridges.wallhaven_filter_bar import WallhavenFilterBar

MAX_RESULTS = 24

# Capas em retrato no SteamGridDB. Sem este filtro a API também devolve
# cápsulas horizontais; restringir a 600x900 sozinho derruba a maioria das
# animadas, que usam os outros tamanhos de retrato.
PORTRAIT_DIMENSIONS = "600x900,342x482,660x930"

# Heroes em paisagem: o formato largo que o fundo da biblioteca quer.
HERO_DIMENSIONS = "1920x620,3840x1240"

# O mínimo que cada formato precisa ter para ninguém ser esticado.
CAPA_MINIMA = (600, 900)
FUNDO_MINIMO = (1280, 720)

FONTES = ("sgdb", "igdb", "tgdb", "wallhaven", "file")


@Gtk.Template(resource_path=shared.PREFIX + "/gtk/image-picker.ui")
class ImagePicker(Adw.Dialog):
    __gtype_name__ = "ImagePicker"

    header_bar: Adw.HeaderBar = Gtk.Template.Child()
    animated_button: Gtk.ToggleButton = Gtk.Template.Child()
    search_bar: Gtk.SearchBar = Gtk.Template.Child()
    search_entry: Gtk.SearchEntry = Gtk.Template.Child()
    source_sgdb: Gtk.ToggleButton = Gtk.Template.Child()
    source_igdb: Gtk.ToggleButton = Gtk.Template.Child()
    source_tgdb: Gtk.ToggleButton = Gtk.Template.Child()
    source_wallhaven: Gtk.ToggleButton = Gtk.Template.Child()
    source_file: Gtk.ToggleButton = Gtk.Template.Child()
    filter_box: Gtk.Box = Gtk.Template.Child()
    stack: Gtk.Stack = Gtk.Template.Child()
    status_page: Adw.StatusPage = Gtk.Template.Child()
    matched_label: Gtk.Label = Gtk.Template.Child()
    flowbox: Gtk.FlowBox = Gtk.Template.Child()
    file_button: Gtk.Button = Gtk.Template.Child()

    def __init__(
        self,
        name: str,
        on_selected: Callable[[Path], None],
        mode: str = "capa",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)

        self.on_selected = on_selected
        self.mode = mode if mode in ("capa", "fundo") else "capa"
        self.e_capa = self.mode == "capa"
        self.set_title(_("Escolher capa") if self.e_capa else _("Escolher fundo"))

        self.sgdb = SgdbHelper()
        self._generation = 0
        self._debounce_id = 0
        self._closed = False
        self._covers: list[GameCover] = []
        self._results: dict[Gtk.FlowBoxChild, str] = {}
        self._added = 0
        self._last_query: Optional[tuple] = None
        self._temp_dir = Path(tempfile.mkdtemp(prefix="jolven_image_"))

        self._barra = WallhavenFilterBar(self._on_filtros)
        self._filtros_atuais = self._barra.filtros
        self.filter_box.append(self._barra)

        self._botoes_fonte = {
            "sgdb": self.source_sgdb,
            "igdb": self.source_igdb,
            "tgdb": self.source_tgdb,
            "wallhaven": self.source_wallhaven,
            "file": self.source_file,
        }
        for fonte, botao in self._botoes_fonte.items():
            botao.connect("toggled", self._on_fonte, fonte)
        self._troca_fonte = False
        self._fonte = "sgdb"

        self.search_entry.set_text(clean_game_name(name))
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("activate", lambda *_: self.search())
        self.animated_button.connect("toggled", lambda *_: self.search())
        self.animated_button.set_active(shared.schema.get_boolean("sgdb-animated"))
        self.flowbox.connect("child-activated", self._on_child_activated)
        self.file_button.connect("clicked", self._on_file_clicked)
        self.connect("closed", self._on_closed)

        self._atualizar_visibilidade()
        self.search()

    # region Fontes

    def _on_fonte(self, botao: Gtk.ToggleButton, fonte: str) -> None:
        # Exclusividade manual: ToggleButton não tem grupo como o CheckButton.
        if self._troca_fonte:
            return
        if not botao.get_active():
            if fonte == self._fonte:
                self._troca_fonte = True
                try:
                    botao.set_active(True)
                finally:
                    self._troca_fonte = False
            return
        if fonte == self._fonte:
            return
        self._troca_fonte = True
        try:
            for outra, outro in self._botoes_fonte.items():
                if outra != fonte:
                    outro.set_active(False)
        finally:
            self._troca_fonte = False
        self._fonte = fonte
        self._atualizar_visibilidade()
        self.search()

    def _on_filtros(self, novos: Filtros) -> None:
        self._filtros_atuais = novos
        if self._fonte == "wallhaven":
            self.search()

    def _atualizar_visibilidade(self) -> None:
        na_busca = self._fonte != "file"
        self.search_bar.set_visible(na_busca)
        # A régua do wallhaven só aparece na fonte dela; nas outras, os
        # filtros guardados continuam valendo para a próxima visita.
        self.filter_box.set_visible(self._fonte == "wallhaven")
        # Animadas só fazem sentido para capas do SGDB.
        self.animated_button.set_visible(self.e_capa and self._fonte == "sgdb")
        if not na_busca:
            self.stack.set_visible_child_name("file")

    # endregion
    # region Search

    def _on_search_changed(self, *_args: Any) -> None:
        if self._debounce_id:
            GLib.source_remove(self._debounce_id)
        self._debounce_id = GLib.timeout_add(500, self._debounce_fire)

    def _debounce_fire(self) -> bool:
        self._debounce_id = 0
        chave = (self._fonte, self.search_entry.get_text().strip())
        if chave == self._last_query:
            return False
        self.search()
        return False

    def search(self) -> None:
        if self._fonte == "file":
            self.stack.set_visible_child_name("file")
            return
        self._generation += 1
        generation = self._generation
        self._clear_results()

        query = self.search_entry.get_text().strip()
        self._last_query = (self._fonte, query)
        if not query:
            self._show_empty(_("Digite o nome de um jogo"))
            return

        self.stack.set_visible_child_name("loading")
        threading.Thread(
            target=self._search_thread, args=(query, self._fonte, generation), daemon=True
        ).start()

    def _search_thread(self, query: str, fonte: str, generation: int) -> None:
        if fonte == "sgdb":
            self._buscar_sgdb(query, generation)
        elif fonte == "igdb":
            self._buscar_igdb(query, generation)
        elif fonte == "tgdb":
            self._buscar_tgdb(query, generation)
        else:
            self._buscar_wallhaven(query, generation)

    def _sgdb_id(self, query: str) -> Optional[str]:
        from cartridges.utils.name_cleaner import search_variants

        for variant in search_variants(query) or [query]:
            jogos = self.sgdb.search_games(variant)
            first_id = (
                jogos[0].get("id") if jogos and isinstance(jogos[0], dict) else None
            )
            if first_id is not None:
                return str(first_id)
        return None

    def _buscar_sgdb(self, query: str, generation: int) -> None:
        if not shared.schema.get_string("sgdb-key"):
            entregar_na_tela(
                self._show_empty,
                _("Chave da API não configurada"),
                _("Configure a chave da API do SteamGridDB nas Preferências."),
                generation,
            )
            return
        try:
            jogo_id = self._sgdb_id(query)
            if jogo_id is None:
                itens = []
            elif self.e_capa:
                animadas = self.animated_button.get_active()
                itens = self.sgdb.get_grids(
                    jogo_id, animadas, dimensions=PORTRAIT_DIMENSIONS
                )
            else:
                itens = self.sgdb.get_heroes(jogo_id, dimensions=HERO_DIMENSIONS)
                if not itens:
                    itens = self.sgdb.get_heroes(jogo_id)
            logging.info("Image picker (SGDB): %d itens", len(itens))
        except SgdbAuthError:
            entregar_na_tela(
                self._show_empty,
                _("Chave da API inválida"),
                _("Verifique a chave da API do SteamGridDB nas Preferências."),
                generation,
            )
            return
        except (SgdbError, requests.RequestException) as error:
            logging.warning("Image picker (SGDB) falhou: %s", error)
            entregar_na_tela(
                self._show_empty,
                _("Não foi possível concluir a busca"),
                _("Verifique a conexão e tente novamente."),
                generation,
            )
            return

        if not itens:
            entregar_na_tela(
                self._show_empty,
                _("Nenhuma imagem encontrada"),
                _("Tente buscar por outro nome."),
                generation,
            )
            return

        animado = self.e_capa and self.animated_button.get_active()
        limite = 12 if animado else MAX_RESULTS
        for item in itens[:limite]:
            if generation != self._generation:
                return
            full_url = item.get("url")
            if not full_url:
                continue
            preview_url = full_url if animado else (item.get("thumb") or full_url)
            try:
                content = download_bytes(preview_url, timeout=15)
            except requests.RequestException as error:
                logging.warning("Image picker (SGDB): prévia falhou (%s)", error)
                continue
            suffix = Path(urlparse(preview_url).path).suffix or ".png"
            preview_path = self._temp_dir / f"{item.get('id', id(item))}{suffix}"
            try:
                preview_path.write_bytes(content)
            except OSError:
                continue
            entregar_na_tela(
                self._add_result_arquivo, preview_path, full_url, animado, generation
            )
        entregar_na_tela(self._finish_results, generation)

    def _buscar_igdb(self, query: str, generation: int) -> None:
        from cartridges.utils.igdb import (
            IGDBAuthError,
            IGDBClient,
            IGDBError,
            IGDBNotFound,
        )

        try:
            cliente = IGDBClient()
            jogo_id, nome = cliente.resolver_jogo(query)
            urls = cliente.capas(jogo_id) if self.e_capa else cliente.capturas(jogo_id)
        except IGDBAuthError:
            entregar_na_tela(
                self._show_empty,
                _("Chave da API não configurada"),
                _("Configure o Client ID e o Secret do IGDB nas Preferências."),
                generation,
            )
            return
        except (IGDBNotFound, IGDBError, requests.RequestException) as error:
            logging.info("Image picker (IGDB) falhou (%s): %s", query, error)
            entregar_na_tela(
                self._show_empty,
                _("Nenhuma imagem encontrada"),
                _("Tente buscar por outro nome."),
                generation,
            )
            return

        if not urls:
            entregar_na_tela(
                self._show_empty,
                _("Nenhuma imagem encontrada"),
                _("Tente buscar por outro nome."),
                generation,
            )
            return

        entregar_na_tela(self._show_casado, nome, generation)
        for url in urls[:MAX_RESULTS]:
            if generation != self._generation:
                return
            try:
                content = download_bytes(url, timeout=15)
            except requests.RequestException as error:
                logging.info("Image picker (IGDB): prévia falhou (%s)", error)
                continue
            entregar_na_tela(self._add_result_bytes, content, url, generation)
        entregar_na_tela(self._finish_results, generation)

    def _buscar_tgdb(self, query: str, generation: int) -> None:
        from cartridges.utils.thegamesdb import (
            TheGamesDBAuthError,
            TheGamesDBClient,
            TheGamesDBError,
            TheGamesDBNotFound,
        )

        if not shared.schema.get_string("thegamesdb-key").strip():
            entregar_na_tela(
                self._show_empty,
                _("Chave da API não configurada"),
                _("Configure a chave da API do TheGamesDB nas Preferências."),
                generation,
            )
            return
        try:
            cliente = TheGamesDBClient()
            achado = cliente.find_game(query)
            nome = str(achado.game.get("game_title") or query)
            if self.e_capa:
                urls = [achado.cover_url] if achado.cover_url else []
            else:
                imagens = cliente.game_images(achado.game.get("id"))
                urls = (imagens.get("screenshot") or []) + (
                    imagens.get("fanart") or []
                )
        except TheGamesDBAuthError:
            entregar_na_tela(
                self._show_empty,
                _("Chave da API inválida"),
                _("Verifique a chave da API do TheGamesDB nas Preferências."),
                generation,
            )
            return
        except (TheGamesDBNotFound, TheGamesDBError, requests.RequestException) as error:
            logging.info("Image picker (TGDB) falhou (%s): %s", query, error)
            entregar_na_tela(
                self._show_empty,
                _("Nenhuma imagem encontrada"),
                _("Tente buscar por outro nome."),
                generation,
            )
            return

        if not urls:
            entregar_na_tela(
                self._show_empty,
                _("Nenhuma imagem encontrada"),
                _("Tente buscar por outro nome."),
                generation,
            )
            return

        entregar_na_tela(self._show_casado, nome, generation)
        for url in urls[:MAX_RESULTS]:
            if generation != self._generation:
                return
            try:
                content = download_bytes(str(url), timeout=15)
            except requests.RequestException as error:
                logging.info("Image picker (TGDB): prévia falhou (%s)", error)
                continue
            entregar_na_tela(self._add_result_bytes, content, str(url), generation)
        entregar_na_tela(self._finish_results, generation)

    def _buscar_wallhaven(self, query: str, generation: int) -> None:
        from cartridges.utils.name_cleaner import search_variants

        filtros = self._filtros_atuais
        formato = "portrait" if self.e_capa else "landscape"
        minimo = CAPA_MINIMA if self.e_capa else FUNDO_MINIMO
        try:
            achados: list = []
            for variant in search_variants(query) or [query]:
                achados = buscar(variant, *minimo, formato=formato, filtros=filtros)
                vistos = {item["id"] for item in achados}
                achados += [
                    item
                    for item in buscar(variant, *minimo, filtros=filtros)
                    if item["id"] not in vistos
                ]
                if achados:
                    break
        except WallhavenError as error:
            logging.warning("Image picker (wallhaven) falhou: %s", error)
            entregar_na_tela(
                self._show_empty,
                _("Não foi possível concluir a busca"),
                _("Verifique a conexão e tente novamente."),
                generation,
            )
            return

        if not achados:
            entregar_na_tela(
                self._show_empty,
                _("Nenhuma imagem encontrada"),
                _("Tente buscar por outro nome."),
                generation,
            )
            return

        for item in achados[:MAX_RESULTS]:
            if generation != self._generation:
                return
            try:
                content = download_bytes(str(item["thumb"]), timeout=15)
            except requests.RequestException as error:
                logging.info("Image picker (wallhaven): prévia falhou (%s)", error)
                continue
            entregar_na_tela(
                self._add_result_bytes, content, str(item["path"]), generation
            )
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

    def _dimensoes_celula(self) -> tuple[int, int]:
        return (140, 210) if self.e_capa else (260, 146)

    def _add_result_arquivo(
        self, preview_path: Path, full_url: str, animado: bool, generation: int
    ) -> bool:
        if generation != self._generation or self._closed:
            return False

        largura, altura = self._dimensoes_celula()
        picture = Gtk.Picture(
            width_request=largura,
            height_request=altura,
            content_fit=Gtk.ContentFit.COVER,
            overflow=Gtk.Overflow.HIDDEN,
        )
        picture.add_css_class("card")

        cover = GameCover({picture}, preview_path)
        if animado:
            cover.set_details_animation(True)
        self._covers.append(cover)

        self.flowbox.append(picture)
        if child := picture.get_parent():
            child.set_focusable(True)
            self._results[child] = full_url
        self._added += 1
        self.stack.set_visible_child_name("results")
        return False

    def _add_result_bytes(
        self, content: bytes, full_url: str, generation: int
    ) -> bool:
        if generation != self._generation or self._closed:
            return False

        try:
            paintable = Gdk.Texture.new_from_bytes(GLib.Bytes.new(content))
        except GLib.Error:
            return False

        largura, altura = self._dimensoes_celula()
        picture = Gtk.Picture(
            paintable=paintable,
            content_fit=Gtk.ContentFit.COVER,
            width_request=largura,
            height_request=altura,
            overflow=Gtk.Overflow.HIDDEN,
        )
        picture.add_css_class("card")

        self.flowbox.append(picture)
        if child := picture.get_parent():
            child.set_focusable(True)
            self._results[child] = full_url
        self._added += 1
        self.stack.set_visible_child_name("results")
        return False

    def _show_casado(self, nome: str, generation: int) -> bool:
        if generation != self._generation or self._closed:
            return False
        self.matched_label.set_label(_("Mostrando imagens de: {}").format(nome))
        self.matched_label.set_visible(True)
        return False

    def _clear_results(self) -> None:
        for cover in self._covers:
            cover.set_hover_animation(False)
            cover.set_details_animation(False)
        self._covers.clear()
        self._results.clear()
        self._added = 0
        self.matched_label.set_visible(False)
        self.flowbox.remove_all()

    def _show_empty(
        self, titulo: str, descricao: str = "", generation: Optional[int] = None
    ) -> bool:
        if generation is not None and generation != self._generation:
            return False
        self.status_page.set_title(titulo)
        self.status_page.set_description(descricao)
        self.stack.set_visible_child_name("empty")
        return False

    # endregion
    # region Selection

    def _on_child_activated(
        self, _flowbox: Gtk.FlowBox, child: Gtk.FlowBoxChild
    ) -> bool:
        if not (full_url := self._results.get(child)):
            return False
        self._generation += 1
        self.stack.set_visible_child_name("loading")
        threading.Thread(
            target=self._select_thread, args=(full_url,), daemon=True
        ).start()
        return False

    def _select_thread(self, full_url: str) -> None:
        try:
            content = download_bytes(full_url, timeout=30)
        except requests.RequestException as error:
            logging.warning("Image picker: download falhou (%s)", error)
            entregar_na_tela(
                self._show_empty,
                _("Não foi possível baixar a imagem"),
                _("Tente novamente."),
            )
            return

        suffix = Path(urlparse(full_url).path).suffix.lower() or ".jpg"
        try:
            if self.e_capa:
                bruto = self._temp_dir / f"bruta{suffix}"
                bruto.write_bytes(content)
                caminho = convert_cover(str(bruto))
                if caminho is None:
                    raise OSError("convert_cover devolveu None")
                caminho = Path(caminho)
            else:
                modelo = f"jolven_fundo_XXXXXX{suffix}"
                arquivo, fluxo = Gio.File.new_tmp(modelo)
                fluxo.close()
                caminho = Path(arquivo.get_path())
                caminho.write_bytes(content)
        except (GLib.Error, OSError) as error:
            logging.warning("Image picker: escolha não pôde ser guardada (%s)", error)
            entregar_na_tela(
                self._show_empty,
                _("Não foi possível baixar a imagem"),
                _("Tente novamente."),
            )
            return

        entregar_na_tela(self._select_done, caminho)

    def _select_done(self, caminho: Path) -> bool:
        if not self._closed:
            self.on_selected(caminho)
            self.close()
        return False

    def _on_file_clicked(self, *_args: Any) -> None:
        dialogo = Gtk.FileDialog()
        filtro = Gtk.FileFilter(name=_("Images"))
        for sufixo in ("jpg", "jpeg", "png", "webp", "gif", "bmp", "tiff", "svg"):
            filtro.add_suffix(sufixo)
        filtros = Gio.ListStore.new(Gtk.FileFilter)
        filtros.append(filtro)
        dialogo.set_filters(filtros)
        dialogo.set_default_filter(filtro)
        dialogo.open(self.get_root(), None, self._on_file_chosen)

    def _on_file_chosen(self, dialogo: Gtk.FileDialog, result: Gio.Task) -> None:
        try:
            caminho = Path(dialogo.open_finish(result).get_path())
        except GLib.Error:
            return
        if self._closed:
            return
        self.on_selected(caminho)
        self.close()

    # endregion

    def _on_closed(self, *_args: Any) -> None:
        self._closed = True
        if self._debounce_id:
            GLib.source_remove(self._debounce_id)
            self._debounce_id = 0
        self._generation += 1
        self._clear_results()
        shutil.rmtree(self._temp_dir, ignore_errors=True)
