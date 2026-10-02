# open_uri.py
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

"""Hand a URL to the user's browser, safely and without warnings.

Two things live here that were previously scattered and slightly wrong at every
call site.

The scheme guard: several of these URLs are lifted out of a third-party RSS
feed, so only ``http``/``https`` is ever followed. A ``file:`` or ``javascript:``
URL that finds its way into a post must not become an OS-level launch just
because someone clicked a row.

The launcher: ``Gio.AppInfo.launch_default_for_uri(uri, None)`` was used
throughout, which trips a ``G_IS_APP_LAUNCH_CONTEXT`` assertion in GIO — it is
being handed no launch context at all, and the browser window it opens is
parented to nothing. :class:`Gtk.UriLauncher` builds the context itself and
accepts the window the click came from, which is what the platform expects.
"""

import logging
from typing import Any, Optional

from gi.repository import Gtk

from cartridges import shared

_ALLOWED_SCHEMES = ("http://", "https://")


def open_uri(uri: str, parent: Optional[Gtk.Window] = None) -> bool:
    """Open ``uri`` in the default browser.

    :param parent: window the click came from; defaults to the main window.
    :returns: False when the URL was empty or carried an unexpected scheme, in
        which case nothing was launched.
    """
    uri = (uri or "").strip()
    if not uri.startswith(_ALLOWED_SCHEMES):
        if uri:
            logging.warning("Refusing to open URI with unexpected scheme: %r", uri)
        return False

    def on_launched(launcher: Gtk.UriLauncher, result: Any) -> None:
        # A browser that refuses to start is the OS's business, not something
        # worth a dialog — but it should not vanish from the log either.
        try:
            launcher.launch_finish(result)
        except Exception as error:  # pylint: disable=broad-exception-caught
            logging.info("Could not open %s: %s", uri, error)

    Gtk.UriLauncher.new(uri).launch(parent or shared.win, None, on_launched)
    return True
