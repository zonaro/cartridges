# news_checker.py
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

"""Watch the news feeds for readable posts and drive the header-bar badge.

Same shape as :class:`~cartridges.utils.updates_checker.UpdatesChecker` — a
worker thread does the network, everything that touches the UI is bounced back
onto the main thread with ``GLib.idle_add`` — but the two watch feeds for
opposite things and share nothing but the download helper. This one holds the
posts it fetched so opening the Novidades page is instant and re-opening it
costs no request.

Novelty is a single number. ``news-last-seen-ts`` records the publication date
of the newest post the user has already been shown; the badge is lit whenever
the feed carries something newer. Opening the page acknowledges everything on
it, which is why the whole "unread" state survives restarts without keeping a
list of post ids anywhere.

The very first successful poll after installation is treated as a baseline
rather than as news: with no record of what the user has seen, every post in the
feed is technically unseen, and lighting the badge for a back-catalogue nobody
missed would make it mean nothing. That poll stores the timestamp silently, and
the badge from then on only ever marks posts that landed while the user was
watching.
"""

import logging
import os
import threading
from typing import Optional

import requests
from gi.repository import GLib, GObject

from cartridges import shared
from cartridges.utils.news_feed import NewsPost, fetch_all_news

# Hours between background polls. 0 turns the recurring poll (and with it the
# badge) off; the page still fetches on demand when opened.
_INTERVAL_KEY = "news-check-interval"

# Newest pubDate the user has already seen, in the State schema because it is
# window state, not a preference.
_LAST_SEEN_KEY = "news-last-seen-ts"


def _ui_language() -> str:
    """The language the interface actually renders in.

    Mirrors ``cartridges/jolven.in``: an explicit ``language`` preference wins;
    ``auto`` follows the system locale, read with gettext's own precedence
    (LANGUAGE, then LC_ALL, LC_MESSAGES, LANG). Read on the main thread because
    GSettings is not meant to be touched from the poll worker.
    """
    try:
        language = shared.schema.get_string("language")
    except Exception:  # pylint: disable=broad-exception-caught
        language = "auto"
    if language and language != "auto":
        return language
    for variable in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(variable)
        if value:
            return value
    return ""


class NewsChecker(GObject.Object):
    """Owns the recurring news poll, the cached posts and the unread flag."""

    __gtype_name__ = "NewsChecker"

    __gsignals__ = {
        # A poll came back with a new list of posts; `posts` has been replaced.
        "posts-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # A poll finished. The argument is True when it produced posts, False
        # when the network (or the feed) let us down — the page uses it to stop
        # its spinner and pick between the list and the "no news" status page.
        "poll-finished": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
        # The unread flag flipped. The argument is the new state, so the badge
        # handler needs no access to the checker at all.
        "unseen-changed": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
    }

    def __init__(self) -> None:
        super().__init__()
        # GLib source id of the recurring timer, so it can be cancelled and
        # never scheduled twice.
        self._timeout_id: Optional[int] = None
        # A single in-flight poll guard: a slow request must not stack up behind
        # the timer firing again, or behind an impatient refresh button. The
        # test-and-set is locked because it is the only thing keeping the worker
        # count at one, and the refresh button can press it at any moment.
        self._lock = threading.Lock()
        self._running = False
        # Set once the app is shutting down, so a poll that is already on the
        # wire cannot emit into a window that has gone away.
        self._stopped = False
        self._posts: tuple[NewsPost, ...] = ()
        self._has_unseen = False

    # -- state ---------------------------------------------------------------

    @property
    def posts(self) -> tuple[NewsPost, ...]:
        """The posts from the last successful poll, newest first."""
        return self._posts

    @property
    def loading(self) -> bool:
        """True while a poll is in flight."""
        return self._running

    @property
    def has_unseen(self) -> bool:
        """True when the feed carries a post newer than the last one seen."""
        return self._has_unseen

    # -- scheduling ----------------------------------------------------------

    def start(self) -> None:
        """Kick off an immediate poll and arm the recurring timer.

        Safe to call once at startup. Unlike the update checker this does not
        wait for any game to opt in: the news page is a global feature, so the
        badge has to be able to light up on its own.
        """
        self._stopped = False
        self.check_async()

        # Cancelled before the setting is read: bailing out first on a 0
        # interval used to leave an already-armed timer polling forever.
        if self._timeout_id is not None:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = None

        interval_hours = shared.schema.get_int(_INTERVAL_KEY)
        if interval_hours <= 0:
            return
        self._timeout_id = GLib.timeout_add_seconds(
            interval_hours * 3600, self._on_timer
        )

    def stop(self) -> None:
        """Cancel the recurring timer and disown any poll still in flight."""
        self._stopped = True
        if self._timeout_id is not None:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = None

    def _on_timer(self) -> bool:
        self.check_async()
        return True  # keep the timer running

    # -- the poll ------------------------------------------------------------

    def check_async(self) -> None:
        """Run one poll off the main thread, unless one is already running."""
        if self._stopped:
            return
        with self._lock:
            if self._running:
                return
            self._running = True
        # Read the language here, on the main thread: the poll worker must not
        # touch GSettings (see _ui_language).
        language = _ui_language()
        threading.Thread(
            target=self._poll_thread, args=(language,), daemon=True
        ).start()

    def _poll_thread(self, language: str) -> None:
        posts: Optional[list[NewsPost]] = None
        try:
            posts = fetch_all_news(language=language)
        except requests.RequestException as error:
            # A failed poll is a non-event: the cached posts (if any) stay up
            # and the badge does not change. Info level because a flaky network
            # is expected, not exceptional.
            logging.info("News feed poll failed: %s", error)
        except Exception:  # pylint: disable=broad-exception-caught
            logging.warning("Unexpected error polling news feeds", exc_info=True)
        finally:
            GLib.idle_add(self._apply, posts)

    # -- applying results (main thread) --------------------------------------

    def _apply(self, posts: Optional[list[NewsPost]]) -> bool:
        """Fold a finished poll into the cached state. Runs on the main thread."""
        with self._lock:
            self._running = False

        # Every listener on these signals is a widget of a window that shutdown
        # is in the middle of tearing down. Nothing to announce to it.
        if self._stopped:
            return False

        if not posts:
            # Keep whatever was cached: a dropped connection should not empty a
            # page the user is looking at. Still a failure, though: the cache
            # being there does not make "Feed atualizado" true.
            self.emit("poll-finished", False)
            return False

        fetched = tuple(posts)
        # `posts-changed` makes the window throw away every row and rebuild the
        # list, which also resets the scroll position to the top. Emitting it on
        # every poll meant the six-hourly tick yanked the page out from under
        # anyone reading it, to redraw a list that was usually byte-identical.
        # The posts are frozen dataclasses, so equality is a real comparison.
        if fetched != self._posts:
            self._posts = fetched
            self.emit("posts-changed")

        self._refresh_unseen()
        self.emit("poll-finished", True)
        return False

    def _newest_timestamp(self) -> int:
        """pubDate of the newest cached post, 0 when there are none."""
        return max((post.timestamp for post in self._posts), default=0)

    def _refresh_unseen(self) -> None:
        """Recompute the unread flag and announce it if it moved."""
        newest = self._newest_timestamp()
        last_seen = shared.state_schema.get_int64(_LAST_SEEN_KEY)

        # First run ever: adopt the feed as the baseline instead of claiming
        # every existing post is news (see the module docstring).
        if last_seen == 0 and newest:
            shared.state_schema.set_int64(_LAST_SEEN_KEY, newest)
            last_seen = newest

        self._set_unseen(newest > last_seen)

    def _set_unseen(self, unseen: bool) -> None:
        if unseen == self._has_unseen:
            return
        self._has_unseen = unseen
        logging.debug("News badge %s", "raised" if unseen else "cleared")
        self.emit("unseen-changed", unseen)

    def mark_seen(self) -> None:
        """Acknowledge every cached post; called when the page is opened."""
        newest = self._newest_timestamp()
        if newest > shared.state_schema.get_int64(_LAST_SEEN_KEY):
            shared.state_schema.set_int64(_LAST_SEEN_KEY, newest)
        self._set_unseen(False)
