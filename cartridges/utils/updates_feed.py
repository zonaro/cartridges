# updates_feed.py
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

"""Read the repack site's RSS feed and pull the patched-game lists out of it.

The site itself sits behind an aggressive firewall/Cloudflare rule that blocks
plain HTML scraping, but the RSS feed is served straight through, so the whole
update signal is taken from there and nowhere else.

Only one kind of post matters: the periodic *Updates Digest*, whose body lists
the games that just received a patch. Each game is a collapsible spoiler shaped
like this::

    <div class="su-spoiler ...">
      <div class="su-spoiler-title"><span class="su-spoiler-icon"></span>Game</div>
      <div class="su-spoiler-content ...">
        <a href="https://fitgirl-repacks.site/game/">Repack page</a>
        ... download links ...
      </div>
    </div>

Two things are lifted from each spoiler: the *title* text (the bare game name,
used for matching) and the *Repack page* link inside the content (the game's own
page, offered to the user when they act on the notice). Everything else — the
file-host anchors and the ``.rar`` list items thick with version numbers — is
download plumbing and is deliberately ignored: reading it as a title yields
either noise or a numeric signature the matcher rightly rejects.
"""

import logging
import re
from datetime import timezone
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from time import time
from typing import Optional
from xml.etree import ElementTree

import requests

# The one and only data source. Kept here so the URL lives next to the code
# that understands the feed's shape.
#
# TLS is not cosmetic here: every string this module returns is trusted
# downstream. Game names steer the title matcher, repack URLs are written to
# each game's JSON and later handed to the OS's URI launcher, and post bodies
# are rendered in the window. Over cleartext any transparent proxy on the path
# could rewrite all three, so the feed is only ever read over https.
FEED_URL = "https://fitgirl-repacks.site/feed/"

# Only posts whose title starts with this are digests of patched games. Compared
# case-insensitively against the stripped title.
_DIGEST_TITLE_PREFIX = "updates digest"

# WordPress serves the post body in this RSS extension element.
_CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}encoded"

# CSS class tokens marking the parts of a per-game spoiler. The container holds
# the bare ``su-spoiler`` token; the header and body add a suffix.
_SPOILER_CLASS = "su-spoiler"
_SPOILER_TITLE_CLASS = "su-spoiler-title"
_SPOILER_CONTENT_CLASS = "su-spoiler-content"

# The link inside a spoiler that points at the game's own repack page (as
# opposed to the file-host download links). Matched by its anchor text.
_REPACK_LINK_TEXT = "repack page"

# A feed is small (a few dozen posts of text), but the cap still bounds a
# misbehaving or hostile response instead of reading it unboundedly into memory.
_MAX_FEED_BYTES = 8 * 1024 * 1024

# Some hosts return a bot page to a blank user agent. A boring browser-ish
# string is enough to get the plain feed.
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Cartridges/updates-feed"

_WHITESPACE_RE = re.compile(r"\s+")

# Ceilings on everything the feed can grow without asking. None of these is a
# display choice — they exist so a hostile or broken document cannot turn a
# parse into an allocation. A real digest lists tens of games with ordinary
# titles; these bounds sit an order of magnitude above that and are silently
# enforced rather than reported, because the feed is not ours to fix.
_MAX_ENTRIES = 2000  # spoilers taken from a single digest body
_MAX_TITLE_CHARS = 300  # characters kept from one spoiler header
_MAX_URL_CHARS = 2048  # characters kept from one href

# Only these two schemes ever survive parsing. The URL ends up persisted in the
# game's JSON and eventually reaches Gtk.UriLauncher; `open_uri` guards that
# call too, but a `javascript:`/`file:` URL should never make it as far as disk
# in the first place.
_ALLOWED_URL_SCHEMES = ("http://", "https://")

# A pubDate more than this far ahead of the clock is not a date, it is either a
# broken post or an attempt to poison dismissal: `Game.dismiss_update` folds the
# advertised timestamp into `update_dismissed_ts`, so acknowledging a post dated
# in the year 3000 would silence that game against every real patch forever.
_MAX_CLOCK_SKEW_SECONDS = 7 * 24 * 3600

# XML declaring a DTD is rejected outright. `xml.etree` expands internal
# entities without any budget, so a few hundred bytes of nested declarations
# expand to gigabytes ("billion laughs") and take the process with them. A
# syndication feed has no legitimate reason to carry a doctype, so refusing one
# costs nothing and closes the whole class of entity-expansion attacks without
# pulling in a third-party parser.
_DOCTYPE_RE = re.compile(r"<!DOCTYPE", re.IGNORECASE)

# Where the prolog ends: the first `<` that opens an element rather than a
# declaration (`<?xml`), a comment (`<!--`) or a doctype.
_ELEMENT_START_RE = re.compile(r"<[A-Za-z_]")


def _prolog_end(xml_text: str) -> int:
    """O índice onde o prólogo termina: o primeiro `<` que abre um elemento.

    Comentários e instruções de processamento são pulados por inteiro, não
    caractere a caractere: um regex solto encontrava o `<a` DENTRO de um
    comentário e encerrava a janela ali, então `<!-- <a --><!DOCTYPE …>`
    deixava o DOCTYPE real fora da busca e o guard não guardava nada. Tudo que
    é legal antes do elemento raiz — declaração XML, comentário, PI, espaço e
    o próprio DOCTYPE — fica dentro da janela; um comentário ou PI sem
    fechamento estende o prólogo até o fim, que só pode errar para o lado de
    recusar o feed.
    """
    position = 0
    length = len(xml_text)
    while position < length:
        start = xml_text.find("<", position)
        if start == -1:
            return length
        if xml_text.startswith("<?", start):
            end = xml_text.find("?>", start + 2)
            if end == -1:
                return length
            position = end + 2
        elif xml_text.startswith("<!--", start):
            end = xml_text.find("-->", start + 4)
            if end == -1:
                return length
            position = end + 3
        elif _ELEMENT_START_RE.match(xml_text, start):
            return start
        else:
            # `<!DOCTYPE`, `<!` de subset interno, ou lixo: continua dentro do
            # prólogo, avança um caractere e segue procurando o elemento raiz.
            position = start + 1
    return length


@dataclass(frozen=True)
class DigestEntry:
    """One patched game named in a digest: its title and its repack page."""

    name: str
    url: str  # the game's repack page, or "" when the link is missing


@dataclass(frozen=True)
class UpdatePost:
    """One *Updates Digest* post reduced to what the checker needs."""

    identifier: str  # guid or link — for logs only, never compared
    timestamp: int  # pubDate as epoch seconds; the post's ordering key
    title: str
    entries: tuple[DigestEntry, ...]

class _DigestParser(HTMLParser):
    """Extract ``(title, repack url)`` for every spoiler in a digest body.

    The parser keys off the spoiler's three nested divs: the ``su-spoiler``
    container bounds one game, ``su-spoiler-title`` holds the name, and the first
    "Repack page" anchor inside ``su-spoiler-content`` holds the link. An entry
    is emitted when the container closes, so a spoiler missing its link still
    yields the name (with an empty url) rather than being dropped.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.entries: list[DigestEntry] = []
        self._div_depth = 0

        self._in_spoiler = False
        self._spoiler_depth = 0
        self._name: Optional[str] = None
        self._url: Optional[str] = None

        self._capturing_title = False
        self._title_depth = 0
        self._title_buffer: list[str] = []

        self._in_content = False
        self._content_depth = 0

        self._in_anchor = False
        self._anchor_href: Optional[str] = None
        self._anchor_buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "div":
            classes = (dict(attrs).get("class") or "").split()
            if _SPOILER_CLASS in classes and not self._in_spoiler:
                self._in_spoiler = True
                self._spoiler_depth = self._div_depth
                self._name = None
                self._url = None
            if _SPOILER_TITLE_CLASS in classes and not self._capturing_title:
                self._capturing_title = True
                self._title_depth = self._div_depth
                self._title_buffer = []
            if _SPOILER_CONTENT_CLASS in classes and not self._in_content:
                self._in_content = True
                self._content_depth = self._div_depth
            self._div_depth += 1
        elif tag == "a" and not self._in_anchor:
            # Anchors do not nest in valid HTML, but a mangled body can still
            # open a second one. Honouring it would discard the outer anchor's
            # text and href halfway through; ignoring it keeps the first one
            # intact, which is the one the markup meant.
            self._in_anchor = True
            self._anchor_href = dict(attrs).get("href")
            self._anchor_buffer = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "div":
            # Floored, and the section tests below close on `<=` rather than
            # `==`. A stray `</div>` (routine in hand-written post bodies) used
            # to drive this counter arbitrarily negative; the machine happens to
            # survive that because every depth is recorded relative to the same
            # counter, but it survives by luck rather than by construction, and
            # only for as long as no `</div>` handler ever skips a value. This
            # states the invariant instead: depth is non-negative, and a section
            # closes when the document leaves it, never only on an exact hit.
            self._div_depth = max(0, self._div_depth - 1)
            if self._capturing_title and self._div_depth <= self._title_depth:
                self._capturing_title = False
                self._name = "".join(self._title_buffer).strip() or self._name
                self._title_buffer = []
            if self._in_content and self._div_depth <= self._content_depth:
                self._in_content = False
            if self._in_spoiler and self._div_depth <= self._spoiler_depth:
                self._in_spoiler = False
                if self._name and len(self.entries) < _MAX_ENTRIES:
                    self.entries.append(
                        DigestEntry(self._name, _safe_url(self._url))
                    )
                self._name = None
                self._url = None
        elif tag == "a" and self._in_anchor:
            self._in_anchor = False
            text = "".join(self._anchor_buffer).strip()
            self._anchor_buffer = []
            # First "Repack page" link inside the content wins; later download
            # anchors are ignored.
            if (
                self._in_content
                and self._url is None
                and text.casefold() == _REPACK_LINK_TEXT
            ):
                self._url = self._anchor_href or ""

    def handle_data(self, data: str) -> None:
        # Both buffers are bounded: an unclosed `su-spoiler-title` div would
        # otherwise accumulate the entire rest of the document, and a single
        # anchor's text is only ever compared against a six-word phrase.
        if self._capturing_title and len(self._title_buffer) < _MAX_TITLE_CHARS:
            self._title_buffer.append(data)
        if self._in_anchor and len(self._anchor_buffer) < _MAX_TITLE_CHARS:
            self._anchor_buffer.append(data)


def _safe_url(raw: Optional[str]) -> str:
    """Return ``raw`` if it is a plain web URL we are willing to keep, else "".

    The value comes from a third-party document and is persisted to the game's
    JSON before anything looks at it again, so the scheme is checked at ingest
    rather than only at the click site.
    """
    url = (raw or "").strip()
    if not url:
        return ""
    if len(url) > _MAX_URL_CHARS:
        logging.warning("Dropping over-long URL from the repack feed")
        return ""
    if not url.lower().startswith(_ALLOWED_URL_SCHEMES):
        logging.warning("Dropping feed URL with unexpected scheme: %r", url[:120])
        return ""
    return url


def parse_xml(xml_text: str) -> Optional[ElementTree.Element]:
    """Parse a feed document, refusing anything that declares a DTD.

    Shared by both readers of this feed so the entity-expansion guard cannot be
    applied in one and forgotten in the other.

    :returns: the root element, or None when the document is unusable — callers
        treat that exactly like an empty feed.
    """
    if not xml_text:
        return None

    # Only the prolog may legally carry a doctype. Scanning just that slice
    # keeps the literal string "<!DOCTYPE" inside a post body (harmless: expat
    # never interprets it there) from failing the whole feed; `_prolog_end`
    # explica por que a janela é andada, e não regexada.
    if _DOCTYPE_RE.search(xml_text, 0, _prolog_end(xml_text)):
        logging.warning("Refusing repack feed: document declares a DTD")
        return None

    try:
        return ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        logging.warning("Repack feed was not valid XML")
        return None


def _normalize_title(raw: str) -> Optional[str]:
    """Whitespace-normalise a spoiler title, or None if it is not a real name.

    The spoiler header already holds the bare title (no build note, no version),
    so unlike the download plumbing around it, nothing has to be cut off — only
    collapsed and trimmed.
    """
    text = _WHITESPACE_RE.sub(" ", raw or "").strip()
    if len(text) < 2:
        return None
    # A title is a game name, not a payload. Anything past the cap is not a name
    # the matcher could ever confirm, and it would still be carried into a toast
    # label and into every later comparison.
    return text[:_MAX_TITLE_CHARS]


def extract_entries(content_html: str) -> list[DigestEntry]:
    """Return the de-duplicated ``(name, url)`` entries named in a digest body.

    Order is preserved (first mention wins) so logs read the way the post does,
    but duplicate titles are dropped.
    """
    parser = _DigestParser()
    try:
        parser.feed(content_html or "")
        parser.close()
    except Exception:  # pylint: disable=broad-exception-caught
        # A malformed body must not sink the whole feed read; just yield nothing
        # for this post.
        logging.warning("Failed to parse an update digest body", exc_info=True)
        return []

    entries: list[DigestEntry] = []
    seen: set[str] = set()
    for entry in parser.entries:
        name = _normalize_title(entry.name)
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        entries.append(DigestEntry(name, entry.url))
    return entries


def is_update_digest(title: Optional[str]) -> bool:
    """True when a post title marks it as an updates digest."""
    return bool(title) and title.strip().casefold().startswith(_DIGEST_TITLE_PREFIX)


def _parse_timestamp(pubdate: Optional[str]) -> int:
    """Convert an RSS ``pubDate`` to epoch seconds, 0 when unusable.

    Two corrections on top of the plain conversion, both of which matter because
    this number is compared against values persisted on disk:

    A date with no usable timezone (``-0000``, or none at all) comes back as a
    naive datetime, and ``.timestamp()`` would then read it in the machine's
    local zone — shifting the ordering key by up to fourteen hours depending on
    where the user happens to be. RFC 5322 says an unknown zone means UTC, so
    that is what is assumed.

    A date far in the future is rejected outright rather than trusted. It is not
    only wrong ordering: ``Game.dismiss_update`` stores the advertised timestamp
    as the floor for every future notice, so one acknowledged post dated in the
    next century would permanently silence that game.
    """
    if not pubdate:
        return 0
    try:
        parsed = parsedate_to_datetime(pubdate)
    except (TypeError, ValueError, OverflowError):
        return 0

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    try:
        stamp = int(parsed.timestamp())
    except (OSError, OverflowError, ValueError):
        return 0

    if stamp < 0:
        return 0
    if stamp > int(time()) + _MAX_CLOCK_SKEW_SECONDS:
        logging.warning("Ignoring implausible feed date %r", pubdate[:120])
        return 0
    return stamp


def parse_feed(xml_text: str) -> list[UpdatePost]:
    """Parse a feed document into digest posts, newest first.

    Non-digest posts are skipped, as are digests we can extract no game from —
    an empty digest carries no signal and would only add noise downstream. A
    digest that yields nothing is logged, since that is the fingerprint of the
    site changing its markup out from under the parser.
    """
    root = parse_xml(xml_text)
    if root is None:
        return []

    posts: list[UpdatePost] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()[:_MAX_TITLE_CHARS]
        if not is_update_digest(title):
            continue

        content = item.findtext(_CONTENT_NS) or item.findtext("description") or ""
        entries = extract_entries(content)
        if not entries:
            logging.warning("Update digest %r yielded no game names", title)
            continue

        identifier = (item.findtext("guid") or item.findtext("link") or title).strip()[
            :_MAX_URL_CHARS
        ]
        timestamp = _parse_timestamp(item.findtext("pubDate"))
        logging.debug(
            "Digest %r (%d): %s",
            title,
            timestamp,
            ", ".join(entry.name for entry in entries),
        )

        posts.append(
            UpdatePost(
                identifier=identifier,
                timestamp=timestamp,
                title=title,
                entries=tuple(entries),
            )
        )

    posts.sort(key=lambda post: post.timestamp, reverse=True)
    return posts


def fetch_feed_text(
    url: str = FEED_URL, timeout: float = 15, max_bytes: int = _MAX_FEED_BYTES
) -> str:
    """Download the feed document as text, capping the response size.

    Split out from :func:`fetch_feed` because the same document feeds two
    unrelated readers — the digest parser here and the news parser in
    :mod:`cartridges.utils.news_feed` — and the transport details (size cap,
    user agent, encoding fallback) should only exist once.

    :raises requests.RequestException: on any network/HTTP failure — callers
        treat a failed poll as "no news", never as an error worth a dialog.
    """
    with requests.get(
        url,
        timeout=timeout,
        stream=True,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/rss+xml, */*"},
    ) as response:
        response.raise_for_status()

        buffer = bytearray()
        for chunk in response.iter_content(chunk_size=64 * 1024):
            buffer.extend(chunk)
            if len(buffer) > max_bytes:
                logging.warning("Repack feed exceeded %d bytes; truncating", max_bytes)
                break

        # `response.encoding` is not the server's answer, it is requests'
        # guess: for any `text/*` reply without an explicit charset it returns
        # ISO-8859-1 per RFC 2616, which silently mojibakes a UTF-8 feed. That
        # is not cosmetic here — a mangled "Assassin's Creed Odyssée" no longer
        # matches the library title, so the game just stops being notified.
        # Trust the header only when it actually declares a charset.
        content_type = response.headers.get("Content-Type", "")
        declared = response.encoding if "charset=" in content_type.lower() else None

    return _decode(bytes(buffer), declared)


def _decode(raw: bytes, declared: Optional[str]) -> str:
    """Decode a feed body: declared charset, then its XML declaration, then UTF-8."""
    for encoding in (declared, _declared_xml_encoding(raw), "utf-8"):
        if not encoding:
            continue
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    # Nothing decoded cleanly; keep whatever is readable rather than losing the
    # whole poll to one bad byte.
    return raw.decode("utf-8", errors="replace")


def _declared_xml_encoding(raw: bytes) -> Optional[str]:
    """Read the ``encoding=`` of an XML declaration, if the document has one."""
    match = re.match(
        rb"""\s*<\?xml[^>]*?encoding\s*=\s*["']([A-Za-z0-9._\-]+)["']""", raw[:512]
    )
    return match.group(1).decode("ascii", errors="replace") if match else None


def fetch_feed(
    url: str = FEED_URL, timeout: float = 15, max_bytes: int = _MAX_FEED_BYTES
) -> list[UpdatePost]:
    """Download and parse the feed into digest posts.

    :raises requests.RequestException: see :func:`fetch_feed_text`.
    """
    return parse_feed(fetch_feed_text(url, timeout, max_bytes))
