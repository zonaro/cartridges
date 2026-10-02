# tarefas.py
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

"""O que o app está fazendo em segundo plano, num lugar só.

Cada trabalho longo (importação, HowLongToBeat, metadados, tamanho em disco)
avisa aqui quando começa, a cada jogo e quando termina. O botão do canto da
biblioteca e a janela "Tarefas em andamento" só leem ``lista``: um trabalho
novo entra com três chamadas, sem mexer em nenhum dos dois.

Os trabalhos rodam em threads, e a lista é lida por widgets: toda mudança é
entregue na thread da tela. A ordem das entregas é a das chamadas, então um
``terminar()`` logo depois do ``comecar()`` nunca chega antes dele.
"""

from typing import Optional

from gi.repository import Gio, GObject

from cartridges.utils.na_tela import entregar_na_tela


class Tarefa(GObject.Object):
    """Um trabalho em andamento: o nome e quantos de quantos já foram."""

    __gtype_name__ = "Tarefa"

    nome = GObject.Property(type=str, default="")
    feitos = GObject.Property(type=int, default=0)
    total = GObject.Property(type=int, default=0)

    def atualizar(self, feitos: int, total: Optional[int] = None) -> None:
        entregar_na_tela(self._atualizar, feitos, total)

    def _atualizar(self, feitos: int, total: Optional[int]) -> bool:
        if total is not None:
            self.total = total
        self.feitos = feitos
        return False

    def terminar(self) -> None:
        entregar_na_tela(_remover, self)


lista = Gio.ListStore(item_type=Tarefa)


def comecar(nome: str, total: int) -> Tarefa:
    tarefa = Tarefa(nome=nome, total=total)
    entregar_na_tela(_adicionar, tarefa)
    return tarefa


def _adicionar(tarefa: Tarefa) -> bool:
    lista.append(tarefa)
    return False


def _remover(tarefa: Tarefa) -> bool:
    achou, posicao = lista.find(tarefa)
    if achou:
        lista.remove(posicao)
    return False
