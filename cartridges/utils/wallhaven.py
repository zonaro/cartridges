# wallhaven.py
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

"""Busca de papéis de parede no wallhaven.cc.

A chave da API é opcional: a busca SFW responde sem autenticação nenhuma, e a
chave só serve para NSFW e para os filtros da conta de quem a informou. Por
isso ela mora nas preferências como um extra, e não como um pré-requisito — ao
contrário do SteamGridDB, cuja tela fica inerte sem chave.

O que este módulo NÃO faz é escolher formato. Medido nos 92 jogos de uma
biblioteca real, só 21% têm papel de parede em retrato no site: filtrar por um
formato só e parar aí deixaria jogos sem imagem. Então a busca vai em degraus —
o formato pedido primeiro, qualquer formato depois — e é o corte
(:func:`cartridges.utils.session_wallpaper.enquadrar`) que resolve a
diferença. Quem chama decide por qual formato começar.
"""

import logging
import re
from typing import Any, Optional
from urllib.parse import urlencode

import requests

from cartridges import shared
from cartridges.utils.download import get_capped

BASE = "https://wallhaven.cc/api/v1/search"

# Só "general". A categoria "anime" foi testada e saiu: ela não traz mais arte
# DE JOGO, traz fan art de personagem no traço de anime — comparadas lado a
# lado numa busca real, as duas devolvem retrato de personagem, e a segunda
# apenas troca o render realista pelo desenhado. "people" nunca esteve aqui:
# ali o que existe são ensaios e cosplay.
CATEGORIES = "100"

# Sempre SFW. A chave da conta pode liberar o resto no site; um papel de parede
# que aparece sozinho em três monitores quando um jogo abre não é lugar para
# isso.
PURITY = "100"

# Sufixos que o site publica. O `path` sempre traz um deles.
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")

# Só o que a tela de escolha e o corte consomem. O resto da resposta (cores,
# tags, uploader) é grande e não é usado em lugar nenhum.
_CAMPOS = ("id", "path", "resolution", "dimension_x", "dimension_y", "favorites")

# Edições e remasterizações não mudam a arte do jogo, e carregadas na busca
# deixam títulos inteiros sem resultado ("Conan Exiles Enhanced" volta vazio,
# "Conan Exiles" não).
_SUFIXO_EDICAO = re.compile(
    r"\s*[-–]?\s*\(?\b("
    r"game of the year|goty|definitive|anniversary|enhanced|remastered|"
    r"complete|deluxe|ultimate|legendary|redux"
    r")\b\s*(edition)?\)?\s*$",
    re.IGNORECASE,
)


class WallhavenError(Exception):
    """A busca não pôde ser feita ou veio num formato inesperado."""


def consultas(nome: str) -> list[str]:
    """As formas de procurar ``nome``, da mais fiel à mais solta.

    O terceiro degrau corta o subtítulo, e é onde mora a única armadilha:
    "Aliens: Dark Descent" vira "Aliens", que traz o filme, não o jogo. Daí a
    guarda de duas palavras — ela derruba justamente os casos ruins
    ("Alien", "Vampire", "Commandos") e preserva os bons ("Watch Dogs 2",
    "The Outer Worlds").
    """
    limpo = re.sub(r"[™®©]", "", nome)
    limpo = limpo.replace("_", " ").replace("’", "'")
    limpo = re.sub(r"\s+", " ", limpo).strip()

    formas = [limpo]

    sem_edicao = _SUFIXO_EDICAO.sub("", limpo).strip()
    if sem_edicao and sem_edicao != limpo:
        formas.append(sem_edicao)

    base = re.split(r"\s*(?::|\s[-–]\s)\s*", sem_edicao or limpo)[0].strip()
    if base and base not in formas and len(base.split()) >= 2:
        formas.append(base)

    return formas


def buscar(
    consulta: str,
    largura: int,
    altura: int,
    formato: Optional[str] = None,
    pagina: int = 1,
    timeout: float = 15,
) -> list[dict[str, Any]]:
    """Uma página de resultados para ``consulta``, do mais favoritado ao menos.

    ``largura`` e ``altura`` são o mínimo que os cortes dos monitores-alvo
    precisam: entram como ``atleast`` para nenhum candidato ser ampliado depois
    do corte. Um 1920x1080 não passa nesse filtro com um monitor em pé — e é
    isso que se quer, porque dele sairia um recorte de 607px esticado para 1080.

    ``formato`` é o ``ratios`` do site (``"portrait"`` ou ``"landscape"``);
    ``None`` aceita qualquer um.

    :raises WallhavenError: rede fora, resposta não-JSON ou chave recusada
    """
    parametros = {
        "q": consulta,
        "categories": CATEGORIES,
        "purity": PURITY,
        "sorting": "relevance",
        "atleast": f"{largura}x{altura}",
        "page": str(pagina),
    }
    if formato:
        parametros["ratios"] = formato

    cabecalhos = {}
    if chave := shared.schema.get_string("wallhaven-key").strip():
        # No cabeçalho, e não no `?apikey=`: a URL vai parar em log de proxy e
        # em histórico de erro, e a chave junto.
        cabecalhos["X-API-Key"] = chave

    try:
        resposta = get_capped(
            f"{BASE}?{urlencode(parametros)}", headers=cabecalhos, timeout=timeout
        )
        resposta.raise_for_status()
        dados = resposta.json()
    except requests.RequestException as erro:
        raise WallhavenError(str(erro)) from erro
    except ValueError as erro:
        raise WallhavenError("resposta não era JSON") from erro

    itens = dados.get("data") if isinstance(dados, dict) else None
    if not isinstance(itens, list):
        raise WallhavenError("resposta sem lista de resultados")

    resultados = []
    for item in itens:
        if not isinstance(item, dict) or not item.get("path"):
            continue
        registro = {campo: item.get(campo) for campo in _CAMPOS}
        miniaturas = item.get("thumbs")
        # A miniatura "large" (432x243, ~23 KB) preserva a proporção original;
        # a "small" já vem cortada em 3x2 pelo site e mentiria sobre o que o
        # corte vai fazer.
        registro["thumb"] = (
            miniaturas.get("large") if isinstance(miniaturas, dict) else None
        ) or item["path"]
        resultados.append(registro)

    # Relevância decide QUAIS 24 chegam; dentro deles, o mais favoritado é o
    # melhor palpite de qualidade que o site oferece.
    resultados.sort(key=lambda item: item.get("favorites") or 0, reverse=True)
    return resultados


def melhor_para(
    nome: str, largura: int, altura: int, formato: str
) -> Optional[dict[str, Any]]:
    """O melhor candidato para ``nome``, descendo os degraus até achar um.

    Em cada forma do nome, ``formato`` primeiro e qualquer formato depois.
    Devolve ``None`` quando nenhum degrau deu resultado — e aí quem chamou cai
    na capa do jogo. Nunca levanta: a escolha automática roda enquanto o jogo
    abre, e um tropeço de rede ali não pode virar erro na cara de ninguém.
    """
    for consulta in consultas(nome):
        for degrau in (formato, None):
            try:
                achados = buscar(consulta, largura, altura, formato=degrau)
            except WallhavenError as erro:
                logging.info("Busca no wallhaven falhou (%s): %s", consulta, erro)
                continue
            if achados:
                return achados[0]
    return None
