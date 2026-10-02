# zerados_picker.py
#
# Copyright 2026 joaomgabaldi
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""O "Adicionar" da página Jogos Zerados: marcar como Zerado um jogo que já
foi desinstalado.

A tela de detalhes só abre para o que está em alguma grade, e um desinstalado
sem a marca não está em nenhuma — sem isto, um jogo desinstalado antes de ser
marcado nunca mais teria como entrar na página. Montado em Python, como o
histórico de sessões: é uma lista cujo tamanho só se sabe ao abrir.

Também adiciona um jogo que nunca passou pelo app: o campo do topo busca na
Steam, e a última linha cria o jogo só pelo nome. A regra de quem pode entrar
fica em `zerado_manual`; aqui só se mostram as linhas.
"""

import logging
import threading
from typing import Any, Optional

import requests
from gi.repository import Adw, Gtk

from cartridges import shared
from cartridges.game import Game
from cartridges.utils import zerado_manual
from cartridges.utils.format_playtime import format_playtime
from cartridges.utils.na_tela import entregar_na_tela
from cartridges.utils.name_cleaner import clean_for_search
from cartridges.utils.relative_date import relative_date
from cartridges.utils.spring_scroll import attach as attach_spring_scroll
from cartridges.utils.steam import SteamError, SteamGameNotFoundError
from cartridges.utils.title_match import TitleMatch


def candidatos() -> list[Game]:
    """Os desinstalados que ainda não são zerados, em ordem de nome."""
    return sorted(
        (
            game
            for game in shared.store
            if game.removed and not game.blacklisted and not game.zerado
        ),
        key=lambda game: game.name.casefold(),
    )


def descricao(jogo: Game) -> str:
    """Tempo e última vez jogado: o que separa duas fichas de mesmo nome."""
    if not jogo.last_played:
        return _("Nunca jogado")
    # As variáveis são o tempo de jogo e a data da última vez jogado
    return _("{} · Jogado por último: {}").format(
        format_playtime(jogo.playtime), relative_date(jogo.last_played)
    )


_MOTIVO = {
    "zerado": _("Já está em Jogos Zerados"),
    "biblioteca": _("Já está na biblioteca"),
}


class ZeradosPicker(Adw.Dialog):
    """Os desinstalados para marcar e, com a busca, os jogos para adicionar."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.set_title(_("Adicionar a Jogos Zerados"))
        self.set_content_width(460)
        self.set_content_height(520)

        # Resposta de uma busca antiga (ou do diálogo já fechado) não pinta
        # a lista: o mesmo contador do `SteamPicker`.
        self._geracao = 0
        self._fechado = False
        self._ultima: Optional[tuple[str, list, Optional[str]]] = None

        self.busca = Gtk.SearchEntry(
            placeholder_text=_("Buscar na Steam"),
            margin_top=6,
            margin_bottom=6,
            margin_start=12,
            margin_end=12,
        )
        self.busca.connect("activate", lambda *_: self.buscar())
        self.busca.connect("search-changed", self._ao_mudar_busca)

        self.aviso = Gtk.Label(
            visible=False, wrap=True, xalign=0, margin_bottom=12
        )
        self.aviso.add_css_class("dim-label")

        self.lista = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE, valign=Gtk.Align.START
        )
        self.lista.add_css_class("boxed-list")

        conteudo = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        conteudo.append(self.aviso)
        conteudo.append(self.lista)

        rolagem = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True
        )
        rolagem.set_child(
            Adw.Clamp(
                child=conteudo,
                margin_top=12,
                margin_bottom=12,
                margin_start=12,
                margin_end=12,
            )
        )
        # A mesma mola das grades: a lista também rola com inércia.
        attach_spring_scroll(rolagem)

        vazio = Adw.StatusPage(
            icon_name="object-select-symbolic",
            title=_("Nenhum jogo disponível"),
            description=_("Busque um jogo na Steam para adicioná-lo."),
        )

        self.pilha = Gtk.Stack()
        self.pilha.add_named(rolagem, "lista")
        self.pilha.add_named(vazio, "vazio")
        self.pilha.add_named(Adw.Spinner(), "carregando")
        self._mostrar_desinstalados()

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        toolbar.add_top_bar(self.busca)
        toolbar.set_content(self.pilha)
        self.set_child(toolbar)

        self.connect("closed", self._ao_fechar)

    def _ao_fechar(self, *_args: Any) -> None:
        self._fechado = True

    def _limpar(self) -> None:
        while (linha := self.lista.get_row_at_index(0)) is not None:
            self.lista.remove(linha)

    def _mostrar(self) -> None:
        self.pilha.set_visible_child_name(
            "lista" if self.lista.get_row_at_index(0) else "vazio"
        )

    # region Desinstalados

    def _mostrar_desinstalados(self) -> None:
        self._ultima = None
        self._limpar()
        self.aviso.set_visible(False)
        for jogo in candidatos():
            # Sem markup: um "&" no nome de um jogo não é marcação.
            linha = Adw.ActionRow(
                title=jogo.name,
                subtitle=descricao(jogo),
                activatable=True,
                use_markup=False,
            )
            linha.add_suffix(Gtk.Image.new_from_icon_name("list-add-symbolic"))
            linha.connect("activated", self.on_escolhido, jogo)
            self.lista.append(linha)
        self._mostrar()

    def on_escolhido(self, linha: Adw.ActionRow, jogo: Game) -> None:
        # Fica aberto: quem veio marcar os jogos antigos costuma ter vários.
        # Linha velha (o jogo foi reinstalado, ou a ficha trocada na store
        # desde que a lista abriu): só sai da lista, sem mexer no jogo.
        if shared.store.get(jogo.game_id) is jogo and jogo.removed:
            jogo.definir_status("beaten")
            jogo.save()
            jogo.update()
        self.lista.remove(linha)
        self._mostrar()

    # endregion
    # region Busca na Steam

    def _ao_mudar_busca(self, *_args: Any) -> None:
        # Só o campo vazio age sozinho; a busca na Steam espera o Enter, que
        # a loja limita as requisições.
        if not self.busca.get_text().strip():
            self._geracao += 1
            self._mostrar_desinstalados()

    def buscar(self) -> None:
        texto = self.busca.get_text().strip()
        if not texto:
            return
        self._geracao += 1
        self.pilha.set_visible_child_name("carregando")
        threading.Thread(
            target=self._buscar, args=(texto, self._geracao), daemon=True
        ).start()

    def _buscar(self, texto: str, geracao: int) -> None:
        aviso: Optional[str] = None
        candidatos_steam: list[tuple[dict, TitleMatch]] = []
        try:
            candidatos_steam = zerado_manual.helper_da_steam().find_candidates(
                clean_for_search(texto)
            )
        except SteamGameNotFoundError:
            aviso = _("Nenhum jogo encontrado na Steam.")
        except (SteamError, requests.RequestException) as erro:
            logging.warning("Busca de zerado na Steam falhou: %s", erro)
            aviso = _("Não foi possível concluir a busca na Steam.")
        entregar_na_tela(
            self._mostrar_resultados, texto, candidatos_steam, aviso, geracao
        )

    def _mostrar_resultados(
        self,
        texto: str,
        candidatos_steam: list[tuple[dict, TitleMatch]],
        aviso: Optional[str],
        geracao: int,
    ) -> bool:
        if geracao != self._geracao or self._fechado:
            return False
        self._ultima = (texto, candidatos_steam, aviso)
        self._limpar()
        self.aviso.set_label(aviso or "")
        self.aviso.set_visible(bool(aviso))

        for candidato, _match in candidatos_steam:
            appid = str(candidato.get("id", ""))
            if not appid:
                continue
            nome = str(candidato.get("name", ""))
            self._linha(nome, appid, nome, _("ID na Steam: {}").format(appid))
        self._linha(
            texto, None, _("Adicionar “{}” sem dados da Steam").format(texto), ""
        )
        self._mostrar()
        return False

    def _linha(
        self, nome: str, appid: Optional[str], titulo: str, subtitulo: str
    ) -> None:
        estado, _existente = zerado_manual.situacao(nome, appid)
        linha = Adw.ActionRow(
            title=titulo,
            subtitle=_MOTIVO.get(estado, subtitulo),
            activatable=estado not in _MOTIVO,
            use_markup=False,
        )
        if estado in _MOTIVO:
            linha.set_sensitive(False)
        else:
            linha.add_suffix(Gtk.Image.new_from_icon_name("list-add-symbolic"))
            linha.connect("activated", self._ao_escolher_resultado, nome, appid)
        self.lista.append(linha)

    def _ao_escolher_resultado(
        self, _linha: Adw.ActionRow, nome: str, appid: Optional[str]
    ) -> None:
        zerado_manual.adicionar(nome, appid)
        # Redesenha em vez de tirar a linha: o jogo recém-adicionado volta
        # desativado, e a linha do nome com ele — não há como criar duas vezes.
        if self._ultima is not None:
            self._mostrar_resultados(*self._ultima, self._geracao)

    # endregion
