# install_size.py
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

"""Quanto cada jogo ocupa no disco, medido em segundo plano.

A pergunta que isto responde é "preciso de espaço, o que eu apago?", e a
resposta já estava quase pronta na tela de detalhes: o status diz se o jogo foi
zerado, a data diz há quanto tempo ele não roda. Faltavam os bytes.

A pasta medida é a mesma que o rastreio de processo vigia
(:func:`install_dir_from_command`), e não a pasta do executável
(:func:`game_folder`, que é o que o botão de abrir pasta usa). A diferença
importa justamente aqui: um jogo que abre por ``SP\\bf6.exe`` mora na pasta
acima, e medir a que o comando nomeia contaria uma fração da instalação. A
regra tem a vantagem de já ser conhecida — se o app conta o seu tempo de jogo
sozinho, ele sabe o tamanho; se não conta, não sabe. Jogos abertos por URL de
loja (``steam://``, Epic, GOG) ficam sem tamanho, pelo mesmo motivo de sempre:
quem sabe onde eles estão é o lançador.

A varredura é uma só por execução do app, sequencial e sem pressa, no mesmo
molde do preenchimento do HowLongToBeat. Ela lê metadados de diretório, não
conteúdo, mas ainda assim é a coisa mais pesada que este app faz com o disco —
por isso um jogo medido há menos de uma semana é pulado, e por isso a varredura
espera a importação terminar antes de começar.
"""

import logging
import os
import threading
from time import time
from typing import Optional

from gi.repository import GLib

from cartridges import shared
from cartridges.game import Game
from cartridges.utils import tarefas
from cartridges.utils.game_folder import game_folder
from cartridges.utils.process_monitor import install_dir_from_command
from cartridges.utils.run_executable import aumid_from_command

# Startup is busy (window, disk load, and usually an auto-import right after).
_START_DELAY_SECONDS = 25
_IMPORT_RETRY_SECONDS = 30

# Uma medição vale uma semana. Um jogo cresce quando recebe um patch ou um DLC,
# o que não acontece entre dois cliques — e a alternativa, remedir tudo a cada
# início, torraria disco toda vez para mudar um número em dois ou três jogos.
_REFRESH_AFTER_SECONDS = 7 * 24 * 60 * 60


# Pastas que só existem para guardar a biblioteca inteira de alguém. Nenhum
# jogo tem uma destas dentro de si, e uma pasta que tem uma delas é onde os
# jogos moram, e não um jogo. É um subconjunto do `_CONTAINER_DIRS` do monitor
# de processos, de propósito: lá a lista pode ser generosa porque errar só
# custa deixar de vigiar uma pasta, aqui um nome ambíguo como "common" ou
# "games" apareceria dentro de algum jogo mais cedo ou mais tarde e o tamanho
# dele sumiria da tela sem explicação.
_LIBRARY_DIRS = frozenset({"steamapps", "xboxgames", "windowsapps"})


def install_size_folder(command: str) -> str:
    """A pasta a medir para ``command``, ou "" quando não dá para saber.

    Dois casos, na ordem: o executável clássico, cuja raiz o rastreio de
    processo já sabe achar, e o jogo empacotado (Microsoft Store / Game Pass),
    cuja pasta o Windows registra contra o pacote. Para o segundo,
    :func:`game_folder` devolve a raiz real da instalação — é só para ele que
    ela é chamada aqui, porque para um executável comum ela devolveria a pasta
    do exe, que pode ser uma subpasta do jogo.

    Sobra um caso que as duas erram: um atalho feito à mão para
    ``steam.exe -applaunch 440`` nomeia o cliente da Steam, e a pasta do
    cliente é onde *toda* a biblioteca Steam está instalada. Abrir essa pasta é
    uma resposta defensável (é o que o comando roda, e é o que o botão de pasta
    faz); dizer que o jogo ocupa 400 GB não é — ainda mais numa ordenação feita
    para decidir o que apagar, onde o número errado iria para o topo. Uma pasta
    que contém uma biblioteca inteira dentro de si é recusada.
    """
    if not (command or "").strip():
        return ""

    if aumid_from_command(command):
        folder = game_folder(command) or ""
    else:
        folder = install_dir_from_command(command)

    return "" if _holds_a_library(folder) else folder


def _holds_a_library(directory: str) -> bool:
    """``directory`` é o lugar onde ficam os jogos, em vez de ser um jogo?"""
    if not directory:
        return False

    try:
        with os.scandir(directory) as entries:
            return any(
                entry.name.casefold() in _LIBRARY_DIRS and entry.is_dir()
                for entry in entries
            )
    except OSError:
        # Ilegível: quem vai medir descobre a mesma coisa e devolve zero.
        return False


def folder_size(directory: str) -> int:
    """Soma o tamanho dos arquivos sob ``directory``, em bytes.

    O que não dá para ler é contado como zero em vez de derrubar a medição:
    uma pasta do Game Pass costuma negar acesso no meio do caminho, e meia
    medição continua sendo mais útil do que nenhuma.

    Links e junções não são seguidos. Um jogo cuja instalação real mora do
    outro lado de uma junção mede pequeno — mas seguir significaria contar duas
    vezes o que estiver dos dois lados, e correr o risco de andar em círculo.
    """
    total = 0
    stack = [directory]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        # follow_symlinks=False só filtra symlink de verdade;
                        # junção (mklink /J, criável sem privilégio) passa por
                        # ele e precisa do is_junction() para valer a promessa.
                        if entry.is_dir(follow_symlinks=False) and not entry.is_junction():
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


# Base 1024 com os rótulos curtos, que é o que o Explorer mostra: o número aqui
# existe para ser comparado com o de lá, e uma medida em base 1000 mostraria
# 94 GB onde o Windows mostra 87.
_UNITS = ("B", "KB", "MB", "GB", "TB")


def format_size(size: int) -> str:
    """Tamanho legível, com vírgula decimal ("87,4 GB")."""
    if size <= 0:
        return ""

    value = float(size)
    unit = 0
    while value >= 1024 and unit < len(_UNITS) - 1:
        value /= 1024
        unit += 1

    # Uma casa decimal só a partir de MB: "1,4 KB" é precisão que ninguém pediu,
    # e em bytes a fração não existe.
    if unit < 2:
        return f"{round(value)} {_UNITS[unit]}"

    rounded = round(value, 1)
    text = str(int(rounded)) if rounded.is_integer() else str(rounded).replace(".", ",")
    return f"{text} {_UNITS[unit]}"


class InstallSizeSweep:
    """Uma varredura em segundo plano pelo tamanho de cada jogo no disco."""

    def __init__(self) -> None:
        self._timeout_id: Optional[int] = None
        # Uma varredura por vez: `run_async` é alcançável pelo temporizador e
        # duas varreduras só fariam o mesmo trabalho duas vezes, no mesmo disco.
        self._lock = threading.Lock()
        self._running = False
        # Marcado ao sair. O trabalhador confere entre um jogo e outro — uma
        # medição em curso não dá para cancelar, então o resultado dela é
        # simplesmente descartado.
        self._stopped = False
        # Sobe a cada `stop()`. O trabalhador leva o número com que nasceu e
        # para quando ele muda: o `start()` logo depois do `stop()` (é o que o
        # reset faz) desfaz o `_stopped`, e o trabalhador antigo seguiria
        # medindo uma biblioteca que já não existe.
        self._generation = 0

    # -- agendamento ----------------------------------------------------------

    def start(self) -> None:
        """Arma a varredura. Chamada uma vez, no início do app."""
        self._stopped = False
        if self._timeout_id is not None:
            GLib.source_remove(self._timeout_id)
        self._timeout_id = GLib.timeout_add_seconds(
            _START_DELAY_SECONDS, self._on_timer
        )

    def stop(self) -> None:
        """Cancela uma varredura pendente e desliga uma em andamento."""
        self._stopped = True
        self._generation += 1
        if self._timeout_id is not None:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = None

    def _on_timer(self) -> bool:
        self._timeout_id = None
        if self._stopped:
            return False

        # A importação já mexe no disco (e adiciona jogos que esta varredura
        # teria de medir depois de qualquer jeito). Espera ela acabar.
        app = shared.win.get_application() if shared.win is not None else None
        if app is not None and app.state == shared.AppState.IMPORT:
            self._timeout_id = GLib.timeout_add_seconds(
                _IMPORT_RETRY_SECONDS, self._on_timer
            )
            return False

        self.run_async()
        return False

    # -- a varredura ----------------------------------------------------------

    def run_async(self) -> None:
        """Começa uma varredura fora do thread principal, se não houver uma."""
        if self._stopped:
            return

        # Instantâneo no thread principal: os mapeamentos do store mudam com uma
        # importação, e iterá-los de um trabalhador arriscaria estourar no meio.
        games = [
            game
            for game in shared.store
            if not game.removed and not game.blacklisted and self._is_stale(game)
        ]
        if not games:
            return

        with self._lock:
            if self._running:
                return
            self._running = True

        logging.info("Install size sweep queued for %d games", len(games))
        threading.Thread(
            target=self._worker, args=(games, self._generation), daemon=True
        ).start()

    @staticmethod
    def _is_stale(game: Game) -> bool:
        """Este jogo precisa ser medido agora?"""
        return (int(time()) - (game.install_size_ts or 0)) >= _REFRESH_AFTER_SECONDS

    def _worker(self, games: list[Game], generation: int) -> None:
        measured = 0
        tarefa: Optional[tarefas.Tarefa] = None
        try:
            # Primeiro a pasta de cada um. Sem pasta conhecida não há o que
            # medir — nem o que mostrar nas tarefas: um jogo aberto pela loja
            # passaria pela fila em milissegundos a cada abertura, e o botão do
            # canto piscaria por nada.
            com_pasta: list[tuple[Game, str]] = []
            for game in games:
                if self._stopped or generation != self._generation:
                    return
                # Reconferido por jogo: uma importação pode ter removido este
                # daqui até a vez dele chegar.
                if game.removed or not self._is_stale(game):
                    continue
                folder = install_size_folder(game.executable)
                if folder:
                    com_pasta.append((game, folder))
                elif game.install_size:
                    GLib.idle_add(self._apply, game, 0)
            if not com_pasta:
                return

            tarefa = tarefas.comecar(_("Tamanho em disco"), len(com_pasta))
            for feitos, (game, folder) in enumerate(com_pasta):
                tarefa.atualizar(feitos)
                if self._stopped or generation != self._generation:
                    break
                if game.removed:
                    continue

                # ponytail: uma pasta por vez, sem paralelismo. Uma biblioteca
                # muito grande leva minutos na primeira execução; se incomodar,
                # o caminho é medir só o que a tela vai mostrar, não abrir mais
                # threads em cima do mesmo disco.
                # Pasta vazia ou ilegível inteira é jogo que saiu do disco ou
                # instalação que não dá para ler: o tamanho vira desconhecido
                # (zero, que a tela não mostra), porque um número antigo ficaria
                # no topo do "o que apagar" por um jogo que já não ocupa nada.
                size = folder_size(folder)
                if not size:
                    if game.install_size:
                        GLib.idle_add(self._apply, game, 0)
                    continue

                measured += 1
                GLib.idle_add(self._apply, game, size)
        finally:
            if tarefa is not None:
                tarefa.terminar()
            with self._lock:
                self._running = False
            logging.info(
                "Install size sweep done: %d of %d games measured",
                measured,
                len(games),
            )

    # -- aplicando os resultados (thread principal) ---------------------------

    def _apply(self, game: Game, size: int) -> bool:
        """Grava o tamanho de um jogo e o repinta. Roda no thread principal."""
        if self._stopped or game.removed:
            return False
        # Identidade no store, não só o snapshot: um reset apaga a biblioteca
        # enquanto o worker anda pela lista dele, e o `save()` abaixo
        # regravaria o JSON de um jogo recém-apagado. Mesmo idioma do
        # `_in_library` do MetadataRefresh e do HLTBBackfill.
        store = getattr(shared, "store", None)
        if store is None or store.get(game.game_id) is not game:
            return False

        game.install_size = size
        # Sem carimbo quando o tamanho some: sem ele, a próxima varredura mede
        # de novo — um disco externo desligado volta a ter tamanho quando
        # volta, e não uma semana depois.
        if size:
            game.install_size_ts = int(time())
        game.save()

        win = shared.win
        if win is None:
            return False

        # A tela de detalhes desenha o tamanho quando é aberta; um jogo medido
        # enquanto ele está aberto ficaria sem a linha até sair e voltar.
        if (
            getattr(win, "active_game", None) is game
            and win.navigation_view.get_visible_page() == win.details_page
        ):
            win.update_install_size_label(game)

        # A ordenação por tamanho lê o valor que acabou de mudar. Sem isto, a
        # grade fica na ordem que a varredura encontrou, que é a de antes dela.
        if win.sort_state == "install_size":
            win.library.invalidate_sort()
            win.zerados_library.invalidate_sort()
        return False

