# single_instance.py
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

"""Keep a second copy of the app from running alongside the first.

GApplication already promises this — that is what the application id is for —
but it delivers it over the D-Bus session bus, and Windows has no session bus
running. GLib tries to start one by spawning ``gdbus.exe``; when that fails
(a mismatched MSYS2 build, missing dependencies) it prints

    gdbus.exe dbus binary failed to launch bus, maybe incompatible version

and GApplication quietly falls back to behaving like a non-unique app. Nothing
crashes, so the failure is easy to read as cosmetic — but the uniqueness check
is gone with it, and two Cartridges windows both loading and writing the same
game files means one of them saving over the other's playtime.

A named mutex is the platform's own answer to this question and needs nothing
running to work. Windows destroys it when the process ends, including when it
is killed, so a crash cannot leave a lock behind that blocks the next start.
"""

import logging
import sys
import threading
from typing import Any, Callable, Optional

# Session-local namespace, which is the same granularity as the data being
# protected: the games, covers and logos this guard exists to keep two copies
# from writing at once all live under the *user's* profile, so two different
# users are never touching the same files in the first place.
#
# `Global\` was wrong in both directions. Where the cross-session handle is
# openable it locks out a second Windows user entirely — they cannot start the
# app at all while the first is logged in, over data they do not share — and
# where it is not, `CreateMutexW` fails with ERROR_ACCESS_DENIED and the guard
# silently falls open, which is the case it was added to prevent.
#
# The name is arbitrary but must never change: it is the identity of the lock.
_MUTEX_NAME = "Local\\page.kramo.Cartridges.SingleInstance"

_ERROR_ALREADY_EXISTS = 183

# Held for the lifetime of the process. Keeping the handle in a module global
# is the whole mechanism: if it were garbage collected the mutex would be
# released and the guard would stop guarding.
_handle: Optional[Any] = None


def acquire() -> bool:
    """Claim the single-instance lock. False when another copy already has it.

    Always true off Windows, where this is not the mechanism in use, and also
    true if the lock cannot be created at all — a guard that cannot be built is
    not a reason to refuse to start the app.
    """
    global _handle  # pylint: disable=global-statement

    if sys.platform != "win32":
        return True

    try:
        import ctypes  # pylint: disable=import-outside-toplevel

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # Spelled out with plain ctypes types rather than through `wintypes`,
        # which cannot even be imported on other platforms: HANDLE is a
        # pointer, BOOL an int, and the name a wide string.
        kernel32.CreateMutexW.argtypes = (
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_wchar_p,
        )
        kernel32.CreateMutexW.restype = ctypes.c_void_p

        handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        last_error = ctypes.get_last_error()
    except (OSError, AttributeError, ValueError) as error:
        logging.warning("Could not create the single-instance lock: %s", error)
        return True

    if not handle:
        logging.warning(
            "Could not create the single-instance lock (error %s)", last_error
        )
        return True

    if last_error == _ERROR_ALREADY_EXISTS:
        # Someone else owns the mutex. This handle refers to the same object
        # but owns nothing, so closing it releases no lock — it is just not
        # left dangling in a process that is about to exit anyway.
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        return False

    _handle = handle
    return True


def release() -> None:
    """Solta o lock antes de um reinício pedido pelo próprio app (restauração
    de backup): o processo novo sobe enquanto este ainda termina de sair, e
    sem isto ele se acharia a segunda cópia e fecharia."""
    global _handle  # pylint: disable=global-statement

    if _handle is None or sys.platform != "win32":
        return
    try:
        import ctypes  # pylint: disable=import-outside-toplevel

        ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(_handle))
    except (OSError, AttributeError) as error:
        logging.warning("Could not release the single-instance lock: %s", error)
    _handle = None


# Título da janela principal (window.blp). Não passa por tradução — a
# interface é hardcoded — então dá para casar contra ele daqui, onde o gettext
# do launcher pode nem ter sido instalado ainda.
_MAIN_WINDOW_TITLE = "Cartridges"


# Evento nomeado que a segunda cópia acende para a primeira: a janela principal
# pode estar escondida (fechada com tarefas rodando, só a das tarefas à vista), e
# uma janela que o GTK escondeu não volta por Win32 — o GTK segue achando que ela
# não está na tela. Só a primeira cópia sabe se mostrar. Mesmo escopo e mesma
# regra do mutex: o nome nunca muda.
_EVENT_NAME = "Local\\page.kramo.Cartridges.Present"

_EVENT_MODIFY_STATE = 0x0002
_INFINITE = 0xFFFFFFFF


def watch_second_launch(callback: Callable[[], None]) -> None:
    """Chama ``callback`` sempre que uma segunda cópia do app tentar abrir.

    Numa thread de fundo: quem chama entrega o resultado à thread da interface.
    Best-effort como o resto do módulo: sem o evento, só se perde o retorno da
    janela escondida.
    """
    if sys.platform != "win32":
        return

    try:
        import ctypes  # pylint: disable=import-outside-toplevel

        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.CreateEventW.argtypes = (
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_wchar_p,
        )
        kernel32.CreateEventW.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32

        # Reinício automático (auto-reset): cada acendida acorda uma vez.
        if not (handle := kernel32.CreateEventW(None, False, False, _EVENT_NAME)):
            return
    except (OSError, AttributeError, ValueError) as error:
        logging.warning("Could not create the second-launch event: %s", error)
        return

    def wait() -> None:
        while kernel32.WaitForSingleObject(handle, _INFINITE) == 0:  # WAIT_OBJECT_0
            callback()

    threading.Thread(target=wait, daemon=True).start()


def _wake_running_instance() -> None:
    """Acende o evento que a primeira cópia vigia (veja `watch_second_launch`)."""
    import ctypes  # pylint: disable=import-outside-toplevel

    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.OpenEventW.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p)
    kernel32.OpenEventW.restype = ctypes.c_void_p
    kernel32.SetEvent.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    if not (handle := kernel32.OpenEventW(_EVENT_MODIFY_STATE, False, _EVENT_NAME)):
        return
    # Este processo acabou de ser iniciado pelo usuário e carrega o direito de
    # foreground: cede-o à primeira cópia, que é quem vai mostrar a janela.
    ctypes.WinDLL("user32").AllowSetForegroundWindow(ctypes.c_uint32(0xFFFFFFFF))
    kernel32.SetEvent(handle)
    kernel32.CloseHandle(handle)


def present_running_instance() -> None:
    """Traz a janela da instância que já está rodando para a frente.

    O GApplication faria isso sozinho (o activate da segunda ativação apresenta
    a janela), mas esse caminho viaja pelo D-Bus, que não existe no Windows —
    é o mesmo motivo de o lock acima existir. Então o segundo processo acha a
    janela via Win32 e a realça antes de sair; sem isto, o segundo clique no
    atalho parece simplesmente não fazer nada.

    Best-effort de ponta a ponta: qualquer falha custa só o realce, nunca pode
    impedir o processo de sair limpo.
    """
    if sys.platform != "win32":
        return

    # Com o próprio `try`: se acordar a primeira cópia falhar, o realce abaixo
    # ainda é tentado.
    try:
        _wake_running_instance()
    except Exception as error:  # pylint: disable=broad-exception-caught
        logging.warning("Could not wake the running Cartridges instance: %s", error)

    try:
        import ctypes  # pylint: disable=import-outside-toplevel
        from ctypes import wintypes  # pylint: disable=import-outside-toplevel

        user32 = ctypes.WinDLL("user32")
        user32.GetWindowTextW.argtypes = (
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_int,
        )
        user32.GetClassNameW.argtypes = (
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_int,
        )
        user32.IsIconic.argtypes = (ctypes.c_void_p,)
        user32.IsWindowVisible.argtypes = (ctypes.c_void_p,)
        user32.ShowWindow.argtypes = (ctypes.c_void_p, ctypes.c_int)
        user32.SetForegroundWindow.argtypes = (ctypes.c_void_p,)

        found: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, ctypes.c_void_p, wintypes.LPARAM)
        def on_window(hwnd: int, _lparam: int) -> bool:
            title = ctypes.create_unicode_buffer(64)
            class_name = ctypes.create_unicode_buffer(64)
            user32.GetWindowTextW(hwnd, title, 64)
            user32.GetClassNameW(hwnd, class_name, 64)
            # O par título + classe "gdk*" (as janelas GTK no Windows registram
            # classes gdkSurface*) evita realçar uma pasta do Explorer que por
            # acaso se chame Cartridges.
            if title.value == _MAIN_WINDOW_TITLE and class_name.value.lower().startswith(
                "gdk"
            ):
                found.append(hwnd)
                return False  # para a enumeração: achou
            return True

        user32.EnumWindows(on_window, 0)
        if not found:
            return

        hwnd = ctypes.c_void_p(found[0])
        # Principal escondida (só a janela das tarefas à vista): quem a mostra é
        # a primeira cópia, ao acordar. Dar foreground aqui à HWND escondida a
        # faria reaparecer visível mas sem foco de teclado (`is_active()` falso).
        if not user32.IsWindowVisible(hwnd):
            return
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        # Funciona porque este processo acabou de ser iniciado pelo usuário e
        # ainda carrega o direito de foreground dessa interação.
        user32.SetForegroundWindow(hwnd)
    except Exception as error:  # pylint: disable=broad-exception-caught
        logging.warning("Could not present the running Cartridges window: %s", error)
