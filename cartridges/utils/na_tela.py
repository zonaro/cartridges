# na_tela.py
#
# Copyright 2026 joaomgabaldi
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Entrega do resultado de uma thread à tela, sem ficar atrás do redesenho."""

from typing import Any, Callable

from gi.repository import GLib


def entregar_na_tela(func: Callable[..., Any], *args: Any) -> int:
    """Agenda ``func(*args)`` na thread principal, à frente do redesenho.

    Para o resultado que o usuário espera olhando um spinner ou uma barra. O
    ``GLib.idle_add`` puro usa a prioridade ociosa (200), abaixo do redesenho
    do GTK (120): enquanto algo anima e cada quadro ocupa o intervalo inteiro
    da tela, o callback não roda. Em 28/09/2026 o resultado já baixado ficava
    de 9 a 36 s parado na fila; em PRIORITY_DEFAULT a pior espera foi ~0,1 s.

    Todas as entregas de uma mesma thread passam por aqui: prioridades
    diferentes não rodam na ordem em que foram agendadas, e os seletores
    contam com o ``_finish_results`` depois dos ``_add_result``. Tarefa de
    fundo, que ninguém espera, e adiamento feito na própria thread principal
    continuam no ``GLib.idle_add`` comum.
    """
    return GLib.idle_add(func, *args, priority=GLib.PRIORITY_DEFAULT)
