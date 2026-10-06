# library_background.py
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

"""O fundo da grade de jogos: a arte do jogo em foco.

Focar um tile troca o fundo da biblioteca para uma captura do jogo, descendo
os degraus até achar uma — IGDB primeiro, depois as capturas e fanarts que o
TheGamesDB já guardou no jogo, depois o wallhaven. Quando nenhum degrau dá
resultado, devolve ``None`` e quem chamou não mexe em nada: em caso de falha,
não faz nada.

Tudo aqui é bloqueante (rede) e roda fora da thread de UI, como a sessão de
papel de parede faz em :mod:`cartridges.utils.session_wallpaper`. O módulo é
livre de GTK de propósito: ele resolve um arquivo em disco, e quem põe a
textura no ``Gtk.Picture`` é a janela, de volta na thread de UI. Nunca
levanta, pelo mesmo motivo — enfeite não pode quebrar navegação.
"""

import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional
from urllib.parse import urlparse

from cartridges import shared
from cartridges.utils.download import download_bytes
from cartridges.utils.wallhaven import IMAGE_SUFFIXES, melhor_para

if TYPE_CHECKING:
    from cartridges.game import Game

# O `.webp` aparece nas capturas do TheGamesDB; o resto é o que a sessão de
# parede já aceita.
_SUFIXOS = (*IMAGE_SUFFIXES, ".webp")

# Quantas URLs de cada lista tentar antes de descer o degrau. Seis é o que a
# página de detalhes guarda em cache; três fanarts bastam porque a primeira
# quase sempre baixa.
_MAX_CAPTURAS = 6
_MAX_FANARTS = 3


def _cache_dir(game_id: str) -> Path:
    return shared.cache_dir / "jolven" / "library-bg" / game_id


def _sufixo(url: str) -> str:
    sufixo = Path(urlparse(url).path).suffix.lower()
    return sufixo if sufixo in _SUFIXOS else ".jpg"


def _baixar(url: str, destino_dir: Path, timeout: float = 20) -> Optional[Path]:
    """Baixa ``url`` para o cache, reaproveitando o arquivo quando já existe."""
    nome = hashlib.sha256(url.encode("utf-8")).hexdigest() + _sufixo(url)
    destino = destino_dir / nome
    if destino.is_file():
        return destino
    conteudo = download_bytes(url, timeout=timeout)
    destino_dir.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(conteudo)
    return destino


def _tentar_urls(urls: list[str], destino_dir: Path) -> Optional[Path]:
    """A primeira URL que baixa, ou ``None`` quando nenhuma prestou."""
    for url in urls:
        try:
            return _baixar(str(url), destino_dir)
        except Exception as erro:  # pylint: disable=broad-except
            logging.info("Não foi possível baixar o fundo da biblioteca: %s", erro)
            continue
    return None


def _manual_nome(game_id: str) -> str:
    return f"{game_id}-fundo"


def _manual_arquivo(game_id: str) -> Optional[Path]:
    """O fundo escolhido à mão para ``game_id``, se ainda está em disco."""
    for sufixo in _SUFIXOS:
        caminho = shared.wallpapers_dir / f"{_manual_nome(game_id)}{sufixo}"
        if caminho.is_file():
            return caminho
    return None


def fundo_escolhido(game: "Game") -> Optional[Path]:
    """O fundo manual de ``game``, ou ``None`` quando está no automático.

    Aponta para um arquivo que sumiu? Volta ao automático em vez de ficar sem
    fundo em silêncio — a mesma regra da parede da sessão.
    """
    if not getattr(game, "fundo_biblioteca", None):
        return None
    return _manual_arquivo(game.game_id)


def salvar_fundo(game_id: str, origem: Path) -> Optional[Path]:
    """Adota ``origem`` como o fundo manual de ``game_id``.

    Devolve o destino em ``wallpapers_dir`` (o ``Game.fundo_biblioteca`` deve
    apontar para o nome dele) ou ``None`` quando não deu para guardar.
    """
    sufixo = origem.suffix.lower()
    if sufixo not in _SUFIXOS:
        sufixo = ".jpg"
    destino = shared.wallpapers_dir / f"{_manual_nome(game_id)}{sufixo}"
    try:
        shared.wallpapers_dir.mkdir(parents=True, exist_ok=True)
        for outro in _SUFIXOS:
            candidato = shared.wallpapers_dir / f"{_manual_nome(game_id)}{outro}"
            if candidato != destino:
                candidato.unlink(missing_ok=True)
        destino.write_bytes(origem.read_bytes())
    except OSError as erro:
        logging.warning("Não foi possível guardar o fundo escolhido: %s", erro)
        return None
    return destino


def redefinir_fundo(game_id: str) -> None:
    """Devolve o fundo de ``game_id`` ao automático."""
    for sufixo in _SUFIXOS:
        (shared.wallpapers_dir / f"{_manual_nome(game_id)}{sufixo}").unlink(
            missing_ok=True
        )


def resolver_fundo(
    game: "Game", largura: int = 1920, altura: int = 1080
) -> Optional[Path]:
    """O arquivo de imagem para o fundo com ``game`` em foco, ou ``None``.

    A escolha à mão vence tudo; depois descem os degraus IGDB → TheGamesDB
    (capturas, depois fanarts) → wallhaven. ``largura`` x ``altura`` é o
    mínimo que o fundo precisa, como o ``atleast`` da busca do wallhaven.
    Nunca levanta: sem imagem, o fundo fica como está.

    Bloqueante (rede): chamar fora da thread de UI.
    """
    try:
        if manual := fundo_escolhido(game):
            return manual

        destino_dir = _cache_dir(game.game_id)
        nome = game.name

        try:
            from cartridges.utils.igdb import screenshot_para
        except ImportError as erro:
            logging.info("Cliente IGDB indisponível: %s", erro)
        else:
            try:
                if url := screenshot_para(nome):
                    if achado := _tentar_urls([url], destino_dir):
                        return achado
            except Exception as erro:  # pylint: disable=broad-except
                logging.info("Fundo via IGDB falhou (%s): %s", nome, erro)

        capturas = list(getattr(game, "tgdb_screenshots", None) or [])[:_MAX_CAPTURAS]
        if capturas and (achado := _tentar_urls(capturas, destino_dir)):
            return achado

        fanarts = list(getattr(game, "tgdb_fanart", None) or [])[:_MAX_FANARTS]
        if fanarts and (achado := _tentar_urls(fanarts, destino_dir)):
            return achado

        if achado_w := melhor_para(nome, largura, altura, "landscape"):
            caminho = achado_w.get("path")
            if caminho and (
                baixado := _tentar_urls([str(caminho)], destino_dir)
            ):
                return baixado

        return None
    except Exception as erro:  # pylint: disable=broad-except
        logging.info("Não foi possível resolver o fundo da biblioteca: %s", erro)
        return None
