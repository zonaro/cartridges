# zerado_manual.py
#
# Copyright 2026 joaomgabaldi
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Jogos Zerados adicionados à mão: jogos terminados que nunca passaram pelo
Jolven (console, outro PC, anos atrás).

Viram uma tumba zerada: ``imported_N``, sem atalho, sem executável, sem
tempo de jogo. Por isso backup, edição, Excluir e ordenação não precisam saber
que eles existem.

Sem repetição: um jogo que já está em Jogos Zerados ou na biblioteca não é
criado de novo, e um desinstalado com a mesma identidade é marcado em vez de
ganhar uma cópia.
"""

from time import time
from typing import Optional

from cartridges import shared
from cartridges.game import Game
from cartridges.store.managers.sgdb_manager import SgdbManager
from cartridges.store.managers.steam_api_manager import SteamAPIManager
from cartridges.utils.name_cleaner import clean_for_search, clean_game_name
from cartridges.utils.steam import SteamAPIHelper, SteamRateLimiter


def _chaves(nome: str, appid: Optional[str]) -> set[str]:
    """As duas identidades de um jogo, appID e nome limpo: um
    resultado da Steam é o mesmo jogo pelo appID ou pelo nome."""
    chaves = {f"nome:{clean_for_search(nome or '').casefold()}"}
    if appid:
        chaves.add(f"steam:{appid}")
    return chaves


def situacao(nome: str, appid: Optional[str] = None) -> tuple[str, Optional[Game]]:
    """Onde o jogo ``nome``/``appid`` já está: ``"zerado"``, ``"biblioteca"``,
    ``"desinstalado"`` (o jogado por último, se houver vários) ou ``"novo"``."""
    procuradas = _chaves(nome, appid)
    na_biblioteca: Optional[Game] = None
    desinstalados: list[Game] = []
    for jogo in shared.store:
        if jogo.blacklisted or not _chaves(jogo.name, jogo.steam_appid) & procuradas:
            continue
        if jogo.zerado:
            return ("zerado", jogo)
        if not jogo.removed:
            na_biblioteca = jogo
        else:
            desinstalados.append(jogo)
    if na_biblioteca is not None:
        return ("biblioteca", na_biblioteca)
    if desinstalados:
        return ("desinstalado", max(desinstalados, key=lambda j: j.last_played or 0))
    return ("novo", None)


def criar(nome: str, appid: Optional[str] = None) -> Game:
    """Registra a tumba zerada, já na grade de Jogos Zerados."""
    dados = {
        "game_id": shared.store.proximo_id_importado(),
        "source": "imported",
        "name": clean_game_name(nome),
        "executable": "",
        "added": int(time()),
        "removed": True,
        "status": "beaten",
    }
    if appid:
        dados["steam_appid"] = str(appid)
    jogo = Game(dados)
    shared.store.add_game(jogo, {})
    jogo.save()
    return jogo


def buscar_dados(jogo: Game) -> None:
    """Steam → SteamGridDB, um depois do outro, como na atualização de
    metadados (`metadata_refresh`). Sem appID não há o que buscar: um jogo
    que a Steam não conhece recebe a capa pela edição.

    `sem_ligacao`: o bloqueio de `situacao` já impede um zerado com o appID de
    um jogo da biblioteca, e a ligação não precisa rodar por baixo.
    """
    ordem = [
        manager
        for manager in (
            shared.store.managers.get(classe)
            for classe in (SteamAPIManager, SgdbManager)
        )
        if manager is not None
    ]
    if not jogo.steam_appid or not ordem:
        return

    dados = {"zerado_manual": True, "sem_ligacao": True}
    jogo.set_loading(1)

    def proximo(indice: int) -> None:
        if indice >= len(ordem):
            jogo.set_loading(-1)
            # Excluído enquanto buscava: não grava de volta no disco.
            if shared.store.get(jogo.game_id) is jogo:
                jogo.save()
                jogo.update()
            return
        ordem[indice].process_game(jogo, dados, lambda _m: proximo(indice + 1))

    proximo(0)


def adicionar(nome: str, appid: Optional[str] = None) -> Optional[Game]:
    """O clique no diálogo. ``None`` quando o jogo já está em Jogos Zerados ou
    na biblioteca (a linha já vem desativada; isto é a segunda trava)."""
    estado, existente = situacao(nome, appid)
    if estado == "desinstalado" and existente is not None:
        existente.definir_status("beaten")
        existente.save()
        existente.update()
        return existente
    if estado != "novo":
        return None
    jogo = criar(nome, appid)
    buscar_dados(jogo)
    return jogo


def helper_da_steam() -> SteamAPIHelper:
    """O helper do manager, como na edição: um `SteamRateLimiter` por busca
    vazaria a thread de recarga dele."""
    manager = shared.store.managers.get(SteamAPIManager)
    return manager.steam_api_helper if manager else SteamAPIHelper(SteamRateLimiter())
