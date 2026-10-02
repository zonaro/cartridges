# animated_flow_box.py
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

"""Grade de capas que desliza até o lugar novo em vez de saltar.

O GtkFlowBox refaz a grade inteira dentro de uma única alocação: num quadro as
capas estão numa posição e no seguinte já estão na outra, sem nada no meio para
animar. Quem quiser o movimento precisa fabricá-lo.

O layout continua sendo do GtkFlowBox. O que esta subclasse faz é, depois que
ele decide onde cada capa vai, re-alocar as capas num ponto entre onde elas
estão desenhadas e onde deveriam estar. Uma mola leva esse progresso de 0 a 1 e
pede uma alocação nova a cada quadro; no fim do trajeto ninguém é reposicionado
e a grade volta a ser exatamente o que o GtkFlowBox calculou.

O gatilho é a parte delicada. Animar toda vez que uma capa muda de lugar quebra
o arrasto da borda da janela: a cada quadro a mola recomeçaria da posição atual
com progresso zero, e a grade ficaria congelada em vez de acompanhar o cursor.
Então só refluxo de verdade anima — mudou o número de colunas, ou alguma capa
mudou de linha. A grade se recentrando enquanto a janela cresce mexe só no X,
não passa por aqui, e continua grudada no mouse.

Entrar ou sair capa (importar, esconder, buscar) também não anima: aí a grade
muda por outro motivo, e o salto é o comportamento de sempre.

Duas consequências desse critério são deliberadas e ficam registradas aqui.
Uma reordenação que só troca capas dentro da mesma linha mexe apenas no X e,
vista daqui de dentro, é idêntica à grade derivando de lado — então não anima;
o preço de animá-la seria reintroduzir o congelamento no arrasto da borda. E o
recentro da grade no instante do refluxo pertence ao pai (halign: center no
.blp), não a esta classe: ele é seco, e o que desliza é só o trajeto de cada
capa dentro da grade.
"""

from typing import Any, Optional

from gi.repository import Adw, Graphene, Gsk, Gtk

# Amortecimento crítico, pelo mesmo motivo da rolagem por mola: uma grade de
# capas quicando no fim do movimento cansa em vez de agradar. A rigidez é mais
# alta que a de lá porque o trajeto aqui é curto — uma coluna, não uma tela.
#
# Esta rigidez dá 400ms de ponta a ponta. Ela e o EPSILON decidem a duração
# juntos, então quem quiser outro tempo confere em vez de estimar:
# `Adw.SpringAnimation.get_estimated_duration()` devolve o valor em ms.
DAMPING_RATIO = 1.0
MASS = 1.0
STIFFNESS = 450.0

# Progresso é adimensional, de 0 a 1, então a folga para dar a mola por
# terminada é bem menor que a de uma mola medida em pixels.
EPSILON = 0.002

# Abaixo disto a capa já está onde o GtkFlowBox a pôs e não precisa ser mexida.
# Vale meio pixel: nada que o olho pegue, e economiza re-alocar a grade inteira
# nos quadros do fim, quando quase todo mundo já chegou.
SETTLED = 0.5


def _first_row_length(layout: dict[Gtk.Widget, tuple[float, float, int, int]]) -> int:
    """Quantas capas cabem na primeira linha, ou seja, o número de colunas."""

    if not layout:
        return 0

    top = min(position[1] for position in layout.values())
    return sum(1 for position in layout.values() if abs(position[1] - top) < SETTLED)


class AnimatedFlowBox(Gtk.FlowBox):
    """GtkFlowBox cujas capas deslizam quando a grade refaz as linhas."""

    __gtype_name__ = "AnimatedFlowBox"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        # capa -> (x, y, largura, altura) que o GtkFlowBox pediu
        self._targets: dict[Gtk.Widget, tuple[float, float, int, int]] = {}
        # capa -> de onde a mola atual partiu
        self._origins: dict[Gtk.Widget, tuple[float, float]] = {}
        # capa -> onde ela está desenhada neste instante
        self._positions: dict[Gtk.Widget, tuple[float, float]] = {}
        self._columns = 0
        self._progress = 1.0
        # Tudo que faria o GtkFlowBox chegar a um layout diferente. Igual ao do
        # quadro anterior significa que as posições da mão ainda valem.
        self._signature: Optional[tuple] = None
        # A mola começa e termina em play(); a alocação é que vai lendo o
        # progresso. Guardá-la evita recriar o objeto a cada refluxo.
        self._animation: Optional[Adw.SpringAnimation] = None
        self._allocating = False

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        # O GtkFlowBox resolve a grade de verdade primeiro. Daqui para baixo é
        # só atrasar a chegada das capas ao lugar onde ele já as pôs.
        Gtk.FlowBox.do_size_allocate(self, width, height, baseline)

        # A mola dispara o callback dela já dentro do play(), e pedir alocação
        # no meio de uma alocação é pedir laço
        self._allocating = True
        try:
            self._animate(width, height)
        finally:
            self._allocating = False

    def _animate(self, width: int, height: int) -> None:
        children = self._visible_children()

        # Perguntar a posição de cada capa custa caro — numa biblioteca de 300
        # jogos, 12ms dos 16ms que um quadro tem. E é trabalho jogado fora na
        # maioria das vezes: os quadros da animação são pedidos por esta classe
        # mesmo, com a grade parada, então o GtkFlowBox devolve o mesmo layout
        # que já está na mão. Esta assinatura reúne tudo que faria o layout
        # mudar e custa uma caminhada pelos filhos, que é 0,3ms.
        signature = (width, height, self.get_max_children_per_line(), children)

        if signature == self._signature and self._targets:
            targets = self._targets
        else:
            targets = self._read_layout(children)

        self._signature = signature
        columns = _first_row_length(targets)

        if self._reflowed(targets, columns):
            # Parte de onde as capas estão desenhadas, não de onde o layout
            # anterior dizia: um refluxo por cima de outro não pode dar
            # solavanco, e é isso que acontece ao arrastar a borda depressa
            self._origins = {
                child: self._positions.get(child, target[:2])
                for child, target in targets.items()
            }
            self._progress = 0.0
            self._play()

        self._targets = targets
        self._columns = columns

        if self._progress >= 1.0:
            # Chegou: o chain-up já deixou tudo no lugar certo, e as origens
            # não valem mais nada — soltá-las é o que impede uma capa removida
            # da biblioteca de continuar viva presa a este dicionário
            self._positions = {child: target[:2] for child, target in targets.items()}
            self._origins = {}
            return

        positions = {}
        for child, (target_x, target_y, child_width, child_height) in targets.items():
            origin_x, origin_y = self._origins.get(child, (target_x, target_y))
            x = origin_x + (target_x - origin_x) * self._progress
            y = origin_y + (target_y - origin_y) * self._progress
            positions[child] = (x, y)

            if abs(x - target_x) < SETTLED and abs(y - target_y) < SETTLED:
                continue

            # -1 é a linha de base que o próprio GtkFlowBox dá às capas; a que
            # chega aqui é a da grade, e não vale para os filhos dela
            child.allocate(
                child_width,
                child_height,
                -1,
                Gsk.Transform().translate(Graphene.Point().init(x, y)),
            )

        self._positions = positions

    def _visible_children(self) -> tuple[Gtk.Widget, ...]:
        """As capas que o GtkFlowBox está de fato desenhando, na ordem delas.

        Serve de assinatura da grade: entrar ou sair jogo, reordenar e filtrar
        pela busca mudam esta tupla, e é isso que autoriza reaproveitar as
        posições lidas no quadro anterior.
        """

        children = []
        child = self.get_first_child()

        while child:
            # Capa filtrada pela busca não é alocada, e perguntar por ela só
            # traria posição vencida para dentro da conta das colunas
            if child.get_child_visible():
                children.append(child)

            child = child.get_next_sibling()

        return tuple(children)

    def _read_layout(
        self, children: tuple[Gtk.Widget, ...]
    ) -> dict[Gtk.Widget, tuple[float, float, int, int]]:
        """Onde o GtkFlowBox acabou de pôr cada capa."""

        layout = {}

        for child in children:
            found, bounds = child.compute_bounds(self)
            if found:
                layout[child] = (
                    bounds.origin.x,
                    bounds.origin.y,
                    round(bounds.size.width),
                    round(bounds.size.height),
                )

        return layout

    def _reflowed(
        self, targets: dict[Gtk.Widget, tuple[float, float, int, int]], columns: int
    ) -> bool:
        """A grade se refez, ou só derivou de lado?"""

        # Primeira alocação: não há de onde sair
        if not self._positions:
            return False

        # Entrou ou saiu capa. A grade muda por outro motivo que não refluxo, e
        # animar isso é outra conversa — aparecer deslizando de uma posição que
        # a capa nunca ocupou não lê como movimento, lê como defeito.
        if targets.keys() != self._targets.keys():
            return False

        if columns != self._columns:
            return True

        # Trocou de linha sem trocar de coluna: reordenação. O X sozinho não
        # conta, senão arrastar a borda da janela cairia aqui todo quadro.
        return any(
            abs(target[1] - self._targets[child][1]) > SETTLED
            for child, target in targets.items()
        )

    def _play(self) -> None:
        if self._animation is None:
            self._animation = Adw.SpringAnimation.new(
                self,
                0.0,
                1.0,
                Adw.SpringParams.new(DAMPING_RATIO, MASS, STIFFNESS),
                Adw.CallbackAnimationTarget.new(self._on_progress),
            )
            self._animation.set_epsilon(EPSILON)
            # Sem isto a grade ultrapassa o destino e volta
            self._animation.set_clamp(True)

        # play() em mola já correndo recomeça do zero, que é justamente o que se
        # quer quando um refluxo chega no meio do outro. Ela também se pula
        # sozinha quando as animações do sistema estão desligadas ou a grade não
        # está na tela: nos dois casos o progresso vai direto a 1 e a grade
        # salta, como fazia antes desta classe existir.
        self._animation.play()

    def _on_progress(self, value: float, *_args: Any) -> None:
        self._progress = value

        if not self._allocating:
            self.queue_allocate()
