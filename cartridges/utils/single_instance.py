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

GApplication already provides uniqueness over the D-Bus session bus,
which is always available on the Linux-only builds this project ships.
These helpers therefore only preserve the call sites in ``main``: the
guard itself lives in GApplication, not here.
"""

from typing import Callable


def acquire() -> bool:
    """Claim the single-instance lock. Always true on Linux."""
    return True


def release() -> None:
    """Release the single-instance lock. No-op on Linux."""
    return None


def watch_second_launch(callback: Callable[[], None]) -> None:
    """Watch for a second launch. No-op: GApplication handles activation."""
    return None


def present_running_instance() -> None:
    """Present the running instance. No-op: GApplication handles activation."""
    return None
