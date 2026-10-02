# spring_scroll.py
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

"""Rolagem com inércia de mola para a roda do mouse.

O GTK move a roda em degraus discretos. Aqui cada entalhe vira um empurrão numa
AdwSpringAnimation sobre o `value` do GtkAdjustment: o movimento arranca rápido
e assenta devagar, como um deslize num celular.

O encadeamento é o ponto. Uma mola guarda velocidade, então um entalhe que chega
enquanto a anterior ainda corre não reinicia o movimento — ele SOMA velocidade à
que já existe, e a física resolve o resto. É isso que faz girar a roda com força
jogar mais longe que girar devagar, sem tabela de multiplicadores nem teto.

Só a roda passa por aqui. Touchpad e touch chegam em unidades de superfície e
seguem para o comportamento nativo do GtkScrolledWindow, que já é contínuo e tem
a cinética dele.
"""

from typing import Optional

from gi.repository import Adw, Gdk, Gtk

# Distância que um entalhe acrescenta ao destino.
STEP = 120

# Amortecimento crítico: assenta sem repicar. Uma grade de capas quicando no
# fim do movimento cansa em vez de agradar. Rigidez baixa alonga a cauda, que é
# a parte que se lê como suavidade.
DAMPING_RATIO = 1.0
MASS = 1.0
STIFFNESS = 220.0

# A mola nunca chega matematicamente ao destino; isto define o quão perto é
# perto o bastante para parar. Em pixels, então meio pixel é invisível.
EPSILON = 0.5


class _SpringScroller:
    """Estado da mola de um único GtkScrolledWindow."""

    def __init__(self, scrolled: Gtk.ScrolledWindow) -> None:
        self.scrolled = scrolled
        self.animation: Optional[Adw.SpringAnimation] = None
        # Destino em voo. Entalhes seguidos precisam somar sobre ele, e não
        # sobre a posição atual — senão girar rápido anda bem menos do que os
        # entalhes pedem, porque cada um recomeçaria do meio do trajeto.
        self.target = 0.0

        controller = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL
        )
        # Captura: precisa chegar antes do GtkScrolledWindow, que senão já teria
        # pulado o adjustment para o valor novo
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        controller.connect("scroll", self._on_scroll)
        scrolled.add_controller(controller)

    def _on_scroll(
        self, controller: Gtk.EventControllerScroll, _dx: float, dy: float
    ) -> bool:
        # Touchpad e touch mandam unidades de superfície e já rolam de forma
        # contínua; mexer neles só pioraria
        event = controller.get_current_event()
        if event is None or event.get_unit() != Gdk.ScrollUnit.WHEEL:
            return Gdk.EVENT_PROPAGATE

        adjustment = self.scrolled.get_vadjustment()
        upper = adjustment.get_upper() - adjustment.get_page_size()
        if upper <= 0:
            # Nada a rolar: deixa seguir para quem estiver por fora
            return Gdk.EVENT_PROPAGATE

        value = adjustment.get_value()

        # A velocidade da mola em curso é herdada para o movimento continuar de
        # onde estava em vez de recomeçar do zero. Ela NÃO recebe o empurrão do
        # entalhe: quem cresce com o entalhe é o destino. Somar impulso à
        # velocidade mirando um destino próximo faria a mola passar do ponto e
        # voltar — repique, que é o oposto do que se quer aqui.
        velocity = 0.0
        running = self.animation is not None and (
            self.animation.get_state() == Adw.AnimationState.PLAYING
        )
        if running:
            velocity = self.animation.get_velocity()
            self.animation.pause()
        else:
            # Parada: o destino anterior está vencido, e a posição pode ter
            # mudado por fora (barra de rolagem, teclado, foco)
            self.target = value

        self.target = max(0.0, min(self.target + dy * STEP, upper))

        self.animation = Adw.SpringAnimation.new(
            self.scrolled,
            value,
            self.target,
            Adw.SpringParams.new(DAMPING_RATIO, MASS, STIFFNESS),
            Adw.PropertyAnimationTarget.new(adjustment, "value"),
        )
        self.animation.set_epsilon(EPSILON)
        self.animation.set_initial_velocity(velocity)
        # Sem isto a mola ultrapassa o destino e volta; numa lista isso lê como
        # defeito, não como física
        self.animation.set_clamp(True)
        self.animation.play()

        return Gdk.EVENT_STOP


def attach(*scrolled_windows: Gtk.ScrolledWindow) -> None:
    """Liga a rolagem por mola nas scrolled windows dadas."""

    for scrolled in scrolled_windows:
        # A referência vive presa ao widget: o controlador guarda o callback, e
        # sem isto o objeto seria coletado com a mola pela metade
        scrolled._spring_scroller = _SpringScroller(scrolled)  # noqa: SLF001
