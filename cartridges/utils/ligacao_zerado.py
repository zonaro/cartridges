# ligacao_zerado.py
#
# Copyright 2026 joaomgabaldi
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Liga um jogo de Jogos Zerados ao jogo da biblioteca que tem o mesmo appID.

Um zerado recriado pelo backup (ou reinstalado de outra pasta) não casa com o
atalho novo: o jogo volta como um jogo novo, e a página ficaria com as duas
versões. Quando o jogo da biblioteca ganha o appID da Steam (pela rotina da
Steam ou pela edição), os dados do zerado passam para ele e o zerado sai, como
se tivesse sido movido de Jogos Zerados para a biblioteca.

Liga só quando não há dúvida: exatamente um zerado e exatamente um jogo da
biblioteca com aquele appID. Qualquer outro caso fica como está, com uma linha
no log — escolher às cegas estragaria dados.
"""

import logging
import shutil

from gi.repository import Adw, GLib

from cartridges import shared
from cartridges.game import Game
from cartridges.store.managers.display_manager import is_main_thread
from cartridges.utils import game_logo, session_fita, session_log, session_wallpaper
from cartridges.utils.save_cover import ANIMATED_SUFFIXES


def agendar(jogo: Game) -> None:
    """Pede a ligação na thread principal. Seguro de qualquer thread."""

    def _tick() -> bool:
        ligar_zerado(jogo)
        return False

    GLib.idle_add(_tick)


def _vazio(valor) -> bool:
    return valor in (None, "", 0) or (isinstance(valor, str) and not valor.strip())


def _mover_capa(zerado: Game, jogo: Game) -> None:
    origem = zerado.get_cover_path()
    if origem is None:
        return
    for sufixo in (*ANIMATED_SUFFIXES, ".tiff"):
        (shared.covers_dir / f"{jogo.game_id}{sufixo}").unlink(missing_ok=True)
    destino = shared.covers_dir / f"{jogo.game_id}{origem.suffix}"
    shutil.copyfile(origem, destino)
    if (capa := shared.win.game_covers.get(jogo.game_id)) is not None:
        capa.new_cover(destino)


def _mover_escolhas(zerado: Game, jogo: Game) -> None:
    """Leva a escolha manual do zerado, só se o jogo não tiver uma própria.

    "Escolha própria" é qualquer decisão travada do jogo, não só a presença de
    um arquivo: no logo, "usar o título" (``logo_choice`` devolve ``"title"``)
    é uma decisão tão do jogo quanto um logo manual; na parede de sessão,
    "não trocar" (``escolha`` devolve ``"none"``) idem. Só ``"auto"`` (nenhuma
    decisão tomada) conta como vazio — sobrescrever uma decisão travada do
    jogo seria desfazê-la, não preencher uma lacuna.
    """
    if game_logo.logo_choice(zerado) == "manual" and game_logo.logo_choice(jogo) == "auto":
        if (logo := game_logo.cached_logo_path(zerado)) is not None:
            game_logo.save_manual_logo(jogo.game_id, jogo.name, logo)
    if (
        session_wallpaper.escolha(zerado) == "manual"
        and session_wallpaper.escolha(jogo) == "auto"
        and (parede := session_wallpaper.imagem_escolhida(zerado.game_id)) is not None
    ):
        session_wallpaper.salvar_escolha(
            jogo.game_id, jogo.name, parede,
            session_wallpaper.posicoes_escolhidas(zerado.game_id),
        )
    if (cor := session_fita.cor_escolhida(zerado.game_id)) is not None and not (
        session_fita.escolhida(jogo.game_id)
    ):
        session_fita.salvar_cor(jogo.game_id, jogo.name, cor)


def ligar_zerado(jogo: Game) -> bool:
    """Liga ``jogo`` ao zerado de mesmo appID. True: ligou."""
    assert is_main_thread(), "ligar_zerado toca a store e widgets"
    appid = getattr(jogo, "steam_appid", None)
    if not appid or jogo.removed or jogo.blacklisted:
        return False

    zerados = [g for g in shared.store if g.zerado and g.steam_appid == appid]
    vivos = [
        g for g in shared.store
        if not g.removed and not g.blacklisted and g.steam_appid == appid
    ]
    if len(zerados) != 1 or vivos != [jogo]:
        logging.info(
            "Sem ligação de zerado para o appID %s: %d zerado(s), %d jogo(s) na biblioteca",
            appid, len(zerados), len(vivos),
        )
        return False
    zerado = zerados[0]

    jogo.playtime = (jogo.playtime or 0) + (zerado.playtime or 0)
    jogo.last_played = max(jogo.last_played or 0, zerado.last_played or 0)
    for campo in ("status", "rating", "notes"):
        if _vazio(getattr(jogo, campo, None)) and not _vazio(getattr(zerado, campo, None)):
            setattr(jogo, campo, getattr(zerado, campo))

    session_log.mover_jogo(zerado.game_id, jogo.game_id)
    try:
        _mover_capa(zerado, jogo)
        _mover_escolhas(zerado, jogo)
    except OSError as erro:
        # Capa e escolhas se refazem; o tempo e as sessões, que já passaram,
        # são o que não pode se perder.
        logging.warning("Ligação de %s: arquivos não copiados: %s", jogo.name, erro)

    # Salva antes de excluir: se `cleanup_game` falhar (ex.: PermissionError no
    # Windows, arquivo aberto por outro processo), o jogo já ficou com o tempo
    # somado e o resto gravado em disco — só o zerado, que também tentou sair,
    # fica para trás, em vez de o trabalho de fusão inteiro morrer na memória.
    jogo.save()
    jogo.update()

    shared.win.retirar_da_grade(zerado)
    shared.store.excluir(zerado, apagar_sessoes=False)

    # Se a tela de detalhes do zerado ainda estiver aberta, sai dela — do
    # contrário `active_game` continua apontando para um jogo que não existe
    # mais na store, e qualquer ação ali (mudar status, editar) reviveria o
    # arquivo apagado, contando o tempo em dobro numa próxima ligação. Mesmo
    # caminho do Excluir.
    if (
        getattr(shared.win, "active_game", None) is zerado
        and shared.win.navigation_view.get_visible_page() == shared.win.details_page
    ):
        shared.win.navigation_view.pop()

    shared.win.library.invalidate_sort()
    shared.win.library.invalidate_filter()
    shared.win.zerados_library.invalidate_filter()
    shared.win.set_library_child()

    # A variável é o nome do jogo
    toast = Adw.Toast.new(
        _("Os dados de {} em Jogos Zerados foram transferidos para a biblioteca").format(
            jogo.name
        )
    )
    toast.set_use_markup(False)
    shared.win.toast_queue.add(toast)
    return True
