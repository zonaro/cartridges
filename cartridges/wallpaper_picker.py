# wallpaper_picker.py
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

"""A escolha à mão do papel de parede da sessão.

A busca automática acerta o tema e erra o enquadramento: medido numa página de
resultados de verdade, um em cada doze cortes para um monitor em pé parte
alguém ao meio. Nenhuma ordenação conserta isso — o site não diz onde está o
assunto da imagem —, então esta tela é a régua: a grade mostra os candidatos
JÁ CORTADOS, no formato dos monitores, e a barrinha desliza a faixa antes de
gravar.

Toda a fidelidade vem de uma coisa só: a miniatura e o arquivo final passam
pela mesma :func:`~cartridges.utils.session_wallpaper.enquadrar`. Como ela
corta em proporção, o quadro de 146px da grade e o de 1080px do monitor são o
mesmo quadro. O que se vê aqui é o que vai para a parede, não uma ideia dele.
"""

import logging
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, NamedTuple, Optional
from urllib.parse import urlparse

import requests
from gi.repository import Adw, Gdk, Gio, GLib, Gtk
from PIL import Image, UnidentifiedImageError

from cartridges import shared
from cartridges.utils.download import download_bytes
from cartridges.utils.na_tela import entregar_na_tela
from cartridges.utils.name_cleaner import clean_game_name
from cartridges.utils.session_wallpaper import (
    Posicoes,
    corta,
    eixo_do_corte,
    enquadrar,
    formatos_ligados,
    imagem_para_textura_bytes,
)
from cartridges.utils.wallhaven import (
    ALCANCES_TOPLIST,
    CORES,
    IMAGE_SUFFIXES,
    ORDENACOES,
    Filtros,
    WallhavenError,
    buscar,
    gerar_seed,
    ler_filtros,
    salvar_filtros,
    tem_chave,
)

# Uma página do site. A grade de quatro colunas mostra seis linhas com isso.
MAX_RESULTS = 24

# O lado fixo da célula da grade, em pixels lógicos: a altura numa grade em pé, a
# largura numa deitada. O outro lado sai da proporção do monitor, e a célula é a
# tela em miniatura — 146x260 num 9:16, 260x146 num 16:9, três deitadas ou
# quatro em pé por linha na janela cheia.
CELL_SIZE = 260


def celula(largura: int, altura: int) -> tuple[int, int]:
    """A célula da grade para um monitor ``largura`` x ``altura``."""
    if altura > largura:
        return max(1, round(CELL_SIZE * largura / altura)), CELL_SIZE
    return CELL_SIZE, max(1, round(CELL_SIZE * altura / largura))


# A caixa em que a prévia grande cabe, em pixels lógicos. Com uma orientação só
# ela tem a janela inteira; no arranjo misto as duas dividem os 860px de largura,
# e a deitada fica com a maior parte porque é a mais larga.
PREVIEW_MAX = (800, 440)
HYBRID_LANDSCAPE_MAX = (600, 340)
HYBRID_PORTRAIT_MAX = (220, 340)

# Altura da cópia reduzida que a barrinha recorta: o dobro da maior prévia, para
# ela não sair borrada.
PREVIEW_SOURCE_HEIGHT = 2 * PREVIEW_MAX[1]


def caber(largura: int, altura: int, max_largura: int, max_altura: int) -> tuple[int, int]:
    """``largura`` x ``altura`` reduzido para caber na caixa, na mesma proporção."""
    escala = min(max_largura / largura, max_altura / altura)
    return max(1, round(largura * escala)), max(1, round(altura * escala))


class _Corte(NamedTuple):
    """Os widgets de um dos dois cortes da tela de ajuste."""

    box: Gtk.Box
    picture: Gtk.Picture
    hint: Gtk.Label
    scale: Gtk.Scale
    adjustment: Gtk.Adjustment


@Gtk.Template(resource_path=shared.PREFIX + "/gtk/wallpaper-picker.ui")
class WallpaperPicker(Adw.Dialog):
    __gtype_name__ = "WallpaperPicker"

    header_bar: Adw.HeaderBar = Gtk.Template.Child()
    none_button: Gtk.Button = Gtk.Template.Child()
    search_bar: Gtk.SearchBar = Gtk.Template.Child()
    search_entry: Gtk.SearchEntry = Gtk.Template.Child()
    filter_box: Gtk.Box = Gtk.Template.Child()
    stack: Gtk.Stack = Gtk.Template.Child()
    status_page: Adw.StatusPage = Gtk.Template.Child()
    flowbox: Gtk.FlowBox = Gtk.Template.Child()
    adjust_landscape_box: Gtk.Box = Gtk.Template.Child()
    adjust_landscape_picture: Gtk.Picture = Gtk.Template.Child()
    adjust_landscape_hint: Gtk.Label = Gtk.Template.Child()
    adjust_landscape_scale: Gtk.Scale = Gtk.Template.Child()
    adjust_landscape_adjustment: Gtk.Adjustment = Gtk.Template.Child()
    adjust_portrait_box: Gtk.Box = Gtk.Template.Child()
    adjust_portrait_picture: Gtk.Picture = Gtk.Template.Child()
    adjust_portrait_hint: Gtk.Label = Gtk.Template.Child()
    adjust_portrait_scale: Gtk.Scale = Gtk.Template.Child()
    adjust_portrait_adjustment: Gtk.Adjustment = Gtk.Template.Child()
    adjust_back: Gtk.Button = Gtk.Template.Child()
    adjust_apply: Gtk.Button = Gtk.Template.Child()

    def __init__(
        self,
        name: str,
        on_selected: Callable[[Path, Posicoes], None],
        on_cleared: Callable[[], None],
        arquivo: Optional[Path] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)

        self.on_selected = on_selected
        self.on_cleared = on_cleared
        self._arquivo = arquivo

        self.formatos = formatos_ligados()
        self.largura, self.altura = self.formatos.grade
        self.cell_width, self.cell_height = celula(self.largura, self.altura)

        self._paisagem = _Corte(
            self.adjust_landscape_box,
            self.adjust_landscape_picture,
            self.adjust_landscape_hint,
            self.adjust_landscape_scale,
            self.adjust_landscape_adjustment,
        )
        self._retrato = _Corte(
            self.adjust_portrait_box,
            self.adjust_portrait_picture,
            self.adjust_portrait_hint,
            self.adjust_portrait_scale,
            self.adjust_portrait_adjustment,
        )

        # A mesma guarda de geração das outras duas telas de escolha: uma
        # busca em voo quando outra começa (ou quando a janela fecha) não pode
        # despejar os resultados dela na grade.
        self._generation = 0
        self._debounce_id = 0
        self._closed = False
        self._results: dict[Gtk.FlowBoxChild, dict[str, Any]] = {}
        self._added = 0
        self._last_query: Optional[str] = None
        self._temp_dir = Path(tempfile.mkdtemp(prefix="cartridges_wallpaper_"))

        # O que está aberto na tela de ajuste: o original inteiro (que é o que
        # vai ser gravado) e uma cópia pequena, que é de onde cada arrastada da
        # barrinha recorta. Recortar o 4K a cada pixel de arrasto travaria.
        self._bytes: bytes = b""
        self._suffix = ".jpg"
        self._previa: Optional[Image.Image] = None

        # Os filtros guardados nas preferências. A troca automática ignora tudo
        # isso de propósito (fica no seguro); aqui cada imagem passa pelo olho
        # antes de ir para a parede, então vale o que o usuário escolheu.
        self._filtros = ler_filtros()
        self._tem_chave = tem_chave()
        self._trava_filtros = False
        self._montar_filtros()
        self.stack.connect("notify::visible-child-name", self._atualizar_filtros)
        self._atualizar_filtros()

        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("activate", lambda *_: self.search())
        self.flowbox.connect("child-activated", self._on_child_activated)
        self.none_button.connect("clicked", self._on_none_clicked)
        for corte in (self._paisagem, self._retrato):
            corte.adjustment.connect("value-changed", self._on_position_changed)
        self.adjust_back.connect("clicked", self._on_back_clicked)
        self.adjust_apply.connect("clicked", self._on_apply_clicked)
        self.connect("closed", self._on_closed)

        if arquivo is None:
            self.search_entry.set_text(clean_game_name(name))
            self.search()
        else:
            # Um arquivo do disco: não há o que buscar nem o que "não trocar",
            # a tela abre direto no ajuste e "Voltar" desiste da escolha.
            self.search_bar.set_visible(False)
            self.none_button.set_visible(False)
            # A tela vazia só aparece numa falha com o arquivo, e quem define o
            # título nesse caso é sempre o _show_empty do erro, não este trecho.
            self.stack.set_visible_child_name("loading")
            threading.Thread(
                target=self._open_file_thread,
                args=(arquivo, self._generation),
                daemon=True,
            ).start()

    # region Filters

    def _rotulos_ordenacao(self) -> list[str]:
        return [
            _("Relevância"),
            _("Adicionadas recentemente"),
            _("Mais vistas"),
            _("Mais favoritadas"),
            _("Em alta no toplist"),
            _("Aleatórias"),
            _("Em alta"),
        ]

    def _rotulos_alcance(self) -> list[str]:
        return [
            _("Último dia"),
            _("Últimos 3 dias"),
            _("Última semana"),
            _("Último mês"),
            _("Últimos 3 meses"),
            _("Últimos 6 meses"),
            _("Último ano"),
        ]

    def _montar_filtros(self) -> None:
        """Os controles da régua, refletindo os filtros guardados."""
        self._trava_filtros = True
        try:
            linha1 = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=8,
                halign=Gtk.Align.CENTER,
            )
            self._botoes_categorias = self._caixa_bandeiras(
                linha1,
                (("general", _("Geral")), ("anime", _("Anime")), ("people", _("Pessoas"))),
                self._filtros.categorias,
                0,
            )
            linha1.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
            self._botoes_pureza = self._caixa_bandeiras(
                linha1,
                (("sfw", "SFW"), ("sketchy", "Sketchy"), ("nsfw", "NSFW")),
                self._filtros.pureza,
                1,
            )
            if not self._tem_chave:
                for botao, nome in zip(self._botoes_pureza, ("sfw", "sketchy", "nsfw")):
                    if nome != "sfw":
                        botao.set_sensitive(False)
                        botao.set_tooltip_text(
                            _("Exige a chave da API (Preferências → Sessão)")
                        )

            self._ordenacao_dropdown = Gtk.DropDown.new_from_strings(
                self._rotulos_ordenacao()
            )
            self._ordenacao_dropdown.set_tooltip_text(_("Ordenação"))
            self._ordenacao_dropdown.set_selected(
                ORDENACOES.index(self._filtros.ordenacao)
            )
            self._ordenacao_dropdown.connect("notify::selected", self._on_ordenacao)
            linha1.append(self._ordenacao_dropdown)

            self._ordem_botao = Gtk.Button()
            self._atualizar_ordem_botao()
            self._ordem_botao.connect("clicked", self._on_ordem)
            linha1.append(self._ordem_botao)

            self._alcance_dropdown = Gtk.DropDown.new_from_strings(
                self._rotulos_alcance()
            )
            self._alcance_dropdown.set_tooltip_text(_("Período do toplist"))
            self._alcance_dropdown.set_selected(
                ALCANCES_TOPLIST.index(self._filtros.alcance)
            )
            self._alcance_dropdown.connect("notify::selected", self._on_alcance)
            self._alcance_dropdown.set_visible(self._filtros.ordenacao == "toplist")
            linha1.append(self._alcance_dropdown)
            self.filter_box.append(linha1)

            linha2 = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=6,
                halign=Gtk.Align.CENTER,
            )
            linha2.append(Gtk.Label(label=_("Cor:")))
            self._botoes_cores: dict[str, Gtk.ToggleButton] = {}
            todas = Gtk.ToggleButton(label=_("Todas"))
            todas.set_tooltip_text(_("Qualquer cor"))
            todas.set_active(not self._filtros.cores)
            todas.connect("toggled", self._on_cor, "")
            linha2.append(todas)
            self._botoes_cores[""] = todas
            for cor in CORES:
                amostra = Gtk.ToggleButton(tooltip_text=f"#{cor}")
                amostra.set_size_request(26, 18)
                amostra.add_css_class("wh-cor")
                amostra.add_css_class(f"wh-{cor}")
                amostra.set_active(self._filtros.cores.lower() == cor)
                amostra.connect("toggled", self._on_cor, cor)
                linha2.append(amostra)
                self._botoes_cores[cor] = amostra
            self._provedor_cores()
            self.filter_box.append(linha2)
        finally:
            self._trava_filtros = False

    def _caixa_bandeiras(
        self,
        linha: Gtk.Box,
        opcoes: tuple[tuple[str, str], ...],
        flags: str,
        grupo: int,
    ) -> list[Gtk.ToggleButton]:
        """Três botões ligados para um flags de três bits do site."""
        caixa = Gtk.Box()
        caixa.add_css_class("linked")
        botoes = []
        for posicao, (nome, rotulo) in enumerate(opcoes):
            botao = Gtk.ToggleButton(label=rotulo)
            botao.set_tooltip_text(rotulo)
            botao.set_active(flags[posicao] == "1")
            botao.connect("toggled", self._on_bandeira, grupo, posicao)
            caixa.append(botao)
            botoes.append(botao)
        linha.append(caixa)
        return botoes

    def _provedor_cores(self) -> None:
        regras = [
            ".wh-cor { padding: 0; min-width: 26px; min-height: 18px; "
            "border-radius: 5px; }"
        ]
        regras += [f".wh-{cor} {{ background: #{cor}; }}" for cor in CORES]
        provedor = Gtk.CssProvider()
        provedor.load_from_string("\n".join(regras))
        tela = Gdk.Display.get_default()
        if tela is not None:
            Gtk.StyleContext.add_provider_for_display(
                tela, provedor, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    def _atualizar_filtros(self, *_args: Any) -> None:
        # A régua só existe onde há busca: com arquivo aberto ela não tem para
        # onde voltar, e na tela de ajuste ela só roubaria espaço da prévia.
        self.filter_box.set_visible(
            self._arquivo is None
            and self.stack.get_visible_child_name() != "adjust"
        )

    def _atualizar_ordem_botao(self) -> None:
        decrescente = self._filtros.ordem == "desc"
        self._ordem_botao.set_icon_name(
            "view-sort-descending-symbolic"
            if decrescente
            else "view-sort-ascending-symbolic"
        )
        self._ordem_botao.set_tooltip_text(
            _("Decrescente (clique para inverter)")
            if decrescente
            else _("Crescente (clique para inverter)")
        )

    def _trocar_filtros(self, novos: Filtros) -> None:
        self._filtros = novos
        try:
            salvar_filtros(novos)
        except Exception as erro:  # preferência fora do ar não cancela a busca
            logging.info("Filtros do wallhaven não foram guardados (%s)", erro)
        self.search()

    def _on_bandeira(
        self, botao: Gtk.ToggleButton, grupo: int, posicao: int
    ) -> None:
        """Um dos três bits de categoria ou pureza. Nunca deixa zerar."""
        if self._trava_filtros:
            return
        campo = "categorias" if grupo == 0 else "pureza"
        bits = list(getattr(self._filtros, campo))
        bits[posicao] = "1" if botao.get_active() else "0"
        if all(bit == "0" for bit in bits):
            self._trava_filtros = True
            try:
                botao.set_active(True)
            finally:
                self._trava_filtros = False
            return
        try:
            novos = replace(self._filtros, **{campo: "".join(bits)})
        except ValueError:
            return
        self._trocar_filtros(novos)

    def _on_ordenacao(self, lista: Gtk.DropDown, *_args: Any) -> None:
        if self._trava_filtros:
            return
        try:
            novos = replace(self._filtros, ordenacao=ORDENACOES[lista.get_selected()])
        except (ValueError, IndexError):
            return
        self._alcance_dropdown.set_visible(novos.ordenacao == "toplist")
        self._trocar_filtros(novos)

    def _on_ordem(self, *_args: Any) -> None:
        if self._trava_filtros:
            return
        self._trocar_filtros(
            replace(
                self._filtros, ordem="asc" if self._filtros.ordem == "desc" else "desc"
            )
        )
        self._atualizar_ordem_botao()

    def _on_alcance(self, lista: Gtk.DropDown, *_args: Any) -> None:
        if self._trava_filtros:
            return
        try:
            novos = replace(self._filtros, alcance=ALCANCES_TOPLIST[lista.get_selected()])
        except (ValueError, IndexError):
            return
        self._trocar_filtros(novos)

    def _on_cor(self, botao: Gtk.ToggleButton, cor: str) -> None:
        """Uma cor por vez; desligar a ativa volta para todas."""
        if self._trava_filtros:
            return
        if botao.get_active():
            destino = cor
        else:
            destino = "" if cor else None
            if destino is None:
                self._trava_filtros = True
                try:
                    botao.set_active(True)
                finally:
                    self._trava_filtros = False
                return
        self._trava_filtros = True
        try:
            for outra, outro_botao in self._botoes_cores.items():
                if outro_botao is not botao:
                    outro_botao.set_active(outra == destino)
        finally:
            self._trava_filtros = False
        try:
            self._trocar_filtros(replace(self._filtros, cores=destino))
        except ValueError:
            return

    # endregion
    # region Search

    def _on_search_changed(self, *_args: Any) -> None:
        if self._debounce_id:
            GLib.source_remove(self._debounce_id)
        self._debounce_id = GLib.timeout_add(500, self._debounce_fire)

    def _debounce_fire(self) -> bool:
        self._debounce_id = 0
        # O set_text do __init__ emite um search-changed atrasado; sem este
        # guard, abrir a janela buscava duas vezes a mesma coisa.
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

        self.stack.set_visible_child_name("loading")
        threading.Thread(
            target=self._search_thread, args=(query, generation), daemon=True
        ).start()

    def _search_thread(self, query: str, generation: int) -> None:
        try:
            # O formato da grade primeiro e qualquer formato depois, na mesma
            # grade: o que menos precisa de corte aparece na frente, e o resto
            # continua à mão logo abaixo. O mínimo é o de todos os alvos, para
            # nenhum corte — nem o do outro formato, no arranjo misto — esticar.
            # A ordem aleatória ganha um seed por busca para não repetir a
            # página entre uma troca de filtro e outra.
            filtros = self._filtros
            semente = gerar_seed() if filtros.ordenacao == "random" else None
            minimo = self.formatos.minimo
            achados = buscar(
                query, *minimo, formato=self.formatos.ratio, filtros=filtros, seed=semente
            )
            vistos = {item["id"] for item in achados}
            achados += [
                item
                for item in buscar(query, *minimo, filtros=filtros, seed=semente)
                if item["id"] not in vistos
            ]
        except WallhavenError as error:
            logging.warning("Busca de papel de parede falhou: %s", error)
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
                _("Nenhum papel de parede encontrado"),
                _("Tente buscar por outro nome."),
                generation,
            )
            return

        # Seis de cada vez, na ordem da busca. Uma por vez, com 15s de limite
        # cada, uma rede ruim segurava a grade por minutos — e a janela fechada
        # só era notada entre uma miniatura e a seguinte.
        candidatos = achados[:MAX_RESULTS]
        with ThreadPoolExecutor(max_workers=6) as executor:
            miniaturas = executor.map(
                lambda item: self._miniatura(item, generation), candidatos
            )
            for item, dados in zip(candidatos, miniaturas):
                if generation != self._generation:
                    return
                if dados is not None:
                    entregar_na_tela(self._add_result, dados, item, generation)

        entregar_na_tela(self._finish_results, generation)

    def _miniatura(self, item: dict[str, Any], generation: int) -> Optional[bytes]:
        """A miniatura de ``item`` já cortada, em PNG. None quando não deu."""
        if generation != self._generation:
            return None  # busca trocada ou janela fechada: nem baixa
        try:
            miniatura = download_bytes(str(item["thumb"]), timeout=15)
        except requests.RequestException as error:
            logging.info("Miniatura não baixou (%s)", error)
            return None
        caminho = self._temp_dir / f"{item['id']}.img"
        try:
            caminho.write_bytes(miniatura)
            with Image.open(caminho) as arquivo:
                quadro = enquadrar(arquivo.convert("RGB"), *self._cell_pixels())
            return imagem_para_textura_bytes(quadro)
        except (OSError, UnidentifiedImageError, ValueError, Image.DecompressionBombError):
            return None

    def _cell_pixels(self) -> tuple[int, int]:
        """A célula em pixels de verdade, para não sair borrada num monitor HiDPI."""
        escala = max(1, int(shared.scale_factor))
        return self.cell_width * escala, self.cell_height * escala

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
        self, dados: bytes, item: dict[str, Any], generation: int
    ) -> bool:
        if generation != self._generation or self._closed:
            return False

        try:
            textura = Gdk.Texture.new_from_bytes(GLib.Bytes.new(dados))
        except GLib.Error:
            return False

        picture = Gtk.Picture(
            paintable=textura,
            content_fit=Gtk.ContentFit.COVER,
            width_request=self.cell_width,
            height_request=self.cell_height,
            can_shrink=True,
        )
        picture.add_css_class("wallpaper-preview")

        self.flowbox.append(picture)
        if child := picture.get_parent():
            child.set_focusable(True)
            self._results[child] = item
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

    def _show_results(self) -> None:
        # A imagem aberta some junto com a tela de ajuste: são dezenas de MB
        # de bitmap, e voltar para a grade quer dizer que ela foi descartada.
        self._previa = None
        self._bytes = b""
        self.stack.set_visible_child_name("results" if self._added else "empty")

    # endregion
    # region Adjust

    def _on_child_activated(
        self, _flowbox: Gtk.FlowBox, child: Gtk.FlowBoxChild
    ) -> None:
        if not (item := self._results.get(child)):
            return
        self.stack.set_visible_child_name("loading")
        threading.Thread(
            target=self._open_thread, args=(item, self._generation), daemon=True
        ).start()

    def _open_thread(self, item: dict[str, Any], generation: int) -> None:
        url = str(item["path"])
        try:
            conteudo = download_bytes(url, timeout=45)
        except requests.RequestException as error:
            logging.warning("Não foi possível baixar o papel de parede: %s", error)
            entregar_na_tela(
                self._show_empty,
                _("Não foi possível baixar a imagem"),
                _("Tente novamente."),
                generation,
            )
            return
        self._preparar(conteudo, Path(urlparse(url).path).suffix, generation)

    def _open_file_thread(self, arquivo: Path, generation: int) -> None:
        try:
            conteudo = arquivo.read_bytes()
        except OSError as error:
            logging.warning("Não foi possível ler o arquivo escolhido: %s", error)
            entregar_na_tela(
                self._show_empty,
                _("Não foi possível abrir a imagem"),
                _("Tente outra imagem."),
                generation,
            )
            return
        self._preparar(conteudo, arquivo.suffix, generation)

    def _preparar(self, conteudo: bytes, sufixo: str, generation: int) -> None:
        """Abre ``conteudo`` e entrega a cópia reduzida à tela de ajuste."""
        try:
            with Image.open(Path(self._salvar_temporario(conteudo, sufixo))) as arquivo:
                imagem = arquivo.convert("RGB")
                escala = min(
                    1.0,
                    PREVIEW_SOURCE_HEIGHT
                    * max(1, int(shared.scale_factor))
                    / max(1, imagem.height),
                )
                previa = (
                    imagem.resize(
                        (
                            max(1, round(imagem.width * escala)),
                            max(1, round(imagem.height * escala)),
                        ),
                        Image.LANCZOS,
                    )
                    if escala < 1
                    else imagem.copy()
                )
        # DecompressionBombError não herda de OSError: sem ela aqui, a thread
        # morria e a tela ficava girando para sempre.
        except (
            OSError,
            UnidentifiedImageError,
            ValueError,
            Image.DecompressionBombError,
        ) as error:
            logging.warning("Imagem escolhida não pôde ser lida: %s", error)
            entregar_na_tela(
                self._show_empty,
                _("Não foi possível abrir a imagem"),
                _("Tente outra imagem."),
                generation,
            )
            return

        entregar_na_tela(self._open_done, conteudo, sufixo, previa, generation)

    def _salvar_temporario(self, conteudo: bytes, sufixo: str) -> str:
        caminho = self._temp_dir / f"escolhida{sufixo or '.jpg'}"
        caminho.write_bytes(conteudo)
        return str(caminho)

    def _cortes(self) -> list[tuple[tuple[int, int], _Corte]]:
        """Os cortes deste arranjo: um por orientação dos monitores-alvo."""
        cortes = [
            (tamanho, corte)
            for tamanho, corte in (
                (self.formatos.paisagem, self._paisagem),
                (self.formatos.retrato, self._retrato),
            )
            if tamanho
        ]
        # Sem alvo nenhum (monitor desligado com a tela aberta), o formato da
        # grade ainda precisa de um corte onde aparecer.
        return cortes or [(self.formatos.grade, self._paisagem)]

    def _open_done(
        self, conteudo: bytes, sufixo: str, previa: Image.Image, generation: int
    ) -> bool:
        if generation != self._generation or self._closed:
            return False

        self._bytes = conteudo
        sufixo = sufixo.lower()
        self._suffix = sufixo if sufixo in IMAGE_SUFFIXES else ".jpg"
        self._previa = previa

        cortes = self._cortes()
        for corte in (self._paisagem, self._retrato):
            corte.box.set_visible(any(corte is visivel for _tamanho, visivel in cortes))

        for (largura, altura), corte in cortes:
            # A barrinha só existe onde há o que deslizar: arte já na proporção
            # do monitor entra inteira.
            tem_corte = corta(previa.width, previa.height, largura, altura)
            corte.hint.set_visible(tem_corte)
            corte.scale.set_visible(tem_corte)
            # Qual eixo a barrinha move depende das duas proporções: arte larga
            # é cortada nas laterais, arte alta demais em cima e embaixo. Dizer
            # isso poupa o usuário de descobrir arrastando.
            corte.hint.set_label(
                _("Arraste para escolher a faixa da imagem")
                if eixo_do_corte(previa.width, previa.height, largura, altura)
                else _("Arraste para escolher a altura da imagem")
            )
            corte.adjustment.set_value(50)

        self._redesenhar_previa()
        self.stack.set_visible_child_name("adjust")
        return False

    def _on_position_changed(self, adjustment: Gtk.Adjustment) -> None:
        self._redesenhar_previa(adjustment)

    def _redesenhar_previa(self, so: Optional[Gtk.Adjustment] = None) -> None:
        """Recorta a prévia de cada corte; só a de ``so``, quando dado.

        Arrastar uma barrinha redesenha só o corte dela: no arranjo misto, os
        dois a cada pixel de arrasto seriam o dobro do trabalho para nada.
        """
        if self._previa is None:
            return
        cortes = self._cortes()
        escala = max(1, int(shared.scale_factor))
        for (largura, altura), corte in cortes:
            if so is not None and corte.adjustment is not so:
                continue
            if len(cortes) == 1:
                caixa = PREVIEW_MAX
            elif altura > largura:
                caixa = HYBRID_PORTRAIT_MAX
            else:
                caixa = HYBRID_LANDSCAPE_MAX
            previa_largura, previa_altura = caber(largura, altura, *caixa)
            quadro = enquadrar(
                self._previa,
                previa_largura * escala,
                previa_altura * escala,
                corte.adjustment.get_value() / 100,
            )
            try:
                textura = Gdk.Texture.new_from_bytes(
                    GLib.Bytes.new(imagem_para_textura_bytes(quadro))
                )
            except GLib.Error as error:
                logging.info("Prévia do corte não pôde ser desenhada: %s", error)
                continue
            corte.picture.set_size_request(previa_largura, previa_altura)
            corte.picture.set_paintable(textura)

    def _on_apply_clicked(self, *_args: Any) -> None:
        if not self._bytes:
            return
        # Entregue fora da pasta temporária desta janela, que é apagada ao
        # fechar: quem chamou segura o arquivo até o Aplicar da edição — ou
        # até o Cancelar, que continua cancelando.
        try:
            modelo = f"cartridges_wallpaper_XXXXXX{self._suffix}"
            arquivo, fluxo = Gio.File.new_tmp(modelo)
            # Fechado antes da escrita: aberto, o fluxo que `new_tmp` devolve
            # segura o arquivo até o coletor de lixo passar.
            fluxo.close()
            caminho = Path(arquivo.get_path())
            caminho.write_bytes(self._bytes)
        except (GLib.Error, OSError) as error:
            logging.warning("Não foi possível salvar a escolha: %s", error)
            self._show_empty(
                _("Não foi possível salvar a imagem"), _("Tente novamente.")
            )
            return

        # A orientação que não existe neste arranjo nunca saiu do meio.
        self.on_selected(
            caminho,
            Posicoes(
                retrato=self.adjust_portrait_adjustment.get_value() / 100,
                paisagem=self.adjust_landscape_adjustment.get_value() / 100,
            ),
        )
        self.close()

    def _on_back_clicked(self, *_args: Any) -> None:
        # Aberta com um arquivo, a tela não tem grade para onde voltar.
        if self._arquivo is not None:
            self.close()
        else:
            self._show_results()

    # endregion

    def _on_none_clicked(self, *_args: Any) -> None:
        self.on_cleared()
        self.close()

    def _on_closed(self, *_args: Any) -> None:
        self._closed = True
        if self._debounce_id:
            GLib.source_remove(self._debounce_id)
            self._debounce_id = 0
        self._generation += 1
        self._clear_results()
        self._previa = None
        self._bytes = b""
        shutil.rmtree(self._temp_dir, ignore_errors=True)
