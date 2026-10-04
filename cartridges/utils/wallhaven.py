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
chave só serve para sketchy/NSFW e para os filtros da conta de quem a informou.
Por isso ela mora nas preferências como um extra, e não como um pré-requisito —
ao contrário do SteamGridDB, cuja tela fica inerte sem chave.

Todos os parâmetros oficiais da busca (``/api/v1/search``) têm suporte aqui:
categorias (general/anime/people), pureza (sfw/sketchy/nsfw), ordenação, ordem,
alcance do toplist, cor dominante, resoluções exatas, proporções, ``seed`` e
página. O que a tela de escolha não expõe (resoluções e proporções) continua
automático: o ``atleast`` e o ``ratios`` saem da geometria dos monitores-alvo,
e é o corte (:func:`cartridges.utils.session_wallpaper.enquadrar`) que resolve
a diferença. Quem chama decide por qual formato começar.

O que este módulo NÃO faz é escolher formato. Medido nos 92 jogos de uma
biblioteca real, só 21% têm papel de parede em retrato no site: filtrar por um
formato só e parar aí deixaria jogos sem imagem. Então a busca vai em degraus —
o formato pedido primeiro, qualquer formato depois. A escolha automática
(:func:`melhor_para`) roda sozinha quando o jogo abre e por isso fica sempre
no seguro (general + SFW + relevância); os filtros guardados valem para a
escolha à mão, onde cada imagem passa pelo olho antes de ir para a parede.
"""

import logging
import random
import re
import string
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlencode

import requests

from cartridges import shared
from cartridges.utils.download import get_capped

BASE = "https://wallhaven.cc/api/v1/search"

# Os três bits de categoria do site, nesta ordem: general, anime, people. Só
# "general" por padrão: a categoria "anime" foi testada e saiu — ela não traz
# mais arte DE JOGO, traz fan art de personagem no traço de anime —, e em
# "people" o que existe são ensaios e cosplay. A escolha à mão libera as três.
CATEGORIAS_PADRAO = "100"

# Sempre SFW por padrão. A chave da conta pode liberar o resto no site; um
# papel de parede que aparece sozinho em três monitores quando um jogo abre
# não é lugar para isso. A escolha à mão libera sketchy e NSFW com chave.
PUREZA_PADRAO = "100"

ORDENACAO_PADRAO = "relevance"
ORDEM_PADRAO = "desc"
ALCANCE_PADRAO = "1M"

# Tudo que o `/api/v1/search` aceita em `sorting`, na ordem da busca do site.
ORDENACOES = (
    "relevance",
    "date_added",
    "views",
    "favorites",
    "toplist",
    "random",
    "hot",
)

ORDENS = ("desc", "asc")

# Só vale com `sorting=toplist`; fora daí o site ignora.
ALCANCES_TOPLIST = ("1d", "3d", "1w", "1M", "3M", "6M", "1y")

# A paleta fixa que o site oferece no filtro de cor (hex sem `#`, como a API
# recebe em `colors`).
CORES = (
    "660000",
    "990000",
    "cc0000",
    "cc3333",
    "ea4c88",
    "993399",
    "663399",
    "333399",
    "0066cc",
    "0099cc",
    "66cccc",
    "77cc33",
    "669900",
    "336600",
    "666600",
    "999900",
    "cccc33",
    "ffff00",
    "ffcc33",
    "ff9900",
    "ff6600",
    "cc6633",
    "996633",
    "663300",
    "000000",
    "999999",
    "cccccc",
    "ffffff",
    "424153",
)

_FLAGS = re.compile(r"^[01]{3}$")
_COR = re.compile(r"^[0-9a-fA-F]{6}$")
_SEED = re.compile(r"^[a-zA-Z0-9]{6}$")


def tem_chave() -> bool:
    """Se há chave da API salva — o que libera sketchy e NSFW no site."""
    try:
        return bool(shared.schema.get_string("wallhaven-key").strip())
    except Exception:  # esquema fora do app instalado (testes): sem chave
        return False


def gerar_seed() -> str:
    """Um ``seed`` válido para paginar a ordem aleatória sem repetir."""
    return "".join(random.choices(string.ascii_letters + string.digits, k=6))


@dataclass(frozen=True)
class Filtros:
    """Os filtros da busca, como a API recebe.

    ``categorias`` e ``pureza`` são os três bits do site (general/anime/people
    e sfw/sketchy/nsfw); ``cores`` é um hex sem `#` ou vazio para qualquer cor.
    Congelado para a tela de escolha tratar como valor: mudou um controle,
    troca o objeto inteiro.
    """

    categorias: str = CATEGORIAS_PADRAO
    pureza: str = PUREZA_PADRAO
    ordenacao: str = ORDENACAO_PADRAO
    ordem: str = ORDEM_PADRAO
    alcance: str = ALCANCE_PADRAO
    cores: str = ""

    def __post_init__(self) -> None:
        if not _FLAGS.match(self.categorias) or self.categorias == "000":
            raise ValueError(f"categorias inválidas: {self.categorias!r}")
        if not _FLAGS.match(self.pureza) or self.pureza == "000":
            raise ValueError(f"pureza inválida: {self.pureza!r}")
        if self.ordenacao not in ORDENACOES:
            raise ValueError(f"ordenação inválida: {self.ordenacao!r}")
        if self.ordem not in ORDENS:
            raise ValueError(f"ordem inválida: {self.ordem!r}")
        if self.alcance not in ALCANCES_TOPLIST:
            raise ValueError(f"alcance inválido: {self.alcance!r}")
        if self.cores and not _COR.match(self.cores):
            raise ValueError(f"cor inválida: {self.cores!r}")

    @property
    def restrito(self) -> bool:
        """Se pede sketchy ou NSFW — o que exige a chave da API."""
        return self.pureza != "100"


# O que a escolha automática usa: fixo no seguro, sem ler preferência nenhuma.
# Ler os filtros guardados aqui seria exibir na parede, sem ninguém olhando,
# uma imagem que o usuário escolheu ver a sós na tela de escolha.
FILTROS_AUTOMATICOS = Filtros()


def ler_filtros() -> Filtros:
    """Os filtros guardados nas preferências, higienizados.

    Valor ausente ou inválido volta ao padrão; sketchy/NSFW sem chave voltam
    a SFW. Nunca levanta: preferência corrompida não pode quebrar a busca.
    """
    try:
        chaves = {
            nome: shared.schema.get_string(f"wallhaven-{nome}")
            for nome in ("categorias", "pureza", "ordenacao", "ordem", "alcance", "cores")
        }
    except Exception:
        return FILTROS_AUTOMATICOS
    if not tem_chave() and chaves["pureza"] not in ("", "100"):
        chaves["pureza"] = "100"
    try:
        return Filtros(
            categorias=chaves["categorias"] or CATEGORIAS_PADRAO,
            pureza=chaves["pureza"] or PUREZA_PADRAO,
            ordenacao=chaves["ordenacao"] or ORDENACAO_PADRAO,
            ordem=chaves["ordem"] or ORDEM_PADRAO,
            alcance=chaves["alcance"] or ALCANCE_PADRAO,
            cores=(chaves["cores"] or "").lstrip("#"),
        )
    except ValueError:
        return FILTROS_AUTOMATICOS


def salvar_filtros(filtros: Filtros) -> None:
    """Guarda ``filtros`` nas preferências para a próxima abertura."""
    shared.schema.set_string("wallhaven-categorias", filtros.categorias)
    shared.schema.set_string("wallhaven-pureza", filtros.pureza)
    shared.schema.set_string("wallhaven-ordenacao", filtros.ordenacao)
    shared.schema.set_string("wallhaven-ordem", filtros.ordem)
    shared.schema.set_string("wallhaven-alcance", filtros.alcance)
    shared.schema.set_string("wallhaven-cores", filtros.cores)

# Sufixos que o site publica. O `path` sempre traz um deles.
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")

# Só o que a tela de escolha e o corte consomem. O resto da resposta (tags,
# uploader) é grande e não é usado em lugar nenhum; `purity` e `category` ficam
# porque a grade pode precisar dizer o que cada imagem é.
_CAMPOS = (
    "id",
    "path",
    "resolution",
    "dimension_x",
    "dimension_y",
    "favorites",
    "purity",
    "category",
)

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


def parametros(
    consulta: str,
    largura: int,
    altura: int,
    formato: Optional[str] = None,
    pagina: int = 1,
    filtros: Optional[Filtros] = None,
    seed: Optional[str] = None,
    resolucoes: str = "",
    proporcoes: str = "",
) -> dict[str, str]:
    """Os parâmetros da URL de busca, prontos para o ``urlencode``.

    ``filtros`` ausente é o seguro (general + SFW + relevância). ``seed`` só
    entra com ordem aleatória; ``topRange`` só com toplist; ``resolucoes`` e
    ``proporcoes`` são listas com vírgula da API (`1920x1080,…` e `16x9,…`).
    ``atleast`` e o ``ratios`` do formato continuam saindo da geometria dos
    monitores — quem chama passa o mínimo que os cortes precisam.

    :raises WallhavenError: sketchy/NSFW sem chave salva
    :raises ValueError: ``seed`` fora do formato de seis alfanuméricos
    """
    atual = filtros or FILTROS_AUTOMATICOS
    if atual.restrito and not tem_chave():
        raise WallhavenError(
            "sketchy e NSFW exigem a chave da API nas preferências"
        )
    if seed is not None and not _SEED.match(seed):
        raise ValueError(f"seed inválida: {seed!r}")

    saidos = {
        "q": consulta,
        "categories": atual.categorias,
        "purity": atual.pureza,
        "sorting": atual.ordenacao,
        "order": atual.ordem,
        "atleast": f"{largura}x{altura}",
        "page": str(pagina),
    }
    if formato:
        saidos["ratios"] = formato
    if proporcoes:
        saidos["ratios"] = (
            f"{saidos['ratios']},{proporcoes}" if "ratios" in saidos else proporcoes
        )
    if resolucoes:
        saidos["resolutions"] = resolucoes
    if atual.ordenacao == "toplist":
        saidos["topRange"] = atual.alcance
    if atual.ordenacao == "random" and seed:
        saidos["seed"] = seed
    if atual.cores:
        saidos["colors"] = atual.cores.lower()
    return saidos


def buscar(
    consulta: str,
    largura: int,
    altura: int,
    formato: Optional[str] = None,
    pagina: int = 1,
    timeout: float = 15,
    filtros: Optional[Filtros] = None,
    seed: Optional[str] = None,
    resolucoes: str = "",
    proporcoes: str = "",
) -> list[dict[str, Any]]:
    """Uma página de resultados para ``consulta``, do mais favoritado ao menos.

    ``largura`` e ``altura`` são o mínimo que os cortes dos monitores-alvo
    precisam: entram como ``atleast`` para nenhum candidato ser ampliado depois
    do corte. Um 1920x1080 não passa nesse filtro com um monitor em pé — e é
    isso que se quer, porque dele sairia um recorte de 607px esticado para 1080.

    ``formato`` é o ``ratios`` do site (``"portrait"`` ou ``"landscape"``);
    ``None`` aceita qualquer um. ``filtros`` ausente é o seguro; a tela de
    escolha passa os filtros guardados, a automática nem precisa.

    :raises WallhavenError: rede fora, resposta não-JSON, chave recusada ou
        sketchy/NSFW sem chave salva
    """
    saidos = parametros(
        consulta,
        largura,
        altura,
        formato,
        pagina,
        filtros,
        seed,
        resolucoes,
        proporcoes,
    )

    cabecalhos = {}
    if chave := shared.schema.get_string("wallhaven-key").strip():
        # No cabeçalho, e não no `?apikey=`: a URL vai parar em log de proxy e
        # em histórico de erro, e a chave junto.
        cabecalhos["X-API-Key"] = chave

    try:
        resposta = get_capped(
            f"{BASE}?{urlencode(saidos)}", headers=cabecalhos, timeout=timeout
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
    nome: str,
    largura: int,
    altura: int,
    formato: str,
    filtros: Optional[Filtros] = None,
) -> Optional[dict[str, Any]]:
    """O melhor candidato para ``nome``, descendo os degraus até achar um.

    Em cada forma do nome, ``formato`` primeiro e qualquer formato depois.
    Devolve ``None`` quando nenhum degrau deu resultado — e aí quem chamou cai
    na capa do jogo. Nunca levanta: a escolha automática roda enquanto o jogo
    abre, e um tropeço de rede ali não pode virar erro na cara de ninguém.
    Sem ``filtros``, fica no seguro (general + SFW + relevância).
    """
    atual = filtros or FILTROS_AUTOMATICOS
    semente = gerar_seed() if atual.ordenacao == "random" else None
    for consulta in consultas(nome):
        for degrau in (formato, None):
            try:
                achados = buscar(
                    consulta, largura, altura, formato=degrau, filtros=atual, seed=semente
                )
            except WallhavenError as erro:
                logging.info("Busca no wallhaven falhou (%s): %s", consulta, erro)
                continue
            if achados:
                return achados[0]
    return None
