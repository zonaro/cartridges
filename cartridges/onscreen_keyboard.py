# onscreen_keyboard.py
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

"""Teclado na tela completo, pensado para a Jolven Session.

O teclado anterior, embutido em ``window.py``, oferecia apenas A-Z 0-9 com
Backspace, Space e Clear. Este diálogo o substitui por um teclado paginado
(letras, números/pontuação, símbolos e emojis) com modificadores de verdade —
Shift pontual, Caps permanente, Backspace, Espaço, Limpar, Concluir e setas de
cursor — e insere cada caractere na posição do cursor, respeitando a seleção da
API ``Gtk.Editable``.

A página é uma grade de botões focáveis: o GTK move o foco geometricamente e o
gamepad usa ``Gtk.Widget.child_focus`` pelo mesmo caminho de qualquer outra
tela, então mouse, teclado físico e controle alcançam todas as teclas. O
trabalho inteiro acontece na thread principal; não há rede nem I/O.
"""

from typing import Any, Callable, Optional

from gi.repository import Adw, GLib, Gtk, Pango

# Altura e largura mínimas de uma tecla. O contrato de UI da sessão pede alvos
# de aproximadamente 44 px; usamos 44 px de altura e 44 px de largura nos
# caracteres para o mesmo conforto de toque.
KEY_MIN = 44

# Quantas teclas de emoji cabem por linha. Sete mantém a página legível em
# telas estreitas sem encher a grade quando a janela está larga.
EMOJIS_PER_ROW = 7

# Letras em minúsculas; a página troca para maiúsculas com Shift/Caps.
# ç e ñ são teclas dedicadas na fileira principal, independente do idioma.
LETTER_ROWS = (
    "qwertyuiop",
    "asdfghjkl",
    "zxcvbnmçñ",
)

# Números e pontuação básica, na ordem de um teclado comum.
NUMBER_ROWS = (
    "1234567890",
    "-_=+()/\\:;",
    "\"',.?!@#$%&",
)

# Símbolos gerais, matemáticos, moedas e letras acentuadas do português.
SYMBOL_ROWS = (
    "€£¥¢§±×÷°µ",
    "¿¡«»¶…•‰✓",
    "©®™★☆♦†‡",
    "áàâãäåçéèê",
    "ëíìîïñóòô",
    "õöúùûüýÿ",
)

# Emojis comuns, agrupados por tema. O rótulo de cada grupo é traduzível; os
# emojis em si não passam por gettext.
EMOJI_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        _("Faces and emotions"),
        (
            "😀",
            "😃",
            "😄",
            "😁",
            "😆",
            "😅",
            "😂",
            "🤣",
            "😊",
            "😇",
            "🙂",
            "😉",
            "😍",
            "🥰",
            "😎",
            "🤔",
        ),
    ),
    (
        _("Gestures and people"),
        (
            "👍",
            "👎",
            "👌",
            "✌️",
            "🤞",
            "🤟",
            "🤙",
            "👋",
            "🙌",
            "👏",
            "🤝",
            "🙏",
            "💪",
            "👊",
            "✊",
            "🖖",
        ),
    ),
    (
        _("Animals and nature"),
        (
            "🐶",
            "🐱",
            "🐭",
            "🐹",
            "🐰",
            "🦊",
            "🐻",
            "🐼",
            "🐨",
            "🐯",
            "🦁",
            "🐮",
            "🐷",
            "🐸",
            "🐵",
            "🦄",
        ),
    ),
    (
        _("Food and drinks"),
        (
            "🍎",
            "🍊",
            "🍋",
            "🍌",
            "🍉",
            "🍇",
            "🍓",
            "🍒",
            "🍑",
            "🥭",
            "🍍",
            "🥥",
            "🍅",
            "🥑",
            "🥔",
            "🥕",
        ),
    ),
    (
        _("Objects and activities"),
        (
            "⚽",
            "🏀",
            "🎮",
            "🎲",
            "🎯",
            "🎁",
            "🎈",
            "🎉",
            "🏆",
            "🥇",
            "🚀",
            "🚗",
            "✈️",
            "🎵",
            "🎶",
            "❤️",
        ),
    ),
)


class OnscreenKeyboardDialog(Adw.Dialog):
    """Teclado virtual completo para editar um ``Gtk.Editable`` sem ponteiro.

    O diálogo guarda referência apenas ao widget alvo e ao próprio estado dos
    modificadores; nenhuma propriedade visual é tocada fora da thread principal.
    Ao fechar, devolve o foco ao campo de origem.
    """

    def __init__(self, editable: Gtk.Editable, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._editable = editable
        self._shift = False
        self._caps = False

        self.set_title(_("On-screen keyboard"))
        self.set_content_width(760)
        self.set_content_height(600)

        self._preview = Gtk.Label(
            xalign=0,
            ellipsize=Pango.EllipsizeMode.END,
            margin_start=12,
            margin_end=12,
            margin_top=12,
        )

        self._stack = Gtk.Stack(
            hexpand=True,
            vexpand=True,
            hhomogeneous=False,
            vhomogeneous=False,
        )
        self._tabs = Gtk.Box(
            spacing=0,
            halign=Gtk.Align.CENTER,
            css_classes=["linked"],
        )
        self._page_buttons: dict[str, Gtk.ToggleButton] = {}

        self._letter_buttons: list[tuple[Gtk.Button, str]] = []
        self._first_key: Optional[Gtk.Widget] = None
        self._shift_button: Optional[Gtk.ToggleButton] = None
        self._caps_button: Optional[Gtk.ToggleButton] = None

        self._build_pages()
        self._build_tabs()

        self.connect("closed", self._on_closed)
        self._refresh_preview()

    # region Montagem da interface

    def _build_pages(self) -> None:
        for name, builder in (
            ("letters", self._build_letters),
            ("numbers", self._build_rows(NUMBER_ROWS)),
            ("symbols", self._build_rows(SYMBOL_ROWS)),
            ("emojis", self._build_emojis),
        ):
            self._stack.add_named(builder(), name)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.append(self._preview)
        content.append(self._tabs)
        content.append(self._stack)
        content.append(self._build_modifiers())

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        toolbar.set_content(content)
        self.set_child(toolbar)

    def _build_tabs(self) -> None:
        first: Optional[Gtk.ToggleButton] = None
        for name, label in (
            ("letters", _("Letters")),
            ("numbers", _("Numbers")),
            ("symbols", _("Symbols")),
            ("emojis", _("Emojis")),
        ):
            button = Gtk.ToggleButton(label=label)
            button.set_size_request(-1, KEY_MIN)
            if first is None:
                first = button
            else:
                button.set_group(first)
            button.connect("toggled", self._on_page_toggled, name)
            self._tabs.append(button)
            self._page_buttons[name] = button
        if first is not None:
            first.set_active(True)

    def _build_letters(self) -> Gtk.Widget:
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
            vexpand=True,
        )
        for row in LETTER_ROWS:
            line = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
            for character in row:
                button = self._key(character)
                button.connect("clicked", self._on_letter, character)
                self._letter_buttons.append((button, character))
                self._remember_first(button)
                line.append(button)
            box.append(line)
        return box

    def _build_rows(self, rows: tuple[str, ...]) -> Callable[[], Gtk.Widget]:
        def build() -> Gtk.Widget:
            box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=6,
                halign=Gtk.Align.CENTER,
                valign=Gtk.Align.CENTER,
                vexpand=True,
            )
            for row in rows:
                line = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
                for character in row:
                    button = self._key(character)
                    button.connect("clicked", self._on_insert, character)
                    self._remember_first(button)
                    line.append(button)
                box.append(line)
            return box

        return build

    def _build_emojis(self) -> Gtk.Widget:
        groups = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=16,
            margin_start=12,
            margin_end=12,
            margin_top=12,
            margin_bottom=12,
        )
        for label, emojis in EMOJI_GROUPS:
            heading = Gtk.Label(label=label, xalign=0)
            heading.add_css_class("dim-label")

            rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            for start in range(0, len(emojis), EMOJIS_PER_ROW):
                line = Gtk.Box(spacing=6)
                for emoji in emojis[start : start + EMOJIS_PER_ROW]:
                    button = self._key(emoji)
                    button.connect("clicked", self._on_insert, emoji)
                    self._remember_first(button)
                    line.append(button)
                rows.append(line)

            groups.append(heading)
            groups.append(rows)

        scroll = Gtk.ScrolledWindow(
            hexpand=True,
            vexpand=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
        )
        scroll.set_child(groups)
        return scroll

    def _build_modifiers(self) -> Gtk.Widget:
        # FlowBox em vez de grade fixa: em 360 px as teclas quebram em
        # linhas sem corte, e em tela larga continuam numa faixa só.
        flow = Gtk.FlowBox(
            column_spacing=6,
            row_spacing=6,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.END,
            homogeneous=True,
            max_children_per_line=4,
            min_children_per_line=2,
            selection_mode=Gtk.SelectionMode.NONE,
            margin_start=12,
            margin_end=12,
            margin_bottom=12,
        )

        self._shift_button = self._toggle(_("Shift"), width=96)
        self._shift_button.connect("toggled", self._on_shift_toggled)
        self._caps_button = self._toggle(_("Caps"), width=96)
        self._caps_button.connect("toggled", self._on_caps_toggled)
        backspace = self._modifier(_("Backspace"), width=120)
        backspace.connect("clicked", self._on_backspace)
        space = self._modifier(_("Space"), width=220)
        space.connect("clicked", self._on_insert, " ")
        clear = self._modifier(_("Clear"), width=96)
        clear.connect("clicked", self._on_clear)
        done = self._modifier(_("Done"), width=96)
        done.add_css_class("suggested-action")
        done.connect("clicked", self._on_done)

        left = self._modifier(
            icon="go-previous-symbolic", tooltip=_("Move cursor left")
        )
        left.connect("clicked", self._on_cursor, -1)
        right = self._modifier(
            icon="go-next-symbolic", tooltip=_("Move cursor right")
        )
        right.connect("clicked", self._on_cursor, 1)

        row = (
            self._shift_button,
            self._caps_button,
            backspace,
            space,
            clear,
            left,
            right,
            done,
        )
        for widget in row:
            flow.append(widget)
        return flow

    def _key(self, label: str) -> Gtk.Button:
        button = Gtk.Button(label=label)
        button.set_size_request(KEY_MIN, KEY_MIN)
        return button

    def _modifier(
        self,
        label: Optional[str] = None,
        *,
        width: int = KEY_MIN,
        icon: Optional[str] = None,
        tooltip: Optional[str] = None,
    ) -> Gtk.Button:
        button = Gtk.Button()
        if icon:
            button.set_icon_name(icon)
        else:
            button.set_label(label or "")
        button.set_size_request(width, KEY_MIN)
        if tooltip:
            button.set_tooltip_text(tooltip)
        return button

    def _toggle(self, label: str, *, width: int = KEY_MIN) -> Gtk.ToggleButton:
        button = Gtk.ToggleButton(label=label)
        button.set_size_request(width, KEY_MIN)
        return button

    def _remember_first(self, widget: Gtk.Widget) -> None:
        if self._first_key is None:
            self._first_key = widget

    # endregion
    # region Foco

    def focus_first_key(self) -> bool:
        """Foca a primeira tecla da página de letras. Uso via ``GLib.idle_add``."""
        if self._first_key is not None:
            self._first_key.grab_focus()
        return False

    def _on_page_toggled(self, button: Gtk.ToggleButton, name: str) -> None:
        if button.get_active():
            self._stack.set_visible_child_name(name)

    def _on_closed(self, *_args: Any) -> None:
        # Devolve o foco ao campo de origem, para o gamepad continuar de onde
        # parou em vez de voltar ao topo da página.
        if isinstance(self._editable, Gtk.Widget):
            self._editable.grab_focus()

    # endregion
    # region Edição do texto

    def _insert(self, _button: Gtk.Button, text: str) -> None:
        editable = self._editable
        editable.delete_selection()
        position = editable.get_position()
        editable.insert_text(text, position)
        # A API não garante que o cursor avance sozinho ao inserir com posição
        # explícita, então reposicionamos logo depois do texto inserido.
        editable.set_position(position + len(text))
        self._refresh_preview()

    def _on_insert(self, button: Gtk.Button, text: str) -> None:
        self._insert(button, text)

    def _on_letter(self, button: Gtk.Button, character: str) -> None:
        upper = self._shift or self._caps
        self._insert(button, character.upper() if upper else character)
        if self._shift and self._shift_button is not None:
            # Shift é pontual: volta a desligar assim que uma letra é digitada.
            self._shift_button.set_active(False)

    def _on_shift_toggled(self, button: Gtk.ToggleButton) -> None:
        self._shift = button.get_active()
        self._refresh_letter_labels()

    def _on_caps_toggled(self, button: Gtk.ToggleButton) -> None:
        self._caps = button.get_active()
        self._refresh_letter_labels()

    def _refresh_letter_labels(self) -> None:
        upper = self._shift or self._caps
        for button, character in self._letter_buttons:
            button.set_label(character.upper() if upper else character)

    def _on_backspace(self, *_args: Any) -> None:
        editable = self._editable
        if editable.get_selection_bounds():
            editable.delete_selection()
        else:
            position = editable.get_position()
            if position > 0:
                editable.delete_text(position - 1, position)
        self._refresh_preview()

    def _on_done(self, *_args: Any) -> None:
        self.close()

        def activate_target() -> bool:
            if isinstance(self._editable, Gtk.Widget):
                self._editable.activate()
            return False

        GLib.idle_add(activate_target)

    def _on_clear(self, *_args: Any) -> None:
        self._editable.set_text("")
        self._refresh_preview()

    def _on_cursor(self, _button: Gtk.Button, delta: int) -> None:
        editable = self._editable
        bounds = editable.get_selection_bounds()
        if bounds:
            start, end = bounds
            editable.set_position(start if delta < 0 else end)
            return
        length = len(editable.get_text() or "")
        position = editable.get_position()
        editable.set_position(max(0, min(length, position + delta)))

    def _refresh_preview(self) -> None:
        text = self._editable.get_text() or ""
        if text:
            self._preview.remove_css_class("dim-label")
            self._preview.set_label(text)
        else:
            self._preview.add_css_class("dim-label")
            self._preview.set_label(_("Type with the controller"))

    # endregion
