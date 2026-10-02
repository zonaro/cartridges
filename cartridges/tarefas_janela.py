# tarefas_janela.py
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

"""A janela "Tarefas em andamento": um bloco por tarefa, ao vivo.

Só lê o quadro (`utils/tarefas.py`). É uma janela de topo do app, e não um
diálogo preso à janela principal: pode ser arrastada, ir para trás da
biblioteca e ficar aberta enquanto o usuário segue usando o resto. Fica aberta
quando a última tarefa termina, com o aviso de que não há nada rodando: fechar
sozinha poderia sumir com ela debaixo do clique do usuário.

Com a janela principal escondida (ver `on_win_close_request` em `main.py`),
esta é a única janela à vista, e fechá-la encerra o app.
"""

from typing import Any, Optional

from gi.repository import Adw, GLib, Gtk

from cartridges import shared
from cartridges.utils import tarefas

# Espera entre o "map" e o posicionamento: a superfície e a altura da janela só
# existem depois dele.
_ESPERA_POSICAO_MS = 30


class TarefasJanela(Adw.Window):
    # A janela aberta agora, se houver: o botão do canto só a traz para a frente.
    aberta: Optional["TarefasJanela"] = None

    def __init__(self, application: Any = None) -> None:
        # Sem "transient-for" e sem "modal": é uma janela do Windows como outra
        # qualquer, com entrada própria na barra de tarefas. Não redimensionável,
        # a altura é a do conteúdo e a largura vem do pedido de largura mínima.
        super().__init__(
            title=_("Tarefas em andamento"),
            application=application,
            resizable=False,
            width_request=360,
            height_request=1,
        )

        self.caixa = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.vazio = Gtk.Label(label=_("Nenhuma tarefa em andamento"))
        self.vazio.add_css_class("dim-label")

        conteudo = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            margin_top=12,
            margin_bottom=24,
            margin_start=24,
            margin_end=24,
        )
        conteudo.append(self.caixa)
        conteudo.append(self.vazio)

        # Só minimizar e fechar: não há o que maximizar numa janela deste tamanho.
        self.cabecalho = Adw.HeaderBar(decoration_layout=":minimize,close")
        barra = Adw.ToolbarView()
        barra.add_top_bar(self.cabecalho)
        barra.set_content(conteudo)
        self.set_content(barra)

        # O Adw.Window, ao contrário do Adw.Dialog, não fecha com o Esc.
        atalhos = Gtk.ShortcutController()
        atalhos.add_shortcut(
            Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string("Escape"),
                Gtk.NamedAction.new("window.close"),
            )
        )
        self.add_controller(atalhos)

        # (objeto, id) de cada ligação: o quadro e as tarefas vivem mais que a
        # janela, e uma ligação esquecida seguraria os widgets depois de fechada.
        self._ligacoes: list[tuple[Any, int]] = []
        self._id_lista = tarefas.lista.connect("items-changed", self._redesenhar)
        self.connect("close-request", self._ao_fechar)
        self._redesenhar()

    @classmethod
    def mostrar(cls, win: Any) -> None:
        """Abre a janela acima do botão do canto, ou traz a que já está aberta."""
        if cls.aberta is not None:
            cls.aberta.present()
            return

        janela = cls(win.get_application())
        cls.aberta = janela
        # Invisível até ir para o lugar: sem isto ela piscaria onde o Windows a
        # põe antes de pular para o canto.
        janela.set_opacity(0)
        janela.connect(
            "map",
            lambda *_: GLib.timeout_add(_ESPERA_POSICAO_MS, janela._posicionar, win),
        )
        janela.present()

    def _posicionar(self, win: Any) -> bool:
        self.set_opacity(1)
        return GLib.SOURCE_REMOVE

    def _redesenhar(self, *_args: Any) -> None:
        self._desligar_tarefas()
        while (filho := self.caixa.get_first_child()) is not None:
            self.caixa.remove(filho)

        for posicao in range(tarefas.lista.get_n_items()):
            self.caixa.append(self._bloco(tarefas.lista.get_item(posicao)))
        self.vazio.set_visible(tarefas.lista.get_n_items() == 0)
        if self.get_mapped():
            self._ajustar_altura()

    def _ajustar_altura(self) -> None:
        # O GTK cresce uma janela não redimensionável quando o conteúdo cresce,
        # mas nunca a encolhe: sem isto, terminadas as tarefas ela ficaria alta,
        # com o aviso de vazio no topo e o resto em branco. Redimensionável por
        # um instante, ela recalcula a altura; o idle a trava de novo.
        self.set_resizable(True)
        self.set_default_size(360, 1)
        GLib.idle_add(self._travar)

    def _travar(self) -> bool:
        self.set_resizable(False)
        return GLib.SOURCE_REMOVE

    def _bloco(self, tarefa: tarefas.Tarefa) -> Gtk.Widget:
        bloco = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        nome = Gtk.Label(label=tarefa.nome, xalign=0)
        nome.add_css_class("heading")
        contagem = Gtk.Label(xalign=0)
        contagem.add_css_class("dim-label")
        barra = Gtk.ProgressBar()
        barra.update_property([Gtk.AccessibleProperty.LABEL], [tarefa.nome])
        bloco.append(nome)
        bloco.append(contagem)
        bloco.append(barra)

        def mostrar(*_args: Any) -> None:
            # As variáveis são quantos itens foram feitos e quantos há no total
            contagem.set_label(_("{} de {}").format(tarefa.feitos, tarefa.total))
            barra.set_fraction(tarefa.feitos / tarefa.total if tarefa.total else 0)

        for sinal in ("notify::feitos", "notify::total"):
            self._ligacoes.append((tarefa, tarefa.connect(sinal, mostrar)))
        mostrar()
        return bloco

    def _desligar_tarefas(self) -> None:
        for objeto, id_ in self._ligacoes:
            objeto.disconnect(id_)
        self._ligacoes.clear()

    def _ao_fechar(self, *_args: Any) -> bool:
        self._desligar_tarefas()
        tarefas.lista.disconnect(self._id_lista)
        if TarefasJanela.aberta is self:
            TarefasJanela.aberta = None
        # Com a janela principal escondida esta era a única janela à vista: fechá-la
        # é o fim do app.
        if shared.win is not None and not shared.win.get_visible():
            if (app := shared.win.get_application()) is not None:
                app.quit()
        return False
