# gamepad_visual.py
#
# Copyright 2026 Jolven contributors
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

"""O desenho do controle na página Teste de gamepad.

Monta um retrato do controle com widgets GTK (``Box``, ``Grid``, ``Fixed``,
``ProgressBar`` e ``Label``), sem Cairo nem SVG: assim os temas claro, escuro e
de alto contraste continuam valendo e cada peça pode receber nome acessível. O
widget não conhece libmanette nem ``/dev/input``; quem lê o hardware entrega os
eventos por :meth:`GamepadVisual.set_button_pressed`,
:meth:`GamepadVisual.set_axis`, :meth:`GamepadVisual.set_hat` e
:meth:`GamepadVisual.set_trigger`. Funciona parado, sem controle conectado.
"""

from typing import Any

from gi.repository import Gtk

from cartridges.controller import hat_direction, name_for_button

#: Tamanho da base do analógico e do ponto que imita o movimento, em pixels.
_STICK_SIZE = 84
_STICK_DOT = 28
_STICK_RADIUS = (_STICK_SIZE - _STICK_DOT) / 2

#: Abaixo disso um eixo é tratado como repouso, para o ponto não tremer.
_EPSILON = 0.005

#: Botões frontais nas posições do losango (coluna, linha): norte, oeste,
#: leste e sul, com os códigos de ``controller.py``.
_FACE_GRID = {
    307: (1, 0),
    308: (0, 1),
    305: (2, 1),
    304: (1, 2),
}

#: Braços da cruz direcional do D-pad (coluna, linha).
_DPAD = {
    "up": (1, 0),
    "left": (0, 1),
    "right": (2, 1),
    "down": (1, 2),
}

_DPAD_GLYPHS = {"up": "▲", "left": "◀", "right": "▶", "down": "▼"}

#: Bits digitais do D-pad (``BTN_DPAD_*``), quando o driver os expõe.
_DPAD_BUTTONS = {544: "up", 545: "down", 546: "left", 547: "right"}

_SHOULDERS = {310: "LB", 311: "RB"}
_TRIGGERS = {312: "LT", 313: "RT"}
_CENTER = (314, 315, 316, 172)
_STICK_BUTTONS = {317: "left", 318: "right"}

#: Rótulos dos botões frontais por layout. O Xbox e o genérico usam os nomes de
#: ``controller.py``; o PlayStation troca pelas formas clássicas.
_FACE_LAYOUTS = {
    "playstation": {304: "✕", 305: "○", 307: "△", 308: "□"},
}

_LAYOUTS = ("xbox", "playstation", "generic")


def _gated(value: float) -> float:
    """Zera a zona morta dos eixos, preservando o sinal."""
    return 0.0 if abs(value) < _EPSILON else value


class GamepadVisual(Gtk.Box):
    """Retrato do controle; acende cada peça conforme os eventos recebidos."""

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.add_css_class("gamepad-visual")

        self._layout = "generic"
        self._axes: dict[int, float] = {
            0: 0.0,
            1: 0.0,
            2: 0.0,
            3: 0.0,
            4: 0.0,
            5: 0.0,
        }
        self._faces: dict[int, Gtk.Label] = {}
        self._shoulders: dict[int, Gtk.Label] = {}
        self._triggers: dict[str, Gtk.ProgressBar] = {}
        self._center: dict[int, Gtk.Label] = {}
        self._dpad: dict[str, Gtk.Label] = {}
        self._sticks: dict[str, dict[str, Any]] = {}

        self.append(self._build_shoulders())
        self.append(self._build_center())
        self.append(self._build_main())

        self.set_layout("generic")

    # -- construção -----------------------------------------------------------

    @staticmethod
    def _name(widget: Gtk.Widget, text: str) -> None:
        widget.update_property([Gtk.AccessibleProperty.LABEL], [text])

    def _build_shoulders(self) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)

        left = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            valign=Gtk.Align.CENTER,
        )
        left.append(self._shoulder_label(310, _("Left bumper")))
        left.append(self._trigger_bar("LT", _("Left trigger")))

        right = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            valign=Gtk.Align.CENTER,
        )
        right.append(self._shoulder_label(311, _("Right bumper")))
        right.append(self._trigger_bar("RT", _("Right trigger")))

        row.append(left)
        row.append(Gtk.Box(hexpand=True))
        row.append(right)
        return row

    def _shoulder_label(self, code: int, accessible: str) -> Gtk.Label:
        label = Gtk.Label(
            label=name_for_button(code),
            width_request=56,
            height_request=36,
            css_classes=["gamepad-shoulder"],
        )
        self._name(label, accessible)
        self._shoulders[code] = label
        return label

    def _trigger_bar(self, name: str, accessible: str) -> Gtk.ProgressBar:
        bar = Gtk.ProgressBar(
            orientation=Gtk.Orientation.VERTICAL,
            inverted=True,
            width_request=16,
            height_request=64,
            valign=Gtk.Align.CENTER,
        )
        bar.add_css_class("gamepad-trigger")
        bar.set_fraction(0.0)
        self._name(bar, accessible)
        self._triggers[name] = bar
        return bar

    def _build_center(self) -> Gtk.Widget:
        row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            halign=Gtk.Align.CENTER,
        )
        for code in _CENTER:
            name = name_for_button(code)
            label = Gtk.Label(
                label=name,
                width_request=60,
                height_request=32,
                css_classes=["gamepad-center"],
            )
            self._name(label, _("Center button {}").format(name))
            self._center[code] = label
            row.append(label)
        return row

    def _build_main(self) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)

        left = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=24,
            hexpand=True,
            halign=Gtk.Align.START,
        )
        left.append(self._build_dpad())
        left.append(self._build_stick("left", _("Left stick")))

        right = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=24,
            hexpand=True,
            halign=Gtk.Align.END,
        )
        right.append(self._build_stick("right", _("Right stick")))
        right.append(self._build_face())

        row.append(left)
        row.append(right)
        return row

    def _build_dpad(self) -> Gtk.Widget:
        grid = Gtk.Grid(row_spacing=4, column_spacing=4, valign=Gtk.Align.CENTER)
        grid.add_css_class("gamepad-dpad")
        names = {
            "up": _("D-pad up"),
            "down": _("D-pad down"),
            "left": _("D-pad left"),
            "right": _("D-pad right"),
        }
        for key, (column, row) in _DPAD.items():
            arm = Gtk.Label(
                label=_DPAD_GLYPHS[key],
                width_request=28,
                height_request=28,
                css_classes=["gamepad-dpad-arm"],
            )
            self._name(arm, names[key])
            self._dpad[key] = arm
            grid.attach(arm, column, row, 1, 1)
        return grid

    def _build_face(self) -> Gtk.Widget:
        grid = Gtk.Grid(row_spacing=4, column_spacing=4, valign=Gtk.Align.CENTER)
        grid.add_css_class("gamepad-face")
        for code, (column, row) in _FACE_GRID.items():
            label = Gtk.Label(
                width_request=44,
                height_request=44,
                css_classes=["gamepad-face-button"],
            )
            self._faces[code] = label
            grid.attach(label, column, row, 1, 1)
        return grid

    def _build_stick(self, name: str, accessible: str) -> Gtk.Widget:
        fixed = Gtk.Fixed(width_request=_STICK_SIZE, height_request=_STICK_SIZE)

        base = Gtk.Box(width_request=_STICK_SIZE, height_request=_STICK_SIZE)
        base.add_css_class("gamepad-stick-base")
        self._name(base, accessible)

        dot = Gtk.Box(width_request=_STICK_DOT, height_request=_STICK_DOT)
        dot.add_css_class("gamepad-stick-dot")
        self._name(dot, _("{} thumb").format(accessible))

        fixed.put(base, 0, 0)
        center = int((_STICK_SIZE - _STICK_DOT) / 2)
        fixed.put(dot, center, center)

        self._sticks[name] = {
            "fixed": fixed,
            "dot": dot,
            "base": base,
            "x_axis": 0 if name == "left" else 3,
            "y_axis": 1 if name == "left" else 4,
        }
        return fixed

    # -- eventos --------------------------------------------------------------

    def set_layout(self, layout: str) -> None:
        """Escolhe os rótulos frontais: Xbox, PlayStation ou genérico."""
        key = (layout or "generic").strip().lower()
        if key not in _LAYOUTS:
            key = "generic"
        self._layout = key
        labels = _FACE_LAYOUTS.get(key, {})
        for code, widget in self._faces.items():
            text = labels.get(code) or name_for_button(code)
            widget.set_label(text)
            self._name(widget, _("Face button {}").format(text))

    def set_button_pressed(self, code: int, pressed: bool) -> None:
        """Acende ou apaga a peça associada a um botão Linux."""
        if code in self._faces:
            self._toggle(self._faces[code], pressed)
        elif code in self._shoulders:
            self._toggle(self._shoulders[code], pressed)
        elif code in _TRIGGERS:
            self.set_trigger(_TRIGGERS[code], pressed)
        elif code in self._center:
            self._toggle(self._center[code], pressed)
        elif code in _DPAD_BUTTONS:
            self._toggle(self._dpad[_DPAD_BUTTONS[code]], pressed)
        elif code in _STICK_BUTTONS:
            state = self._sticks[_STICK_BUTTONS[code]]
            self._toggle(state["base"], pressed)

    def set_axis(self, axis: int, value: float) -> None:
        """Guarda um eixo e move o ponto ou a barra correspondente."""
        self._axes[axis] = value
        if axis in (0, 1):
            self._update_stick("left")
        elif axis in (3, 4):
            self._update_stick("right")
        elif axis == 2:
            self._set_trigger_value("LT", value)
        elif axis == 5:
            self._set_trigger_value("RT", value)

    def set_trigger(self, name: str, pressed: bool) -> None:
        """Preenche ou esvazia a barra de um gatilho digital."""
        self._set_trigger_value(name.upper(), 1.0 if pressed else 0.0)

    def set_hat(self, axis: int, value: int) -> None:
        """Acende um braço da cruz direcional ou apaga todos em repouso."""
        direction = hat_direction(axis, value)
        for name, arm in self._dpad.items():
            self._toggle(arm, direction == name)

    # -- internos -------------------------------------------------------------

    def _set_trigger_value(self, name: str, value: float) -> None:
        bar = self._triggers.get(name)
        if bar is None:
            return
        amount = _gated(value)
        bar.set_fraction(max(0.0, min(1.0, amount)))
        self._toggle(bar, amount > _EPSILON)

    def _update_stick(self, name: str) -> None:
        state = self._sticks.get(name)
        if state is None:
            return
        x = _gated(self._axes.get(state["x_axis"], 0.0)) * _STICK_RADIUS
        y = _gated(self._axes.get(state["y_axis"], 0.0)) * _STICK_RADIUS
        length = (x * x + y * y) ** 0.5
        if length > _STICK_RADIUS:
            scale = _STICK_RADIUS / length
            x *= scale
            y *= scale
        center = (_STICK_SIZE - _STICK_DOT) / 2
        state["fixed"].move(state["dot"], round(center + x), round(center + y))

    @staticmethod
    def _toggle(widget: Gtk.Widget, pressed: bool) -> None:
        for css_class in ("active", "pressed"):
            if pressed:
                widget.add_css_class(css_class)
            else:
                widget.remove_css_class(css_class)
