# news_feed.py
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

"""Read repack and giveaway RSS feeds as *news*: everything that is not a digest.

One document, two readers with opposite appetites.
:mod:`cartridges.utils.updates_feed` wants the periodic *Updates Digest* posts,
whose bodies are machine fodder for the per-game patch notice. This module wants
the other half — new repacks, site announcements, anything a person would
actually sit and read — and drops the digests on the floor.

That exclusion is the whole point of the split and it only goes one way. A
digest never reaches the Novidades page: its body is a wall of collapsible
spoiler markup that reads as noise, and every game it names is already surfaced
on that game's own details page. Conversely a news post is never handed to the
update matcher, which would happily mistake a repack announcement for a patch.

The page aggregates sources into one list (see ``NEWS_FEEDS``): the repack feed
above, the GamerPower "all giveaways" feed and the FreeToKeep "everything free
to keep" feed. The GamerPower sub-feeds (pc/steam/xbox/...) overlap with its
``/rss/giveaways`` aggregate, so only the aggregate is polled. Each post carries
its source label so the row can show where it came from.

Portuguese readers get a fourth source. The TrinityWeb Games feed publishes
exclusively in Portuguese (its ``<language>`` is pt-BR), so it is listed
separately in ``_PT_BR_FEEDS`` and only polled when the UI language is
Brazilian Portuguese — see :func:`feeds_for_language`. In any other locale the
rows would be prose nobody can read, so the source is skipped entirely rather
than shown and ignored.

Only the parts of an ``<item>`` that a list row can show are kept: title, link,
publication date, source and a short plain-text excerpt cut out of the HTML summary.
"""

import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser

import requests

# pylint: disable=protected-access
# The digest reader owns the feed's transport and its date/title conventions;
# borrowing them here keeps both halves of the same document reading it the same
# way instead of drifting apart.
from cartridges.utils.updates_feed import (
    FEED_URL,
    _MAX_FEED_BYTES,
    _MAX_TITLE_CHARS,
    _parse_timestamp,
    _safe_url,
    fetch_feed_text,
    is_update_digest,
    parse_xml,
)

# The GamerPower "all giveaways" aggregate. The per-platform/per-type
# sub-feeds (pc, steam, xbox, playstation, nintendo, mobile, games, loot)
# overlap with it, so only this one is polled to avoid duplicates.
GAMERPOWER_FEED_URL = "https://www.gamerpower.com/rss/giveaways"

# FreeToKeep "everything free to keep" aggregate. The per-store feeds
# (/feed/steam.xml, /feed/epic.xml, ...) overlap with it, so only the
# combined feed is polled. The JSON API (/api/v1/...) is out of scope.
FREETOKEEP_FEED_URL = "https://freetokeep.gg/feed.xml"

# TrinityWeb Games blog. The feed declares <language>pt-BR</language> and its
# posts are written in Portuguese, so it is polled only for a pt-BR interface
# (see _PT_BR_FEEDS / feeds_for_language). It is not part of NEWS_FEEDS.
TRINITYWEB_FEED_URL = "https://games.thetrinityweb.com.br/feed.xml"

# (source label, feed url) polled by fetch_all_news, in order. Source labels
# are proper nouns shown verbatim in the UI, never translated.
NEWS_FEEDS: tuple[tuple[str, str], ...] = (
    ("FitGirl", FEED_URL),
    ("GamerPower", GAMERPOWER_FEED_URL),
    ("FreeToKeep", FREETOKEEP_FEED_URL),
)

# Sources that only make sense for one UI language, appended to NEWS_FEEDS by
# feeds_for_language when the locale matches. Today only pt-BR.
_PT_BR_FEEDS: tuple[tuple[str, str], ...] = (("TrinityWeb", TRINITYWEB_FEED_URL),)

# Every source the module can ever resolve a label for, gated or not, so
# fetch_news can name a post even when it was reached through a gated feed.
_SOURCE_BY_URL = {url: source for source, url in NEWS_FEEDS + _PT_BR_FEEDS}

_CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}encoded"

_MEDIA_NS = "{http://search.yahoo.com/mrss/}"

# Hard ceiling on the excerpt. Rows are expanders that show the whole text, so
# this is not a display choice — it is a sanity guard for the case where a post
# ships its entire body in <description> instead of a summary.
_SUMMARY_MAX_CHARS = 4000

# WordPress appends a "read the rest" teaser to the excerpt. It is a link in the
# markup, so it survives tag stripping as a dangling sentence; cut it off.
_TEASER_RE = re.compile(
    r"\s*(continue reading|read more|leia mais)\b.*$", re.IGNORECASE | re.DOTALL
)

_WHITESPACE_RE = re.compile(r"\s+")

# Tags whose text content is markup plumbing, never prose.
_SKIPPED_TAGS = frozenset({"script", "style"})

# Ceiling on how many rows one feed may produce. The page is a list of recent
# posts; a document claiming a hundred thousand of them is not one.
_MAX_POSTS = 500

# Ceiling on the tag shown beside a post's date. It is a WordPress category,
# not free text, and it shares a single line with the date.
_MAX_CATEGORY_CHARS = 120


@dataclass(frozen=True)
class NewsPost:
    """One readable feed post, reduced to what a list row shows."""

    identifier: str  # guid or link — used to tell posts apart, not to order them
    timestamp: int  # pubDate as epoch seconds; the post's ordering key
    title: str
    url: str  # the post's own page, or "" when the link is missing
    summary: str  # plain-text excerpt, possibly ""
    category: str  # first <category>, or "" — shown as a tag on the row
    source: str = ""  # feed label from NEWS_FEEDS, shown before date/category
    image: str = ""  # thumbnail URL from enclosure/media:content, or ""


class _TextExtractor(HTMLParser):
    """Collect the visible text of an HTML fragment, ignoring the markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _SKIPPED_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.chunks.append(data)


def strip_html(fragment: str) -> str:
    """Return the plain text of an HTML fragment, whitespace collapsed.

    A malformed fragment yields whatever was parsed before it broke rather than
    raising: an unreadable excerpt costs a row its subtitle, never the page.
    """
    parser = _TextExtractor()
    try:
        parser.feed(fragment or "")
        parser.close()
    except Exception:  # pylint: disable=broad-exception-caught
        logging.debug("Failed to parse a news post summary", exc_info=True)

    return _WHITESPACE_RE.sub(" ", "".join(parser.chunks)).strip()


def summarize(fragment: str, max_chars: int = _SUMMARY_MAX_CHARS) -> str:
    """Turn a post's HTML summary into plain text, kept whole.

    Nothing is shortened for presentation: the news rows expand to show the
    entire excerpt, so trimming here would throw away text the reader asked
    for. The cap only catches a runaway body, and even then the cut lands on a
    word boundary with an ellipsis rather than mid-word.
    """
    # Cutting the teaser off often leaves the dash or spacing that introduced
    # it. A trailing "…" is kept — the excerpt really is truncated — and a full
    # stop is never touched, or every excerpt would lose its last sentence.
    text = _TEASER_RE.sub("", strip_html(fragment)).strip(" \t–—-")
    if len(text) <= max_chars:
        return text

    head = text[: max_chars + 1]
    cut = head.rfind(" ")
    return (head[:cut] if cut > 0 else text[:max_chars]).rstrip(",;:.") + "…"


def _post_image(item: object) -> str:
    """Return the item's thumbnail URL, or "" when it has none usable.

    FreeToKeep serves it as ``<enclosure url type="image/...">`` and
    GamerPower as ``<media:content url>``. The value is scheme-checked like
    every other URL out of the feed; the bytes themselves are only trusted
    after the loader decodes them as an image.
    """
    find = getattr(item, "find", None)
    if not callable(find):
        return ""
    enclosure = find("enclosure")
    if enclosure is not None:
        claimed = (enclosure.get("type") or "").strip().lower()
        if not claimed or claimed.startswith("image/"):
            image = _safe_url(enclosure.get("url"))
            if image:
                return image
    media = find(f"{_MEDIA_NS}content")
    if media is not None:
        claimed = (media.get("type") or "").strip().lower()
        if not claimed or claimed.startswith("image/"):
            image = _safe_url(media.get("url"))
            if image:
                return image
    return ""


def parse_news(xml_text: str, source: str = "") -> list[NewsPost]:
    """Parse a feed document into readable posts, newest first.

    Update digests are skipped — see this module's docstring — as are items with
    no title, which carry nothing a row could display.
    """
    root = parse_xml(xml_text)
    if root is None:
        return []

    posts: list[NewsPost] = []
    seen: set[str] = set()
    for item in root.iter("item"):
        if len(posts) >= _MAX_POSTS:
            logging.warning("News feed carried more than %d posts", _MAX_POSTS)
            break

        title = _WHITESPACE_RE.sub(" ", (item.findtext("title") or "")).strip()
        if not title:
            continue
        # The one exclusion this module exists for.
        if is_update_digest(title):
            continue
        title = title[:_MAX_TITLE_CHARS]

        # The link is displayed and eventually launched, so it goes through the
        # same scheme guard as a digest's repack URL. A rejected link costs the
        # row its "Abrir no navegador" action; the post itself still reads.
        link = _safe_url(item.findtext("link"))
        identifier = (item.findtext("guid") or link or title).strip()

        # One post, one row: a feed that repeats a guid (a re-publish, or a
        # concatenated document) would otherwise stack identical rows.
        if identifier in seen:
            continue
        seen.add(identifier)

        summary = summarize(
            item.findtext("description") or item.findtext(_CONTENT_NS) or ""
        )
        category = (item.findtext("category") or "").strip()[:_MAX_CATEGORY_CHARS]

        posts.append(
            NewsPost(
                identifier=identifier,
                timestamp=_parse_timestamp(item.findtext("pubDate") or ""),
                title=title,
                url=link,
                summary=summary,
                category=category,
                source=source,
                image=_post_image(item),
            )
        )

    posts.sort(key=lambda post: post.timestamp, reverse=True)
    logging.debug("News feed yielded %d news posts", len(posts))
    return posts


def fetch_news(
    url: str = FEED_URL, timeout: float = 15, max_bytes: int = _MAX_FEED_BYTES
) -> list[NewsPost]:
    """Download and parse one feed into readable posts.

    :raises requests.RequestException: on any network/HTTP failure — the caller
        treats a failed poll as "nothing new", never as an error worth a dialog.
    """
    return parse_news(
        fetch_feed_text(url, timeout, max_bytes),
        source=_SOURCE_BY_URL.get(url, ""),
    )


def is_brazilian_portuguese(language: str) -> bool:
    """True when a locale or ``LANGUAGE`` list means Brazilian Portuguese.

    Accepts what the environment actually holds: a single locale
    (``pt_BR.UTF-8``), a hyphenated tag (``pt-BR``) or gettext's colon list
    (``pt_BR:pt:en``). Any variant spelling is normalised before comparing, so
    the gate does not hinge on how the locale was written.
    """
    for candidate in (language or "").split(":"):
        tag = candidate.strip().split(".")[0].split("@")[0].replace("-", "_")
        if tag.lower() == "pt_br":
            return True
    return False


def feeds_for_language(language: str = "") -> tuple[tuple[str, str], ...]:
    """The feeds to poll for a UI language: the base list plus gated sources.

    A single-language source is appended only when its locale matches, so a
    plain call with an empty language gets exactly ``NEWS_FEEDS`` and callers
    that never care about language stay unaffected.
    """
    if is_brazilian_portuguese(language):
        return NEWS_FEEDS + _PT_BR_FEEDS
    return NEWS_FEEDS


def fetch_all_news(
    timeout: float = 15, max_bytes: int = _MAX_FEED_BYTES, language: str = ""
) -> list[NewsPost]:
    """Download the feeds for ``language`` and merge the posts, newest first.

    ``language`` selects the source set through :func:`feeds_for_language`; the
    pt-BR-only sources are polled only for a Brazilian Portuguese interface. A
    feed that fails (network, HTTP, oversized) is skipped with a log line; it
    never sinks the other feeds. Identifiers repeated across feeds resolve to
    the newest post. The merged list is capped at _MAX_POSTS.
    """
    merged: dict[str, NewsPost] = {}
    failures = 0
    for source, url in feeds_for_language(language):
        try:
            posts = fetch_news(url, timeout, max_bytes)
        except requests.RequestException as error:
            failures += 1
            logging.info("News feed poll failed for %s: %s", source, error)
            continue
        except Exception:  # pylint: disable=broad-exception-caught
            failures += 1
            logging.warning(
                "Unexpected error polling news feed %s", source, exc_info=True
            )
            continue
        for post in posts:
            previous = merged.get(post.identifier)
            if previous is None or post.timestamp > previous.timestamp:
                merged[post.identifier] = post

    result = sorted(merged.values(), key=lambda post: post.timestamp, reverse=True)
    if len(result) > _MAX_POSTS:
        logging.warning("News feeds carried more than %d posts", _MAX_POSTS)
        del result[_MAX_POSTS:]
    logging.debug(
        "News feeds yielded %d posts (%d feeds failed)", len(result), failures
    )
    return result
