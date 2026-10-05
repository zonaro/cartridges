# igdb.py
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

"""Capturas de tela de jogos via IGDB.

A API v4 exige duas credenciais que moram nas preferências (``igdb-client-id``
e ``igdb-client-secret``): o ``client_id`` identifica o app e o ``secret``
troca um token efêmero no Twitch (OAuth ``client_credentials``), que é o que
vai no ``Authorization: Bearer`` de cada consulta. Sem as duas, nada aqui
funciona — e está tudo bem: quem chama (:func:`screenshot_para`) recebe
``None`` e cai no próximo degrau (TheGamesDB, wallhaven).

O token vive só em memória, com a validade que o Twitch devolve menos um
minuto de folga. Não vai para disco, nem para log, pelo mesmo motivo da chave
do wallhaven não ir para a URL: credencial em repouso é credencial vazada.

Tudo que toca rede pode falhar, e o ponto de chamada é o foco de um tile na
grade — um tropeço ali não pode virar erro na cara de ninguém. Por isso
:func:`screenshot_para` nunca levanta: qualquer exceção vira ``logging.info``
e ``None`` (em caso de falha, não faz nada).
"""

import logging
import time
from typing import Any, Optional

import requests

from cartridges import shared
from cartridges.utils.download import get_capped
from cartridges.utils.title_match import CONFIDENT_SCORE, rank_candidates

_JOGOS_URL = "https://api.igdb.com/v4/games"
_CAPTURAS_URL = "https://api.igdb.com/v4/screenshots"
_TOKEN_URL = "https://id.twitch.tv/oauth2/token"

# O que a URL crua traz (``t_thumb``) é miniatura; a ``t_1080p`` é a maior
# variação que o CDN garante para capturas.
_IMAGEM_GRANDE = "t_1080p"

_token: Optional[str] = None
_token_expira_em: float = 0.0


class IGDBError(Exception):
    """A consulta não pôde ser feita ou veio num formato inesperado."""


class IGDBAuthError(IGDBError):
    """Faltam credenciais ou elas foram recusadas."""


def _credenciais() -> tuple[str, str]:
    """O ``(client_id, client_secret)`` salvo, ou erro quando incompleto.

    :raises IGDBAuthError: chave ausente, vazia ou esquema fora do app
    """
    try:
        client_id = shared.schema.get_string("igdb-client-id").strip()
        secret = shared.schema.get_string("igdb-client-secret").strip()
    except Exception as erro:
        raise IGDBAuthError("sem credenciais salvas") from erro
    if not client_id or not secret:
        raise IGDBAuthError("sem credenciais salvas")
    return client_id, secret


def _token_valido(client_id: str, secret: str, timeout: float) -> str:
    """Um token Bearer do Twitch, renovando quando expirou.

    :raises IGDBAuthError: credenciais recusadas
    :raises IGDBError: rede fora ou resposta inesperada
    """
    global _token, _token_expira_em
    if _token and time.monotonic() < _token_expira_em:
        return _token
    try:
        resposta = get_capped(
            _TOKEN_URL,
            params={
                "client_id": client_id,
                "client_secret": secret,
                "grant_type": "client_credentials",
            },
            timeout=timeout,
        )
        resposta.raise_for_status()
        dados = resposta.json()
    except requests.RequestException as erro:
        status = getattr(getattr(erro, "response", None), "status_code", None)
        if status in (400, 401, 403):
            raise IGDBAuthError("credenciais recusadas") from None
        raise IGDBError("falha ao autenticar") from None
    except ValueError as erro:
        raise IGDBError("resposta não era JSON") from erro
    if not isinstance(dados, dict) or not dados.get("access_token"):
        raise IGDBError("resposta sem token")
    try:
        folga = float(dados.get("expires_in", 0)) - 60
    except (TypeError, ValueError):
        folga = 0
    _token = str(dados["access_token"])
    _token_expira_em = time.monotonic() + max(folga, 0)
    return _token


def _consultar(
    url: str, corpo: str, client_id: str, token: str, timeout: float
) -> Any:
    """Uma consulta Apicalypse, já decodificada.

    :raises IGDBAuthError: token recusado
    :raises IGDBError: rede fora ou resposta inesperada
    """
    try:
        resposta = get_capped(
            url,
            data=corpo.encode("utf-8"),
            headers={"Client-ID": client_id, "Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
        if resposta.status_code in (401, 403):
            raise IGDBAuthError("consulta recusada")
        resposta.raise_for_status()
        return resposta.json()
    except IGDBAuthError:
        raise
    except requests.RequestException as erro:
        raise IGDBError("consulta falhou") from None
    except ValueError as erro:
        raise IGDBError("resposta não era JSON") from erro


class IGDBClient:
    """Busca capturas de tela usando as chaves das preferências."""

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
    ) -> None:
        if client_id is None or client_secret is None:
            client_id, client_secret = _credenciais()
        self.client_id = client_id
        self.client_secret = client_secret

    def id_do_jogo(self, nome: str, timeout: float = 15) -> int:
        """O id IGDB do jogo que mais parece ``nome``.

        :raises IGDBNotFound: sem candidato confiante
        :raises IGDBError: rede fora, resposta inesperada ou sem credenciais
        """
        token = _token_valido(self.client_id, self.client_secret, timeout)
        jogos = _consultar(
            _JOGOS_URL,
            f'search "{nome}"; fields id,name; limit 10;',
            self.client_id,
            token,
            timeout,
        )
        candidatos = [
            jogo for jogo in jogos or [] if isinstance(jogo, dict) and jogo.get("id")
        ]
        ranked = rank_candidates(nome, candidatos)
        if not ranked or ranked[0][1].score < CONFIDENT_SCORE:
            raise IGDBNotFound(nome)
        return int(ranked[0][0]["id"])

    def capturas(self, jogo_id: int | str, timeout: float = 15) -> list[str]:
        """As URLs das capturas de ``jogo_id``, da maior para a menor variação.

        :raises IGDBError: rede fora ou resposta inesperada
        """
        token = _token_valido(self.client_id, self.client_secret, timeout)
        itens = _consultar(
            _CAPTURAS_URL,
            f"where game = {jogo_id}; fields url; limit 10;",
            self.client_id,
            token,
            timeout,
        )
        urls = []
        for item in itens or []:
            if not isinstance(item, dict) or not item.get("url"):
                continue
            url = str(item["url"])
            if url.startswith("//"):
                url = f"https:{url}"
            # Troca a variação da miniatura pela grande; se a URL não tem
            # variação nenhuma, segue como veio.
            if "/t_thumb/" in url:
                url = url.replace("/t_thumb/", f"/{_IMAGEM_GRANDE}/")
            elif "/t_cover_small/" in url or "/t_logo_med/" in url:
                url = url.rsplit("/", 1)[0] + f"/{_IMAGEM_GRANDE}/" + url.rsplit("/", 1)[1]
            urls.append(url)
        return urls


class IGDBNotFound(IGDBError):
    """Nenhum jogo confiante para o nome pedido."""


def screenshot_para(nome: str, timeout: float = 15) -> Optional[str]:
    """A primeira captura do jogo que mais parece ``nome``, ou ``None``.

    É o primeiro degrau do fundo da biblioteca (depois vêm TheGamesDB e
    wallhaven). Nunca levanta: sem credenciais, sem rede ou sem jogo
    confiante, devolve ``None`` e quem chamou tenta o próximo — em caso de
    falha, não faz nada.
    """
    try:
        cliente = IGDBClient()
        jogo_id = cliente.id_do_jogo(nome, timeout=timeout)
        capturas = cliente.capturas(jogo_id, timeout=timeout)
    except Exception as erro:  # pylint: disable=broad-except
        logging.info("Busca no IGDB falhou (%s): %s", nome, erro)
        return None
    return capturas[0] if capturas else None
