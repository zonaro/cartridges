# wallhaven_filter_bar.py
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

"""A régua de filtros do wallhaven, reutilizável entre as telas de escolha.

Extraída do wallpaper_picker: as duas linhas de controles (categorias,
pureza, ordenação, ordem, alcance do toplist e cor dominante) com exatamente
o mesmo comportamento — lê os filtros guardados, guarda cada troca e avisa
quem hospeda via ``on_changed`` para refazer a busca. Todos os parâmetros
oficiais da busca que fazem sentido à mão estão aqui; ``seed``, página,
resoluções e proporções continuam automáticos, como no módulo de busca.
"""

import logging
from dataclasses import replace
from typing import Any, Callable, Optional
from gi.repository import Gdk, Gtk

from cartridges.utils.wallhaven import (
    ALCANCES_TOPLIST,
    CORES,
    ORDENACOES,
    Filtros,
    ler_filtros,
    salvar_filtros,
    tem_chave,
)

_PROVEDOR_CSS: Optional[Gtk.CssProvider] = None


def rotulos_ordenacao() -> list[str]:
    """Os rótulos da ordenação, na ordem de ``ORDENACOES``."""
    return [
        _("Relevância"),
        _("Adicionadas recentemente"),
        _("Mais vistas"),
        _("Mais favoritadas"),
        _("Em alta no toplist"),
        _("Aleatórias"),
        _("Em alta"),
    ]


def rotulos_alcance() -> list[str]:
    """Os rótulos do alcance, na ordem de ``ALCANCES_TOPLIST``."""
    return [
        _("Último dia"),
        _("Últimos 3 dias"),
        _("Última semana"),
        _("Último mês"),
        _("Últimos 3 meses"),
        _("Últimos 6 meses"),
        _("Último ano"),
    ]


class WallhavenFilterBar(Gtk.Box):
    """As duas linhas de filtros do wallhaven, com todos os filtros."""

    def __init__(self, on_changed: Callable[[Filtros], None], **kwargs: Any) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6, **kwargs)
        self._on_changed = on_changed
        self._filtros = ler_filtros()
        self._tem_chave = tem_chave()
        self._trava = False
        self._montar()
        self._garantir_css()

    @property
    def filtros(self) -> Filtros:
        """Os filtros como estão agora na régua."""
        return self._filtros

    def _montar(self) -> None:
        self._trava = True
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
                rotulos_ordenacao()
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

            self._alcance_dropdown = Gtk.DropDown.new_from_strings(rotulos_alcance())
            self._alcance_dropdown.set_tooltip_text(_("Período do toplist"))
            self._alcance_dropdown.set_selected(
                ALCANCES_TOPLIST.index(self._filtros.alcance)
            )
            self._alcance_dropdown.connect("notify::selected", self._on_alcance)
            self._alcance_dropdown.set_visible(self._filtros.ordenacao == "toplist")
            linha1.append(self._alcance_dropdown)
            self.append(linha1)

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
            self.append(linha2)
        finally:
            self._trava = False

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

    @classmethod
    def _garantir_css(cls) -> None:
        """As amostras de cor, registradas uma vez por display."""
        global _PROVEDOR_CSS
        if _PROVEDOR_CSS is not None:
            return
        regras = [
            ".wh-cor { padding: 0; min-width: 26px; min-height: 18px; "
            "border-radius: 5px; }"
        ]
        regras += [f".wh-{cor} {{ background: #{cor}; }}" for cor in CORES]
        _PROVEDOR_CSS = Gtk.CssProvider()
        _PROVEDOR_CSS.load_from_string("\n".join(regras))
        if tela := Gdk.Display.get_default():
            Gtk.StyleContext.add_provider_for_display(
                tela, _PROVEDOR_CSS, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
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

    def _trocar(self, novos: Filtros) -> None:
        self._filtros = novos
        try:
            salvar_filtros(novos)
        except Exception as erro:  # preferência fora do ar não cancela a busca
            logging.info("Filtros do wallhaven não foram guardados (%s)", erro)
        self._on_changed(novos)

    def _on_bandeira(
        self, botao: Gtk.ToggleButton, grupo: int, posicao: int
    ) -> None:
        """Um dos três bits de categoria ou pureza. Nunca deixa zerar."""
        if self._trava:
            return
        campo = "categorias" if grupo == 0 else "pureza"
        bits = list(getattr(self._filtros, campo))
        bits[posicao] = "1" if botao.get_active() else "0"
        if all(bit == "0" for bit in bits):
            self._trava = True
            try:
                botao.set_active(True)
            finally:
                self._trava = False
            return
        try:
            novos = replace(self._filtros, **{campo: "".join(bits)})
        except ValueError:
            return
        self._trocar(novos)

    def _on_ordenacao(self, lista: Gtk.DropDown, *_args: Any) -> None:
        if self._trava:
            return
        try:
            novos = replace(self._filtros, ordenacao=ORDENACOES[lista.get_selected()])
        except (ValueError, IndexError):
            return
        self._alcance_dropdown.set_visible(novos.ordenacao == "toplist")
        self._trocar(novos)

    def _on_ordem(self, *_args: Any) -> None:
        if self._trava:
            return
        self._trocar(
            replace(
                self._filtros, ordem="asc" if self._filtros.ordem == "desc" else "desc"
            )
        )
        self._atualizar_ordem_botao()

    def _on_alcance(self, lista: Gtk.DropDown, *_args: Any) -> None:
        if self._trava:
            return
        try:
            novos = replace(self._filtros, alcance=ALCANCES_TOPLIST[lista.get_selected()])
        except (ValueError, IndexError):
            return
        self._trocar(novos)

    def _on_cor(self, botao: Gtk.ToggleButton, cor: str) -> None:
        """Uma cor por vez; desligar a ativa volta para todas."""
        if self._trava:
            return
        if botao.get_active():
            destino = cor
        else:
            destino = "" if cor else None
            if destino is None:
                self._trava = True
                try:
                    botao.set_active(True)
                finally:
                    self._trava = False
                return
        self._trava = True
        try:
            for outra, outro_botao in self._botoes_cores.items():
                if outro_botao is not botao:
                    outro_botao.set_active(outra == destino)
        finally:
            self._trava = False
        try:
            self._trocar(replace(self._filtros, cores=destino))
        except ValueError:
            return
