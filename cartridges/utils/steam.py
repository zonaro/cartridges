# steam.py
#
# Copyright 2022-2023 redclaw
# Copyright 2023 Geoffrey Coulaud
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

import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Callable, Optional, TypedDict

import requests
from requests.exceptions import HTTPError, RequestException

from cartridges import shared
from cartridges.utils.download import get_capped
from cartridges.utils.rate_limiter import RateLimiter
from cartridges.utils.steam_genre import pick_genre
from cartridges.utils.title_match import TitleMatch, rank_candidates


# Bumped whenever a Steam lookup starts producing a field it did not before.
# A game records the version it was last looked up at, which is what lets the
# refresh tell "this game predates the new field" from "Steam has nothing to
# say about it". Field emptiness cannot answer that: of 30 games looked up
# fresh, 29 had no gamepad recommendation and 5 no controller support — all of
# them correct answers, none of them a gap to go back for.
#
# 1: genre, controller_support, gamepad_recommended
# 2: description
STEAM_METADATA_VERSION = 2

# appdetails category ids for gamepad support. Steam exposes no "no support"
# value: a game simply carries neither.
CONTROLLER_FULL_CATEGORY = 28
CONTROLLER_PARTIAL_CATEGORY = 18
# "Gamepad Recommended". Steam has no id for the opposite claim, so like the
# two above, absence means "unstated" rather than "no".
GAMEPAD_RECOMMENDED_CATEGORY = 60

# How many user tags to ask for. Only the first one that names a genre is
# used, but the genre tag can sit well down the list on games whose top tags
# are all setting and mood — Spider-Man reaches "Ação / Aventura" at 8.
TAG_COUNT = 20

# Appids per bulk tag request. The endpoint answers 200 of them in one call in
# well under a second. This is what makes refreshing a whole library
# affordable: one token for the tags of 200 games instead of 200 tokens.
#
# 200 is also close to the ceiling, which is why it is not higher: the appids
# travel in the query string, and a full batch measures 6647 bytes of URL —
# inside the 8 kB most servers accept, but with only about a fifth to spare.
# Raising this number is not free; it needs measuring again.
TAG_BATCH_SIZE = 200


class SteamError(Exception):
    pass


class SteamGameNotFoundError(SteamError):
    pass


class SteamNotAGameError(SteamError):
    pass


class SteamInvalidManifestError(SteamError):
    pass


class SteamManifestData(TypedDict):
    """Dict returned by SteamFileHelper.get_manifest_data"""

    name: str
    appid: str
    stateflags: str


class SteamAPIData(TypedDict, total=False):
    """Dict returned by SteamAPIHelper.get_api_data"""

    name: str
    developer: str
    publisher: str
    release_date: str
    metacritic: int
    steam_review: str
    genre: str
    # "full" or "partial"; absent when Steam claims neither.
    controller_support: str
    gamepad_recommended: bool
    description: str


class SteamRateLimiter(RateLimiter):
    """Rate limiter for the Steam web API"""

    # Steam web API limit
    # 200 requests per 5 min seems to be the limit
    # https://stackoverflow.com/questions/76047820/how-am-i-exceeding-steam-apis-rate-limit
    # https://stackoverflow.com/questions/51795457/avoiding-error-429-too-many-requests-steam-web-api
    refill_period_seconds = 5 * 60
    refill_period_tokens = 200
    burst_tokens = 100

    def _init_pick_history(self) -> None:
        """
        Load the pick history from schema.

        Allows remembering API limits through restarts of Jolven: the
        base class drains the bucket by what is restored here
        (`seed_history`), so a relaunch right after a large import starts
        with the tokens those requests already spent, instead of a full
        burst on top of them.
        """
        super()._init_pick_history()
        timestamps_str = shared.state_schema.get_string("steam-limiter-tokens-history")
        # A corrupted state entry (bad JSON / wrong type) must never crash
        # the importer at startup; just start with a fresh history instead.
        try:
            timestamps = json.loads(timestamps_str)
        except ValueError:
            timestamps = []
        if isinstance(timestamps, list):
            restored = [t for t in timestamps if isinstance(t, (int, float))]
            # Guarded, because `add()` with no arguments logs a pick at the
            # current time: a first launch (or a history that has just aged
            # out) would otherwise invent a request nobody made — and now
            # that a restored pick costs a token, it would also cost a real
            # one.
            if restored:
                self.pick_history.add(*restored)
        self.pick_history.remove_old_entries()

    def acquire(self) -> None:
        """Get a token from the bucket and store the pick history in the schema"""
        super().acquire()
        timestamps_str = json.dumps(self.pick_history.copy_timestamps())
        shared.state_schema.set_string("steam-limiter-tokens-history", timestamps_str)


class SteamFileHelper:
    """Helper for Steam file formats"""

    def get_manifest_data(self, manifest_path: Path) -> SteamManifestData:
        """Get local data for a game from its manifest"""

        with open(manifest_path, "r", encoding="utf-8") as file:
            contents = file.read()

        data = {}

        for key in SteamManifestData.__required_keys__:  # pylint: disable=no-member
            regex = f'"{key}"\\s+"(.*)"\n'
            if (match := re.search(regex, contents, re.IGNORECASE)) is None:
                raise SteamInvalidManifestError()
            data[key] = match.group(1)

        return SteamManifestData(
            name=data["name"],
            appid=data["appid"],
            stateflags=data["stateflags"],
        )


# Month names mapped to their number. Kept explicit so parsing does not
# depend on the process locale: datetime.strptime("%b") would expect
# localized names and silently fail. Portuguese only: appdetails is asked
# for it (`l=brazilian`), and it is what the app stores and what a date
# typed by hand is written in.
_MONTH_NUMBERS = {
    "jan": 1, "janeiro": 1,
    "fev": 2, "fevereiro": 2,
    "mar": 3, "marco": 3, "março": 3,
    "abr": 4, "abril": 4,
    "mai": 5, "maio": 5,
    "jun": 6, "junho": 6,
    "jul": 7, "julho": 7,
    "ago": 8, "agosto": 8,
    "set": 9, "setembro": 9,
    "out": 10, "outubro": 10,
    "nov": 11, "novembro": 11,
    "dez": 12, "dezembro": 12,
}  # fmt: skip
# Output uses Brazilian Portuguese abbreviations
_MONTH_ABBR = (
    "", "jan.", "fev.", "mar.", "abr.", "mai.", "jun.",
    "jul.", "ago.", "set.", "out.", "nov.", "dez.",
)  # fmt: skip


# Textos da Steam para jogo sem data, na grafia que o app mostra.
_UNDATED_TEXTS = {"em breve": "Em breve", "a ser anunciado": "A ser anunciado"}


def parse_release_date(raw: Optional[str]) -> tuple[Optional[int], Optional[int], Optional[int]]:
    """Lê uma data de lançamento como ``(ano, mês, dia)``; mês e dia podem faltar.

    Formatos aceitos, e só eles: "17/set./2020" (o da Steam), "02/09/2020"
    (digitado à mão), "set. 2020" ou "set./2020" (mês e ano) e "2020". Os
    meses vêm de uma tabela própria, e não do locale do processo, que é o
    que `datetime.strptime("%b")` usaria.

    O dia e o mês só passam se formarem uma data que existe: "31/fev./2026"
    e "99/inf./99" dão ``(None, None, None)``, como "Em breve".
    """
    text = (raw or "").strip().lower()
    day = month = None
    if m := re.fullmatch(r"(\d{1,2})/([a-zç]+)\.?/(\d{4})", text):
        day, month, year = int(m[1]), _MONTH_NUMBERS.get(m[2]), int(m[3])
    elif m := re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text):
        day, month, year = int(m[1]), int(m[2]), int(m[3])
    elif m := re.fullmatch(r"([a-zç]+)\.?[ /](?:de )?(\d{4})", text):
        month, year = _MONTH_NUMBERS.get(m[1]), int(m[2])
    elif m := re.fullmatch(r"\d{4}", text):
        year = int(m[0])
    else:
        return None, None, None
    try:
        date(year, 1 if month is None else month, 1 if day is None else day)
    except (TypeError, ValueError):
        return None, None, None
    return year, month, day


def format_release_date(raw: Optional[str]) -> Optional[str]:
    """A data no formato da Steam ("2/set./2026", "set./2026", "2026").

    "Em breve" e "A ser anunciado" passam na grafia do app. Qualquer outra
    coisa é descartada (``None``, com registro no log de depuração): o texto
    mostrado é sempre montado aqui, nunca o que veio de fora.
    """
    text = (raw or "").strip()
    if not text:
        return None
    if undated := _UNDATED_TEXTS.get(text.lower()):
        return undated
    year, month, day = parse_release_date(text)
    if year is None:
        logging.debug("Data de lançamento descartada: %r", text)
        return None
    if month is None:
        return str(year)
    if day is None:
        return f"{_MONTH_ABBR[month]}/{year}"
    return f"{day}/{_MONTH_ABBR[month]}/{year}"


class SteamAPIHelper:
    """Helper around the Steam API"""

    base_url = "https://store.steampowered.com/api"
    search_url = "https://store.steampowered.com/api/storesearch/"
    reviews_url = "https://store.steampowered.com/appreviews"
    store_items_url = "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/"
    rate_limiter: RateLimiter

    def __init__(self, rate_limiter: RateLimiter) -> None:
        self.rate_limiter = rate_limiter

    def search_store(self, name: str) -> list[dict]:
        """Return the raw store search results for ``name``.

        :raises SteamGameNotFoundError: if the endpoint returns nothing usable
        """
        with self.rate_limiter:
            try:
                with get_capped(
                    self.search_url,
                    params={"term": name, "l": "english", "cc": "us"},
                    timeout=10,
                ) as response:
                    response.raise_for_status()
                    payload = response.json()
                    # JSON válido que não é objeto (null, lista — proxy no
                    # meio) fazia `.get()` estourar AttributeError, que nenhum
                    # except daqui ou do steam_picker cobre: a thread do picker
                    # morria e o spinner ficava eterno. Shape errado vale o
                    # mesmo "não encontrado" do JSON inválido logo abaixo.
                    items = payload.get("items") if isinstance(payload, dict) else None
                    if not isinstance(items, list):
                        items = []
                    # Só entradas com appid numérico: `"id": null` (ou sem
                    # "id") passava daqui e estourava TypeError/KeyError
                    # adiante — no `resolve`, nos seletores — fora de todo
                    # except, matando a thread do seletor com o spinner
                    # girando.
                    items = [
                        item
                        for item in items
                        if isinstance(item, dict) and isinstance(item.get("id"), int)
                    ]
            except HTTPError as error:
                logging.warning("Steam search HTTP error for %s", name, exc_info=error)
                raise error
            except ValueError as error:
                # 200 with an invalid JSON body (proxy, captive portal, …):
                # treat it as "not found" so the game is left untouched
                logging.warning("Steam search invalid response for %s", name)
                raise SteamGameNotFoundError() from error

        if not items:
            raise SteamGameNotFoundError()
        return items

    def search_candidates(self, name: str) -> list[tuple[dict, TitleMatch]]:
        """Return the plausible store entries for ``name``, best match first.

        Entries that cannot be the same game — a sequel, a soundtrack, an
        unrelated title sharing a word — are dropped, so what comes back is
        either the game or something worth showing the user as a suggestion.

        :raises SteamGameNotFoundError: if nothing plausible is found
        """
        ranked = rank_candidates(name, self.search_store(name))
        if not ranked:
            logging.debug("No plausible Steam candidate for %s", name)
            raise SteamGameNotFoundError()
        return ranked

    def find_candidates(self, name: str) -> list[tuple[dict, TitleMatch]]:
        """Return everything that could be ``name``, best match first.

        Only the storefront search. The fallback through the full app list
        (``ISteamApps/GetAppList``) was removed: Steam retired that endpoint
        (404), and its replacement requires a Web API key the app does not
        have.

        :raises SteamGameNotFoundError: if nothing plausible is found
        """
        return self.search_candidates(name)

    def resolve(self, name: str, max_attempts: int = 4) -> tuple[str, SteamAPIData]:
        """Find the appid for ``name`` and return it with its metadata.

        Confident candidates are tried in order until one turns out to be an
        actual game: dedicated servers, test builds and trailers can share
        their game's name and are only distinguishable once appdetails answers.

        :return: an ``(appid, data)`` tuple
        :raises SteamGameNotFoundError: if nothing confident resolves to a game
        """
        attempts = 0
        for candidate, match in self.find_candidates(name):
            if not match.confident or attempts >= max_attempts:
                break
            attempts += 1
            appid = str(candidate["id"])
            logging.debug(
                "Trying %s for %s (%s, score %d)",
                appid,
                name,
                match.reason,
                match.score,
            )
            try:
                return appid, self.get_api_data(appid)
            except (SteamNotAGameError, SteamGameNotFoundError):
                continue
        raise SteamGameNotFoundError()

    def get_api_data(
        self,
        appid: str,
        tag_ids: Optional[list[int]] = None,
        skip_genre: bool = False,
    ) -> SteamAPIData:
        """
        Get online data for a game from its appid.
        May block to satisfy the Steam web API limitations.

        See https://wiki.teamfortress.com/wiki/User:RJackson/StorefrontAPI#appdetails
        """

        # Get data from the API (way block to satisfy its limits)
        with self.rate_limiter:
            try:
                with get_capped(
                    # Portuguese, for the one field of this payload that is
                    # prose: the description. Everything else we read survives
                    # the switch untouched — `type` is not translated, category
                    # and genre ids are the same, and the date only changes
                    # shape ("5 Dec, 2019" to "5/dez./2019"), which the parser
                    # already reads because it has to accept the Portuguese
                    # form the app itself stores.
                    f"{self.base_url}/appdetails?appids={appid}&l=brazilian",
                    timeout=10,
                ) as response:
                    response.raise_for_status()
                    payload = response.json()
            except HTTPError as error:
                logging.warning("Steam API HTTP error for %s", appid, exc_info=error)
                raise error
            except ValueError as error:
                # 200 with an invalid JSON body: same handling as "not found"
                logging.warning("Steam API invalid response for %s", appid)
                raise SteamGameNotFoundError() from error

        # Um pedido leva um appID só, então a resposta tem uma entrada só, e é
        # a dele. A chave não serve: desde 24/09/2026 a Steam devolve a entrada
        # sob o ID de um DLC do jogo (o 391220 veio como "541750"), com o
        # appID certo dentro de `data`. Qualquer outro formato conta como "não
        # encontrado": o jogo fica como está, em vez de a importação cair.
        entry = (
            next(iter(payload.values()))
            if isinstance(payload, dict) and len(payload) == 1
            else None
        )
        if not isinstance(entry, dict) or not entry.get("success"):
            logging.debug("Appid %s not found", appid)
            raise SteamGameNotFoundError()

        app_data = entry.get("data")
        if not isinstance(app_data, dict):
            logging.debug("Appid %s has no data", appid)
            raise SteamGameNotFoundError()

        # Handle appid is not a game
        if app_data.get("type") not in {"game", "demo", "mod"}:
            logging.debug("Appid %s is not a game", appid)
            raise SteamNotAGameError()

        values = self.parse_app_data(app_data)

        # `skip_genre` leaves the key out entirely rather than computing one,
        # so `update_values` never touches the field. Falling back to the
        # coarse store genre here would be worse than saying nothing: it would
        # replace a hard-won "Metroidvania" with "Ação" whenever the tag
        # request had simply failed.
        if not skip_genre and (genre := self.get_genre(appid, app_data, tag_ids)):
            values["genre"] = genre

        # Both scores are shown side by side in the UI, so the review summary
        # is always fetched, regardless of whether there's a Metacritic score.
        review = self.get_review_summary(appid)
        if review:
            values["steam_review"] = review
        return values

    @staticmethod
    def parse_app_data(app_data: dict) -> SteamAPIData:
        """Pull the fields we keep out of an appdetails payload.

        Every field is optional: the name is only included when non-empty so
        an API quirk can never blank out the game's title, and the rest are
        simply left out when absent so `update_values` does not overwrite
        what the record already holds.
        """
        values = SteamAPIData()
        if name := app_data.get("name"):
            values["name"] = name
        # `short_description`, not `about_the_game`. The long one is HTML with
        # images and autoplay video, and for some games it is *only* that:
        # Cyberpunk 2077's runs 3665 characters and contains no text at all.
        # This one is a plain sentence or two, always present, and translated.
        if description := app_data.get("short_description"):
            values["description"] = description.strip()
        if developers := app_data.get("developers"):
            values["developer"] = ", ".join(developers)
        if publishers := app_data.get("publishers"):
            values["publisher"] = ", ".join(publishers)
        release_info = app_data.get("release_date")
        if isinstance(release_info, dict) and (
            release_date := format_release_date(release_info.get("date"))
        ):
            values["release_date"] = release_date
        metacritic_info = app_data.get("metacritic")
        if isinstance(metacritic_info, dict) and isinstance(
            (score := metacritic_info.get("score")), int
        ):
            values["metacritic"] = score
        if controller := SteamAPIHelper.parse_controller_support(app_data):
            values["controller_support"] = controller
        # Always written, unlike the fields above. Those are left out when
        # absent because absent means "the API did not say", which must not
        # overwrite what the record holds. Here the payload is the whole
        # answer — the category is in the list or it is not — so a game that
        # loses the recommendation should lose the line with it.
        values["gamepad_recommended"] = SteamAPIHelper.parse_gamepad_recommended(app_data)
        return values

    def get_genre(
        self, appid: str, app_data: dict, tag_ids: Optional[list[int]] = None
    ) -> Optional[str]:
        """Return the single genre to show for a game, or None.

        ``tag_ids`` lets a caller that already has the user tags — the library
        refresh, which fetches them for every game at once — supply them
        instead of paying a request per game. Left out, they are fetched here:
        the appdetails payload in ``app_data`` only carries the coarse store
        genres, which are the fallback rather than the answer. See
        :mod:`cartridges.utils.steam_genre`.
        """
        genres = app_data.get("genres")
        genre_ids = (
            [str(entry.get("id")) for entry in genres if isinstance(entry, dict)]
            if isinstance(genres, list)
            else []
        )
        if tag_ids is None:
            tag_ids = self.get_store_tags(appid)
        return pick_genre(genre_ids, tag_ids)

    @staticmethod
    def category_ids(app_data: dict) -> set:
        """The appdetails category ids, or an empty set for any odd shape."""
        categories = app_data.get("categories")
        if not isinstance(categories, list):
            return set()
        return {entry.get("id") for entry in categories if isinstance(entry, dict)}

    @staticmethod
    def parse_controller_support(app_data: dict) -> Optional[str]:
        """Return "full", "partial" or None from an appdetails payload.

        Matched on the category id rather than its description: thirteen
        categories have "Controller", "Gamepad" or "Input" in the name
        (DualShock, DualSense, Tracked Controller, Steam Input API, the four
        Remote Play ones), and none of them answers whether the game is
        playable with a gamepad.
        """
        ids = SteamAPIHelper.category_ids(app_data)
        if CONTROLLER_FULL_CATEGORY in ids:
            return "full"
        if CONTROLLER_PARTIAL_CATEGORY in ids:
            return "partial"
        # Neither category present. Reported as "unknown" rather than "none":
        # plenty of older games take a gamepad without ever being labelled for
        # it, so the UI omits the line instead of claiming there is no support.
        return None

    @staticmethod
    def parse_gamepad_recommended(app_data: dict) -> bool:
        """Whether the developers recommend playing with a gamepad.

        A separate claim from `parse_controller_support`, and a rarer one:
        full support says the game *works* with a gamepad, this says it is
        the way it is meant to be played. Only 2 of 52 games surveyed
        carried it.
        """
        return GAMEPAD_RECOMMENDED_CATEGORY in SteamAPIHelper.category_ids(app_data)

    def get_store_tags(self, appid: str) -> list[int]:
        """Return one game's user tag ids, most-voted first.

        These are the store page tags ("Metroidvania", "Soulslike"), which the
        appdetails endpoint does not carry — only ids come back here, and
        :mod:`cartridges.utils.steam_genre` is what turns one into a name.

        Returns an empty list on any failure: the genre falls back to the
        coarse `genres` field, which is already in hand, so a hiccup here is
        never worth failing (or retrying) the whole lookup over.
        """
        return self.get_store_tags_bulk([appid]).get(appid, [])

    def get_store_tags_bulk(
        self, appids: list[str], should_stop: Optional[Callable[[], bool]] = None
    ) -> dict[str, list[int]]:
        """Return user tag ids for many games at once, keyed by appid.

        One request covers :data:`TAG_BATCH_SIZE` games, which is what keeps a
        whole-library refresh from spending a rate limiter token per game.
        Appids that fail, are unknown or carry no tags are simply absent from
        the result rather than mapped to an empty list, so a caller can tell
        "no tags came back" from "this game has none" — the first must not
        overwrite a genre that is already on record.

        ``should_stop`` is consulted between batches. A large library takes
        several of them, each able to sit on a 30 s timeout, so without this
        a user who cancels in the first second still waits out every request
        that was going to be made.
        """
        # The endpoint wants numbers, and `steam_appid` is a string that can
        # have been hand-edited in the game's record.
        numeric: dict[int, str] = {}
        for appid in appids:
            try:
                numeric[int(appid)] = appid
            except (TypeError, ValueError):
                continue
        if not numeric:
            return {}

        wanted = list(numeric)
        result: dict[str, list[int]] = {}
        for start in range(0, len(wanted), TAG_BATCH_SIZE):
            if should_stop is not None and should_stop():
                break
            batch = wanted[start : start + TAG_BATCH_SIZE]
            payload = {
                "ids": [{"appid": appid} for appid in batch],
                "context": {"language": "english", "country_code": "US"},
                "data_request": {"include_tag_count": TAG_COUNT},
            }
            with self.rate_limiter:
                try:
                    with get_capped(
                        self.store_items_url,
                        params={"input_json": json.dumps(payload)},
                        timeout=30,
                    ) as response:
                        response.raise_for_status()
                        items = response.json()["response"].get("store_items", [])
                # AttributeError included on purpose: a proxy or captive portal
                # answering 200 with `{"response": null}` reaches the `.get`
                # above on a non-dict, and that one escaping used to kill the
                # refresh's prefetch thread — which, being the only thing that
                # schedules the queue, left the whole run wedged as "running"
                # for the rest of the process.
                except (
                    RequestException,
                    ValueError,
                    KeyError,
                    TypeError,
                    AttributeError,
                ) as error:
                    logging.debug("Steam tags error for %d apps", len(batch), exc_info=error)
                    continue

            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                key = numeric.get(item.get("appid"))
                tags = item.get("tags")
                if key is None or not isinstance(tags, list):
                    continue
                result[key] = [
                    tag["tagid"]
                    for tag in tags
                    if isinstance(tag, dict) and isinstance(tag.get("tagid"), int)
                ]
        return result

    def get_review_summary(self, appid: str) -> Optional[str]:
        """Return the localized Steam review summary (e.g. "Muito positivas").

        Uses the public appreviews endpoint, which is separate from
        appdetails. Returns None when the game has no reviews or on any
        error, so a failure here never blocks the rest of the import.
        """
        with self.rate_limiter:
            try:
                with get_capped(
                    f"{self.reviews_url}/{appid}",
                    params={
                        "json": 1,
                        "language": "all",
                        "purchase_type": "all",
                        "num_per_page": 0,
                        "l": "brazilian",
                    },
                    timeout=10,
                ) as response:
                    response.raise_for_status()
                    payload = response.json()
                    # isinstance antes do .get(): corpo não-objeto estourava
                    # AttributeError depois do appdetails já ter respondido —
                    # a mesma perda que o comentário abaixo descreve, pela
                    # porta do shape em vez da do transporte.
                    summary = (
                        payload.get("query_summary")
                        if isinstance(payload, dict)
                        else None
                    )
            except (RequestException, ValueError) as error:
                # Every transport failure, not just HTTPError: a timeout or a
                # dropped connection here used to escape *after* appdetails had
                # already answered, throwing away the name, developer and
                # Metacritic score it returned and making the manager retry the
                # whole appdetails request three times over. A review summary
                # is the least important field on the page; it is never worth
                # that.
                logging.debug("Steam reviews error for %s", appid, exc_info=error)
                return None

        if not isinstance(summary, dict) or not summary.get("total_reviews"):
            return None
        desc = summary.get("review_score_desc")
        return desc if isinstance(desc, str) and desc else None
