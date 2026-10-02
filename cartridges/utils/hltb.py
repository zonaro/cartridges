# hltb.py
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

"""Fetch completion time estimates from HowLongToBeat.

HowLongToBeat publishes no API. Its search is ``POST /api/search/site``, guarded
by a short-lived token from ``GET /api/search/site/init``, sent back as the
``x-auth-token`` header. The token itself encodes the IP and User-Agent, so the
credential and the search must come from one session sending identical
headers, and it expires: a 403 means "re-init and retry", which is exactly what
the site's own front end does.

Three routes exist, and :func:`fetch_times` picks between them:

* :meth:`HLTBHelper.resolve` searches by title. Needs the credential, and is
  only used the first time a game is looked up.
* :meth:`HLTBHelper.get_times_by_id` reads the server-rendered ``/game/<id>``
  page, where the same numbers are embedded in the markup. No credential, no
  handshake — so once a game's id is known, every later refresh takes this
  route and never touches the guarded endpoint.
* :meth:`HLTBHelper.resolve_chapters` assembles a breakdown for the few games
  the site only catalogues chapter by chapter (``_CHAPTER_SERIES``), which have
  no single page to point at.

Times are in seconds — the same unit :attr:`Game.playtime` uses — so the two
never need converting to sit side by side. They are never compared: this
module only provides a reference table.

The path is renamed every so often, and it is the only thing that changes: on
2026-08-24 ``/api/bleed`` started answering 404 overnight and the pair below
took its place, serving the same JSON, with the same three credential keys and
the same search body. Only the two constants had to move.

Until 2026-09-23 the init also handed out a honeypot pair (``hpKey``/``hpVal``)
that had to be echoed as ``x-hp-*`` headers and inside the body. The site
dropped it and so did this module; if it ever comes back, the symptom is every
search rejected with the init answering 200.

When it breaks again — and it will — the symptom is ``HowLongToBeat init
failed`` in the log, at DEBUG, wrapping a 404 from the site itself rather than
from Cloudflare. The new name is found by sweeping the homepage's JS chunks for
``"/api/…"``: the paths are in there as plain strings, and the search one is
whichever has an ``/init`` sibling.
"""

import logging
import re
import json
import threading
import time
from typing import Any, Optional, TypedDict

import requests
from requests.exceptions import RequestException

from cartridges.utils.download import MAX_RESPONSE_BYTES, read_capped
from cartridges.utils.name_cleaner import clean_for_search
from cartridges.utils.rate_limiter import RateLimiter
from cartridges.utils.title_match import TitleMatch, rank_candidates, tokenize

BASE_URL = "https://howlongtobeat.com"
SEARCH_URL = f"{BASE_URL}/api/search/site"
INIT_URL = f"{BASE_URL}/api/search/site/init"
REQUEST_TIMEOUT_SECONDS = 15
# Os corpos são lidos em stream com teto (`read_capped`, o mesmo de Steam, SGDB
# e wallhaven); o teto é o `MAX_RESPONSE_BYTES` importado acima, lido na hora
# da chamada. Passar dele é RequestException, que todo chamador daqui já trata
# como "o site não respondeu direito".

# The token embeds the User-Agent that asked for it, so every request in a
# session must send this exact string or the credential is rejected.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": BASE_URL,
    "Referer": f"{BASE_URL}/",
}

# Entry types that are not the base game. Their times are real but belong to
# something else, so showing them for the game would simply be wrong.
_IGNORED_GAME_TYPES = frozenset({"dlc", "mod"})

# The same filter for the chapter route, where "dlc" has to be allowed through.
# HowLongToBeat types a chaptered game's first chapter as a game and every
# later one as DLC of it — Poppy Playtime's chapters 2 to 5 are all "dlc" —
# so the ordinary filter threw away four fifths of the answer.
#
# Letting them in is safe precisely here and nowhere else: this route never
# substitutes an entry for the game, it lists each one as its own chapter, and
# a candidate only qualifies if the wanted title is its exact opening followed
# by a chapter marker. A soundtrack or a season pass cannot pass that.
_CHAPTER_IGNORED_GAME_TYPES = frozenset({"mod"})

# Games sold as one product that HowLongToBeat only catalogues chapter by
# chapter, with no entry for the whole thing. Their times are assembled from
# the chapters instead of matched to a single page.
#
# Deliberately a hand-written list rather than a rule. Every general version of
# "sum the entries that extend this title" is wrong somewhere expensive:
# "Final Fantasy" would collect its entire franchise, and "The Last of Us"
# would swallow "Part II" — a different game with different times. A new case
# is one line here, and until it is added the game simply has no times, which
# is the failure mode worth having.
_CHAPTER_SERIES = frozenset({"poppy playtime"})

# The word that has to open what a candidate adds to the title, immediately
# followed by the chapter number: "Poppy Playtime: Chapter 1 - A Tight Squeeze",
# "Poppy Playtime - Chapter 3". Requiring it in that exact position — rather
# than anywhere in the title — is what keeps "Poppy Playtime Forever: Chapter 9"
# out: that entry extends a different title and merely happens to be chaptered.
_CHAPTER_WORDS = frozenset({"chapter", "chapters", "episode", "capitulo"})

# When HowLongToBeat blocks an IP it answers 403 to everything, and a single
# search costs up to four requests (homepage, init, search, then a forced
# re-init and retry, because a 403 is indistinguishable from an expired
# credential). The startup backfill walks the whole library and only logs a
# failure, so a thousand-game library used to fire some four thousand requests
# at a site that had already refused the first one — which is both pointless
# and the surest way to stay blocked. After this many consecutive failures the
# helper stops asking for a while and fails every lookup locally instead.
_BREAKER_FAILURE_THRESHOLD = 3
_BREAKER_COOLDOWN_SECONDS = 15 * 60

# The three fields as they appear in the JSON embedded in a game page. The
# quote-and-colon anchor matters: without it, `"comp_main":` would also match
# `"comp_main_count":`, `"comp_main_avg":` and friends, which sit right beside
# it and mean something else entirely.
_PAGE_FIELD_RES = {
    "hltb_main": re.compile(r'"comp_main":\s*(\d+)'),
    "hltb_main_extra": re.compile(r'"comp_plus":\s*(\d+)'),
    "hltb_completionist": re.compile(r'"comp_100":\s*(\d+)'),
}


class HLTBError(Exception):
    """Base class for every HowLongToBeat failure."""


class HLTBGameNotFoundError(HLTBError):
    """No entry on HowLongToBeat matches the title with enough confidence."""


class HLTBUnavailableError(HLTBError):
    """The endpoint could not be reached, or refused the credential twice."""


class HLTBTimes(TypedDict, total=False):
    """Completion times for a game, in seconds.

    Mirrors the shape :meth:`Game.update_values` expects, so the result of a
    lookup applies to a game without any translation step.
    """

    hltb_id: Optional[int]
    hltb_main: int
    hltb_main_extra: int
    hltb_completionist: int
    # Per-chapter breakdown, for the handful of games that only exist on
    # HowLongToBeat as separate chapters (see ``_CHAPTER_SERIES``). Empty for
    # every normal game, so a lookup always states which of the two a game is
    # rather than leaving a stale breakdown behind.
    hltb_chapters: list[dict]


class HLTBRateLimiter(RateLimiter):
    """Rate limiter for HowLongToBeat.

    The site publishes no limit and has clearly been on the receiving end of
    scrapers, so this is deliberately gentle: roughly one request every 1.5 s
    sustained, with a small burst so a handful of games still import at once.
    Most refreshes cost a single request anyway — they go through the
    unguarded game page rather than the search.
    """

    refill_period_seconds = 60
    refill_period_tokens = 40
    burst_tokens = 10


def format_hltb_time(seconds: Optional[int]) -> str:
    """Render a completion time as compact hours ("16 h", "22,5 h").

    Uses a comma decimal to match :func:`format_playtime`, and drops the
    decimal when it would be ``,0``. Returns an em dash for missing data so a
    partially-filled row still lines up.
    """
    if not seconds or seconds <= 0:
        return "—"

    hours = round(seconds / 3600, 1)
    if hours < 0.1:
        # Under six minutes is a rounding artefact, not an estimate. Showing
        # "0 h" would read as "no data", which is a different claim.
        return _("< 0,1 h")

    text = str(int(hours)) if hours.is_integer() else str(hours).replace(".", ",")
    # The variable is a number of hours, e.g. "16" or "22,5"
    return _("{} h").format(text)


def _coerce_seconds(value: Any) -> Optional[int]:
    """Return ``value`` as a positive number of seconds, or None.

    HowLongToBeat sends 0 for "nobody has submitted this time", which must not
    become a displayed "0 h".
    """
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


def parse_entry(entry: dict) -> HLTBTimes:
    """Extract id and completion times from a raw search result.

    Only keys with real data are included, so applying the result to a game
    never blanks a time the game already had.
    """
    times = HLTBTimes()

    if (game_id := _coerce_seconds(entry.get("game_id"))) is not None:
        times["hltb_id"] = game_id

    for key, field in (
        ("hltb_main", "comp_main"),
        ("hltb_main_extra", "comp_plus"),
        ("hltb_completionist", "comp_100"),
    ):
        if (seconds := _coerce_seconds(entry.get(field))) is not None:
            times[key] = seconds  # type: ignore[literal-required]

    return times


_TIME_KEYS = ("hltb_main", "hltb_main_extra", "hltb_completionist")


def _n_times(times: HLTBTimes) -> int:
    """How many of the three estimates an entry actually carries."""
    return sum(1 for key in _TIME_KEYS if times.get(key))


def is_chapter_series(name: str) -> bool:
    """True when ``name`` is one of the hand-listed chaptered games."""
    return " ".join(tokenize(name)) in _CHAPTER_SERIES


def chapter_times(chapters: dict[int, dict]) -> HLTBTimes:
    """Turn per-chapter entries into a breakdown, chapter order preserved.

    No total is produced, on purpose. Adding the chapters up would invent a
    number that exists nowhere on HowLongToBeat, and it would be wrong for the
    common case anyway: someone playing Poppy Playtime asks how long *the
    chapter they are on* takes, not how long the series would take end to end.

    The three single-value fields and ``hltb_id`` are blanked rather than left
    alone: a game that had been matched to the wrong single entry before must
    not keep those leftovers next to its real breakdown, and a None id is what
    keeps later refreshes searching by name instead of pinning the game to one
    chapter's page.
    """
    times = HLTBTimes(
        hltb_id=None,
        hltb_chapters=[
            {
                "number": number,
                "name": chapters[number]["title"],
                "hltb_id": chapters[number]["times"].get("hltb_id"),
                **{key: chapters[number]["times"].get(key) for key in _TIME_KEYS},
            }
            for number in sorted(chapters)
        ],
    )
    for key in _TIME_KEYS:
        times[key] = None  # type: ignore[literal-required]
    return times


def has_times(times: HLTBTimes) -> bool:
    """True when the lookup produced something worth keeping.

    Either one of the three estimates, or a chapter breakdown — a chaptered
    game carries no single estimate at all, and callers use this to decide
    whether a lookup succeeded.
    """
    return any(times.get(key) for key in _TIME_KEYS) or bool(
        times.get("hltb_chapters")
    )


class HLTBHelper:
    """Helper around the HowLongToBeat search and game pages."""

    rate_limiter: RateLimiter

    def __init__(self, rate_limiter: Optional[RateLimiter] = None) -> None:
        self.rate_limiter = rate_limiter or HLTBRateLimiter()
        self._session = requests.Session()
        self._session.headers.update(_BROWSER_HEADERS)
        # One credential is shared by every lookup in the process; the lock
        # stops parallel import workers each fetching their own.
        self._credential: Optional[str] = None
        self._credential_lock = threading.Lock()
        # Circuit breaker state. It lives on the helper rather than on a caller
        # because the helper is the app-wide instance (see `shared_helper`):
        # the import pipeline, the backfill and the details dialog all go
        # through this object, so one of them tripping the breaker spares the
        # other two as well.
        self._breaker_lock = threading.Lock()
        self._failures = 0
        self._blocked_until = 0.0

    # region Circuit breaker

    def _breaker_check(self) -> None:
        """Fail a lookup locally while the breaker is open.

        :raises HLTBUnavailableError: if the cooldown has not elapsed
        """
        with self._breaker_lock:
            remaining = self._blocked_until - time.time()
        if remaining > 0:
            raise HLTBUnavailableError(
                "HowLongToBeat refused "
                f"{_BREAKER_FAILURE_THRESHOLD} lookups in a row, "
                f"waiting {int(remaining)}s"
            )

    def _breaker_record(self, ok: bool) -> None:
        """Count one outcome; open the breaker on a run of failures.

        Any answer at all closes it again: the site being reachable is the only
        thing the counter is tracking, so a miss for one game must not be held
        against the next.
        """
        with self._breaker_lock:
            if ok:
                self._failures = 0
                self._blocked_until = 0.0
                return
            self._failures += 1
            if self._failures < _BREAKER_FAILURE_THRESHOLD:
                return
            self._failures = 0
            self._blocked_until = time.time() + _BREAKER_COOLDOWN_SECONDS
        logging.info(
            "HowLongToBeat is not answering, pausing lookups for %d minutes",
            _BREAKER_COOLDOWN_SECONDS // 60,
        )

    # endregion

    # region Credential

    def _fetch_credential(self) -> Optional[str]:
        """Get a fresh search credential, or None if the site refused."""
        # The site loads the homepage before it ever searches; do the same so
        # any cookie the init endpoint expects is already in the jar.
        with self.rate_limiter:
            try:
                # stream + with: os cookies vêm dos headers, o corpo nunca é
                # lido — sem stream o requests baixava a homepage inteira para
                # ninguém.
                with self._session.get(
                    f"{BASE_URL}/", timeout=REQUEST_TIMEOUT_SECONDS, stream=True
                ):
                    pass
            except RequestException as error:
                logging.debug("HowLongToBeat homepage failed", exc_info=error)

        with self.rate_limiter:
            try:
                with self._session.get(
                    INIT_URL,
                    params={"t": int(time.time() * 1000)},
                    timeout=REQUEST_TIMEOUT_SECONDS,
                    stream=True,
                ) as response:
                    response.raise_for_status()
                    body = json.loads(read_capped(response, MAX_RESPONSE_BYTES))
            except (RequestException, ValueError) as error:
                logging.debug("HowLongToBeat init failed", exc_info=error)
                return None

        if not isinstance(body, dict):
            return None
        if not (token := body.get("token")):
            logging.debug("HowLongToBeat init returned no usable credential")
            return None
        return str(token)

    def _credential_or_fetch(self, force: bool = False) -> Optional[str]:
        with self._credential_lock:
            if self._credential is not None and not force:
                return self._credential
            self._credential = self._fetch_credential()
            return self._credential

    # endregion

    @staticmethod
    def _build_body(name: str) -> dict:
        """Build the search body as the site's code does."""
        return {
            "searchType": "games",
            "searchTerms": name.split(),
            "searchPage": 1,
            "size": 20,
            "searchOptions": {
                "games": {
                    "userId": 0,
                    "platform": "",
                    "sortCategory": "popular",
                    "rangeCategory": "main",
                    "rangeTime": {"min": None, "max": None},
                    "gameplay": {
                        "perspective": "",
                        "flow": "",
                        "genre": "",
                        "difficulty": "",
                    },
                    "rangeYear": {"min": "", "max": ""},
                    "modifier": "",
                },
                "users": {"sortCategory": "postcount"},
                "lists": {"sortCategory": "follows"},
                "filter": "",
                "sort": 0,
                "randomizer": 0,
            },
            "useCache": True,
        }

    def _post_search(
        self, name: str, credential: str
    ) -> tuple[Optional[list[dict]], bool]:
        """Run one search attempt.

        :return: ``(entries, stale)``. ``entries`` is None on failure; ``stale``
            is True only when the credential was rejected, which is the one
            case worth retrying with a fresh one.
        """
        headers = {
            "Content-Type": "application/json",
            "x-auth-token": credential,
        }

        with self.rate_limiter:
            try:
                with self._session.post(
                    SEARCH_URL,
                    json=self._build_body(name),
                    headers=headers,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                    stream=True,
                ) as response:
                    if response.status_code in (401, 403):
                        logging.debug("HowLongToBeat credential rejected, refreshing")
                        return None, True
                    response.raise_for_status()
                    body = json.loads(read_capped(response, MAX_RESPONSE_BYTES))
            except (RequestException, ValueError) as error:
                logging.debug("HowLongToBeat search failed", exc_info=error)
                return None, False

        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            return None, False
        return [entry for entry in data if isinstance(entry, dict)], False

    def search(self, name: str) -> list[dict]:
        """Return the raw HowLongToBeat entries matching ``name``.

        :raises HLTBUnavailableError: if the endpoint refused twice, or if the
            breaker is open after a run of such failures
        """
        name = (name or "").strip()
        if not name:
            raise HLTBGameNotFoundError()

        self._breaker_check()
        try:
            entries = self._search(name)
        except HLTBUnavailableError:
            self._breaker_record(ok=False)
            raise
        self._breaker_record(ok=True)
        return entries

    def _search(self, name: str) -> list[dict]:
        """One full search: credential, search, and one forced retry."""
        credential = self._credential_or_fetch()
        if credential is not None:
            entries, stale = self._post_search(name, credential)
            if entries is not None:
                return entries
            if not stale:
                raise HLTBUnavailableError("HowLongToBeat search failed")

        # Either there was no credential, or the one held has expired. One
        # forced refresh and one retry, matching what the site's front end
        # does — a second failure is a real outage, not a stale token.
        credential = self._credential_or_fetch(force=True)
        if credential is None:
            raise HLTBUnavailableError("could not obtain a HowLongToBeat credential")

        entries, _stale = self._post_search(name, credential)
        if entries is None:
            raise HLTBUnavailableError("HowLongToBeat rejected a fresh credential")
        return entries

    def find_candidates(self, name: str) -> list[tuple[dict, TitleMatch]]:
        """Return plausible entries for ``name``, best match first.

        Uses the same matcher as the Steam lookup, so a game is not confused
        with its sequel here either — the live search really does return
        "The Outer Worlds", its Spacer's Choice edition, "The Outer Worlds 2"
        and two DLCs together, so this filtering is doing real work.

        Entries are matched on their title and, failing that, on their alias,
        which is where HowLongToBeat keeps alternate and regional names.
        """
        entries = [
            entry
            for entry in self.search(name)
            if str(entry.get("game_type", "")).lower() not in _IGNORED_GAME_TYPES
        ]

        ranked = rank_candidates(name, entries, name_key="game_name")
        if not any(match.confident for _, match in ranked):
            by_alias = [entry for entry in entries if entry.get("game_alias")]
            for candidate, match in rank_candidates(
                name, by_alias, name_key="game_alias"
            ):
                if match.confident:
                    ranked.insert(0, (candidate, match))
                    break

        if not ranked:
            logging.debug("No plausible HowLongToBeat candidate for %s", name)
            raise HLTBGameNotFoundError()
        return ranked

    def resolve(self, name: str) -> HLTBTimes:
        """Find ``name`` on HowLongToBeat and return its completion times.

        Only a confident match is adopted, and only when it carries a time: an
        entry with all three fields at zero is a stub someone created, and
        returning it would replace "no data" with a misleading row of dashes.

        :raises HLTBGameNotFoundError: if nothing confident has usable times
        :raises HLTBUnavailableError: if the endpoint could not be reached
        """
        for candidate, match in self.find_candidates(name):
            if not match.confident:
                break
            times = parse_entry(candidate)
            if not has_times(times):
                logging.debug(
                    "HowLongToBeat entry %s has no times", candidate.get("game_name")
                )
                continue
            logging.debug(
                "HowLongToBeat match for %s: %s (%s, score %d)",
                name,
                candidate.get("game_name"),
                match.reason,
                match.score,
            )
            return times
        raise HLTBGameNotFoundError()

    def resolve_chapters(self, name: str) -> HLTBTimes:
        """Collect the chapters of a chaptered game, best entry per number.

        Only ever called for the titles in ``_CHAPTER_SERIES``. A candidate is
        collected only when the wanted title is its exact opening and the very
        next words are a chapter marker and its number: "Poppy Playtime:
        Chapter 1 - A Tight Squeeze" qualifies, "Poppy Playtime Forever:
        Chapter 9" does not, because what it adds before the marker makes it
        another title. Two chapters are the minimum: with one, this is not a
        chaptered game and the ordinary route deserves the chance to match it.

        DLC-typed entries count here — that is how the site files every chapter
        after the first — which is why this walks the raw search rather than
        reusing :meth:`find_candidates`.

        :raises HLTBGameNotFoundError: if fewer than two chapters were found
        :raises HLTBUnavailableError: if the endpoint could not be reached
        """
        wanted = tokenize(name)
        chapters: dict[int, dict] = {}

        for entry in self.search(name):
            if str(entry.get("game_type", "")).lower() in _CHAPTER_IGNORED_GAME_TYPES:
                continue
            title = str(entry.get("game_name") or "")
            tokens = tokenize(title)
            if tokens[: len(wanted)] != wanted:
                continue
            # `tokenize` canonicalises numerals, so "Chapter III" arrives here
            # as ["chapter", "3"] and needs no separate handling.
            rest = tokens[len(wanted) :]
            if len(rest) < 2 or rest[0] not in _CHAPTER_WORDS or not rest[1].isdigit():
                continue
            times = parse_entry(entry)
            if not any(times.get(key) for key in _TIME_KEYS):
                continue

            number = int(rest[1])
            known = chapters.get(number)
            # A chapter can appear twice (a re-release, a bundle listing). Keep
            # whichever entry carries more of the three estimates.
            if known is None or _n_times(times) > _n_times(known["times"]):
                chapters[number] = {"title": title, "times": times}

        if len(chapters) < 2:
            logging.debug("No HowLongToBeat chapters for %s", name)
            raise HLTBGameNotFoundError()

        logging.debug(
            "HowLongToBeat chapters for %s: %s",
            name,
            ", ".join(chapters[number]["title"] for number in sorted(chapters)),
        )
        return chapter_times(chapters)

    def get_times_by_id(self, hltb_id: int) -> HLTBTimes:
        """Read a known game's times from its public page.

        The game page is server-rendered with the same JSON the search returns,
        and needs no credential — so a game whose id is already known refreshes
        without ever touching the guarded endpoint, and without re-running a
        title search that could drift onto a different game.

        :raises HLTBGameNotFoundError: if the page carries no usable times
        :raises HLTBUnavailableError: if the page could not be fetched, or if
            the breaker is open after a run of such failures
        """
        self._breaker_check()
        with self.rate_limiter:
            try:
                with self._session.get(
                    f"{BASE_URL}/game/{int(hltb_id)}",
                    timeout=REQUEST_TIMEOUT_SECONDS,
                    stream=True,
                ) as response:
                    if response.status_code == 404:
                        raise HLTBGameNotFoundError()
                    response.raise_for_status()
                    page = read_capped(response, MAX_RESPONSE_BYTES).decode(
                        "utf-8", errors="replace"
                    )
            except RequestException as error:
                self._breaker_record(ok=False)
                raise HLTBUnavailableError("game page unreachable") from error

        # The page answered, whatever it happened to contain: a game with no
        # times on it says nothing about whether the site is reachable.
        self._breaker_record(ok=True)

        times = HLTBTimes(hltb_id=int(hltb_id))
        for key, pattern in _PAGE_FIELD_RES.items():
            if match := pattern.search(page):
                if (seconds := _coerce_seconds(match.group(1))) is not None:
                    times[key] = seconds  # type: ignore[literal-required]

        if not has_times(times):
            logging.debug("No HowLongToBeat times on the page for %s", hltb_id)
            raise HLTBGameNotFoundError()
        return times


def fetch_times(
    helper: HLTBHelper, name: str, hltb_id: Optional[int] = None
) -> HLTBTimes:
    """Get a game's times by the cheapest route that fits what is known.

    A known id goes straight to the public game page: no credential, one
    request, and — more importantly — no second title search that could quietly
    land on a different game than the one matched the first time. Searching is
    the fallback, for games never identified before and for ids that stopped
    resolving (entries do get merged or removed).

    Every caller that looks a game up goes through here — the import pipeline,
    the startup backfill and the button in the details dialog — so the three can
    never drift into disagreeing about which route wins.

    :raises HLTBGameNotFoundError: if nothing usable was found
    :raises HLTBUnavailableError: if HowLongToBeat could not be reached
    """
    cleaned = clean_for_search(name or "")
    if not cleaned:
        raise HLTBGameNotFoundError()

    # Chaptered games are handled before anything else, id included: there is
    # no single page for them, so an id sitting on such a game is a leftover
    # from a lookup that matched the wrong thing.
    if is_chapter_series(cleaned):
        try:
            return helper.resolve_chapters(cleaned)
        except HLTBGameNotFoundError:
            # The listing changed shape (chapters merged into one entry, say).
            # Fall through and try to match it like any other game.
            logging.debug("Falling back to a plain lookup for %s", cleaned)

    if hltb_id:
        try:
            return _plain(helper.get_times_by_id(int(hltb_id)))
        except HLTBGameNotFoundError:
            logging.debug("HowLongToBeat id %s is gone, searching by name", hltb_id)

    return _plain(helper.resolve(cleaned))


def _plain(times: HLTBTimes) -> HLTBTimes:
    """Mark a result as coming from a single entry, not from chapters.

    Stated rather than implied, so a game that used to be treated as chaptered
    does not keep a stale breakdown beside its new times.
    """
    times.setdefault("hltb_chapters", [])
    return times
