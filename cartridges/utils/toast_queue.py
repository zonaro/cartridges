# toast_queue.py
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

"""One toast at a time, in the order they were raised.

Adw.ToastOverlay keeps a queue of its own, but a HIGH priority toast jumps it:
it replaces whatever is on screen and pushes that one back into the queue. When
several unrelated things speak up at once — the startup import, the update
check, the import summary — the result is a toast being cut off mid-life and
then coming back, which reads like a glitch.

This class owns the overlay instead. Only ever one toast is handed to it, and
the next one is added once the previous is gone, so a burst of toasts plays as a
queue: no toast is interrupted, none is shown twice.

Because a queued toast is not in the overlay yet, `Adw.Toast.dismiss` silently
does nothing to it — always cancel through :meth:`ToastQueue.dismiss`, which
handles both cases and still emits ``dismissed`` so callers keeping their own
bookkeeping (the undo toasts) hear about it either way.
"""

from collections import deque
from typing import Optional

from gi.repository import Adw, GLib


class ToastQueue:
    """Serializes every toast that goes through a single Adw.ToastOverlay."""

    def __init__(self, overlay: Adw.ToastOverlay) -> None:
        self._overlay = overlay
        self._pending: deque = deque()
        self._current: Optional[Adw.Toast] = None
        self._dismissed_handler_id: int = 0
        # Toasts that asked to be taken down N seconds after they appear, while
        # they were still waiting their turn. See `dismiss_after`.
        self._delayed: dict = {}
        self._timeout_id: int = 0

    # -- queueing -------------------------------------------------------------

    def add(self, toast: Adw.Toast) -> None:
        """Queue ``toast``, showing it right away if nothing else is up."""
        if toast is self._current or toast in self._pending:
            return
        self._pending.append(toast)
        self._show_next()

    def dismiss(self, toast: Optional[Adw.Toast]) -> None:
        """Take ``toast`` down, whether it is on screen or still queued."""
        if toast is None:
            return

        if toast in self._pending:
            self._pending.remove(toast)
            self._delayed.pop(toast, None)
            # It never reached the overlay, so libadwaita won't emit anything
            # for it. Emit "dismissed" ourselves: to everyone else this toast
            # is over, and some of them clean up on that signal.
            toast.emit("dismissed")
            return

        # On screen (or not ours at all): let libadwaita do it. The "dismissed"
        # signal brings us back in `_on_dismissed` to start the next one.
        toast.dismiss()

    def dismiss_after(self, toast: Optional[Adw.Toast], seconds: int) -> None:
        """Take ``toast`` down ``seconds`` after it has *appeared*.

        For toasts with a timeout of 0 that something else decides the lifetime
        of. The countdown only starts once the toast has its turn — armed while
        it is still queued, it would expire before anyone ever saw it.
        """
        if toast is None:
            return
        if toast is self._current:
            self._arm_timeout(toast, seconds)
        elif toast in self._pending:
            self._delayed[toast] = seconds

    # -- internals ------------------------------------------------------------

    def _show_next(self) -> None:
        if self._current is not None or not self._pending:
            return

        toast = self._pending.popleft()
        self._current = toast
        self._dismissed_handler_id = toast.connect("dismissed", self._on_dismissed)
        self._overlay.add_toast(toast)

        if (seconds := self._delayed.pop(toast, None)) is not None:
            self._arm_timeout(toast, seconds)

    def _on_dismissed(self, toast: Adw.Toast) -> None:
        if toast is not self._current:
            return

        self._cancel_timeout()
        if self._dismissed_handler_id:
            toast.disconnect(self._dismissed_handler_id)
            self._dismissed_handler_id = 0
        self._current = None

        # Off the signal emission before touching the overlay again, so the next
        # toast is added once this one has finished being taken down.
        GLib.idle_add(self._show_next_idle)

    def _show_next_idle(self) -> bool:
        self._show_next()
        return False

    def _arm_timeout(self, toast: Adw.Toast, seconds: int) -> None:
        self._cancel_timeout()
        self._timeout_id = GLib.timeout_add_seconds(seconds, self._on_timeout, toast)

    def _cancel_timeout(self) -> None:
        if self._timeout_id:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = 0

    def _on_timeout(self, toast: Adw.Toast) -> bool:
        self._timeout_id = 0
        if toast is self._current:
            toast.dismiss()
        return False
