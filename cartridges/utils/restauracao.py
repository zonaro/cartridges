# restauracao.py
#
# Copyright 2026 joaomgabaldi
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""O que sobra de uma restauração de backup: os jogos restaurados com
pendências.

Tudo aqui só existe enquanto `restauracao_pendente.json` existir na pasta do
app. Sem o arquivo, a importação, a biblioteca e a abertura seguem como
sempre. O arquivo nasce na troca (`backup.aplicar_pendente`) e morre quando a
última pendência sai.
"""

import json
import logging
from pathlib import Path
from typing import Iterable, Optional

from cartridges import shared
from cartridges.game import Game
from cartridges.utils import session_log

_NOME = "restauracao_pendente.json"

# Lido uma vez por caminho: `e_pendente` roda para cada card em todo filtro da
# biblioteca. A chave é o caminho, e não um valor único, porque os testes
# repontam `shared.app_dir` a cada teste. Só `gravar` escreve o arquivo, e ela
# descarta a entrada.
_cache: dict[str, tuple[frozenset[str], int]] = {}


def _arquivo() -> Path:
    return shared.app_dir / _NOME


def _ler() -> tuple[frozenset[str], int]:
    caminho = _arquivo()
    chave = str(caminho)
    if chave not in _cache:
        try:
            dados = json.loads(caminho.read_text(encoding="utf-8"))
            lidos = (
                frozenset(str(game_id) for game_id in dados.get("jogos", [])),
                int(dados.get("restaurado_em", 0)),
            )
        except FileNotFoundError:
            lidos = (frozenset(), 0)
        except (OSError, ValueError, TypeError, AttributeError):
            # Ilegível: sem pendências. A próxima importação regrava a lista
            # vazia e o arquivo some, encerrando o fluxo.
            logging.warning("%s ilegível; pendências da restauração ignoradas", caminho)
            lidos = (frozenset(), 0)
        _cache[chave] = lidos
    return _cache[chave]


def existe() -> bool:
    return _arquivo().is_file()


def ids() -> frozenset[str]:
    return _ler()[0]


def restaurado_em() -> int:
    return _ler()[1]


def e_pendente(game_id: str) -> bool:
    return game_id in _ler()[0]


def gravar(game_ids: Iterable[str], quando: int) -> None:
    """Grava as pendências. Lista vazia apaga o arquivo e encerra o fluxo."""
    caminho = _arquivo()
    _cache.pop(str(caminho), None)
    ordenados = sorted(set(game_ids))
    if not ordenados:
        caminho.unlink(missing_ok=True)
        return
    temporario = caminho.with_name(_NOME + ".tmp")
    temporario.write_text(
        json.dumps({"restaurado_em": quando, "jogos": ordenados}, ensure_ascii=False),
        encoding="utf-8",
    )
    temporario.replace(caminho)


def remover(game_id: str) -> None:
    if e_pendente(game_id):
        gravar(ids() - {game_id}, restaurado_em())


def resolver(encontrados: set[str]) -> None:
    """Depois de uma importação que varreu as fontes até o fim.

    Sai da lista o pendente que a varredura achou (``encontrados`` é o
    ``duplicate_game_ids`` da store) e o que não está mais na store com esse id:
    ele foi adotado sob um id novo, ou foi excluído.
    """
    restantes = []
    for game_id in ids():
        jogo = shared.store.get(game_id)
        if jogo is None or jogo.removed or game_id in encontrados:
            continue
        restantes.append(game_id)
    gravar(restantes, restaurado_em())


def pendentes() -> list[Game]:
    jogos = [jogo for game_id in ids() if (jogo := shared.store.get(game_id)) is not None]
    return sorted(jogos, key=lambda jogo: jogo.name.casefold())


def ids_que_dependem_de_atalho(pasta_games: Path) -> list[str]:
    """Os jogos de ``pasta_games`` que só abrem com um atalho: vivos, visíveis
    e de fonte de atalhos. Um manual (``imported``) ou um zerado não dependem
    de atalho nenhum para aparecer."""
    game_ids = []
    for arquivo in sorted(pasta_games.glob("*.json")):
        try:
            dados = json.loads(arquivo.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (
            isinstance(dados, dict)
            and isinstance(dados.get("game_id"), str)
            and dados.get("source") != "imported"
            and not dados.get("removed")
            and not dados.get("blacklisted")
        ):
            game_ids.append(dados["game_id"])
    return game_ids


def tem_historico(jogo: Game) -> bool:
    """Horas jogadas ou sessões. As duas, e não só as sessões: o tempo é
    gravado a cada minuto de jogo, e a sessão só entra no histórico quando
    termina bem. Um jogo cuja sessão caiu no meio tem horas e nenhuma sessão,
    e excluí-lo sem perguntar apagaria esse tempo."""
    return bool(jogo.playtime) or bool(session_log.load(jogo.game_id))


def resumo(jogo: Game) -> tuple[int, int, int]:
    """(segundos jogados, número de sessões, última vez jogado)."""
    return jogo.playtime or 0, len(session_log.load(jogo.game_id)), jogo.last_played or 0
