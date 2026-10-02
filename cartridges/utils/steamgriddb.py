# steamgriddb.py
#
# Copyright 2022-2023 kramo
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

import logging
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from gi.repository import Gio
from requests.exceptions import HTTPError, RequestException

from cartridges import shared
from cartridges.game import Game
from cartridges.utils.download import download_bytes, get_capped
from cartridges.utils.name_cleaner import clean_for_search
from cartridges.utils.save_cover import ANIMATED_SUFFIXES, convert_cover, save_cover
from cartridges.utils.title_match import rank_candidates


class SgdbError(Exception):
    pass


class SgdbAuthError(SgdbError):
    pass


class SgdbGameNotFound(SgdbError):
    pass


class SgdbBadRequest(SgdbError):
    pass


class SgdbNoImageFound(SgdbError):
    pass


def auth_error(res: requests.Response) -> SgdbAuthError:
    """Build the error for a 401, whatever the body turned out to be.

    The API answers 401 with a JSON ``errors`` list, but the 401 the user
    actually hits — a bad or expired key — is often served by Cloudflare in
    front of it, as an HTML page. Reading that as JSON raised a
    JSONDecodeError, and JSONDecodeError is retryable: the wrong-key case
    was retried three times and then reported as a generic failure, so the
    one dialog that would have told the user what to fix was the one they
    never saw.
    """
    try:
        message = str(res.json()["errors"][0])
    except (ValueError, LookupError, TypeError):
        message = "SteamGridDB rejected the API key"
    return SgdbAuthError(message)


def _data_list(res: requests.Response) -> list:
    """The ``data`` of a 200 from SGDB, requiring the documented shape.

    A 200 with valid JSON of another shape — ``{"success": false}`` from a
    proxy, for example — raised ``KeyError`` on ``res.json()["data"]``,
    which no picker ``except`` covers: the search thread died and the
    dialog sat on its spinner forever. A wrong shape is now
    ``SgdbBadRequest``, an error treatable like any other.
    """
    try:
        payload = res.json()
    except ValueError as error:
        raise SgdbBadRequest(res.status_code) from error
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise SgdbBadRequest(res.status_code)
    return payload["data"]


class SgdbHelper:
    """Helper class to make queries to SteamGridDB"""

    base_url = "https://www.steamgriddb.com/api/v2/"

    @property
    def auth_headers(self) -> dict[str, str]:
        key = shared.schema.get_string("sgdb-key")
        headers = {"Authorization": f"Bearer {key}"}
        return headers

    def get_game_id(self, game: Game) -> Any:
        """Get the best matching SGDB game id for a game. Can raise an exception."""
        # The query is a path segment, so it must be URL-encoded — otherwise
        # spaces and characters like ':', '&', "'" or accents break the request.
        query = clean_for_search(game.name)
        uri = f"{self.base_url}search/autocomplete/{quote(query, safe='')}"
        res = get_capped(uri, headers=self.auth_headers, timeout=10)
        match res.status_code:
            case 200:
                results = [r for r in _data_list(res) if isinstance(r, dict)]
                if not results:
                    raise SgdbGameNotFound(query)
                with_ids = [r for r in results if r.get("id") is not None]
                if not with_ids:
                    raise SgdbBadRequest(res.status_code)
                # Ranked by the same title matching as the Steam lookup, not
                # by string equality: the query is the *cleaned* name and the
                # results are raw, so a fuzzy first hit used to win. A
                # confident match ranks first; failing one, the best plausible
                # title; a sequel or an unrelated title is never taken.
                ranked = rank_candidates(query, with_ids)
                if not ranked:
                    raise SgdbGameNotFound(query)
                return ranked[0][0]["id"]
            case 401:
                raise auth_error(res)
            case 404:
                raise SgdbGameNotFound(res.status_code)
            case _:
                # raise_for_status() only raises on 4xx/5xx; guard against an
                # unexpected 2xx/3xx silently returning None.
                res.raise_for_status()
                raise SgdbBadRequest(res.status_code)

    def search_games(self, query: str) -> list[dict]:
        """Return the SGDB games matching a query (list of ``{id, name, …}``)."""
        uri = f"{self.base_url}search/autocomplete/{quote(query, safe='')}"
        res = get_capped(uri, headers=self.auth_headers, timeout=10)
        match res.status_code:
            case 200:
                return _data_list(res)
            case 401:
                raise auth_error(res)
            case _:
                res.raise_for_status()
                return []

    def get_grids(
        self, game_id: str, animated: bool = False, dimensions: Optional[str] = None
    ) -> list[dict]:
        """Return every grid (cover) available for a SGDB game id.

        Each item has at least ``url`` (full image) and ``thumb`` (preview).
        ``dimensions`` (e.g. ``"600x900"``) is optional: restricting it drops
        most animated grids, which rarely use the exact cover size.
        """
        params = []
        if dimensions:
            params.append(f"dimensions={dimensions}")
        if animated:
            params.append("types=animated")
        uri = f"{self.base_url}grids/game/{game_id}"
        if params:
            uri += "?" + "&".join(params)
        res = get_capped(uri, headers=self.auth_headers, timeout=10)
        match res.status_code:
            case 200:
                return _data_list(res)
            case 401:
                raise auth_error(res)
            case 404:
                raise SgdbGameNotFound(res.status_code)
            case _:
                res.raise_for_status()
                return []

    def get_logos(self, game_id: str) -> list[dict]:
        """Return the horizontal logos available for a SGDB game id.

        Each item carries at least ``url``, ``style`` (``official``/``white``/
        ``black``/``custom``) and its intrinsic ``width``/``height``; picking
        between them is the caller's job, since what makes a logo usable
        depends on where it is drawn.

        Only static, non-NSFW, non-joke assets are asked for: the details page
        header is a piece of branding, not a place for an animation to loop
        behind the metadata.
        """
        uri = f"{self.base_url}logos/game/{game_id}?types=static&nsfw=false&humor=false"
        res = get_capped(uri, headers=self.auth_headers, timeout=10)
        match res.status_code:
            case 200:
                return _data_list(res)
            case 401:
                raise auth_error(res)
            case 404:
                raise SgdbGameNotFound(res.status_code)
            case _:
                res.raise_for_status()
                return []

    def get_image_uri(self, game_id: str, animated: bool = False) -> Any:
        """Get the image for a SGDB game id"""
        uri = f"{self.base_url}grids/game/{game_id}?dimensions=600x900"
        if animated:
            uri += "&types=animated"
        res = get_capped(uri, headers=self.auth_headers, timeout=10)
        match res.status_code:
            case 200:
                data = _data_list(res)
                if len(data) == 0:
                    raise SgdbNoImageFound()
                first = data[0]
                if not isinstance(first, dict) or not first.get("url"):
                    raise SgdbBadRequest(res.status_code)
                return first["url"]
            case 401:
                raise auth_error(res)
            case 404:
                raise SgdbGameNotFound(res.status_code)
            case _:
                # raise_for_status() only raises on 4xx/5xx; guard against an
                # unexpected 2xx/3xx silently returning None.
                res.raise_for_status()
                raise SgdbBadRequest(res.status_code)

    def conditionaly_update_cover(self, game: Game) -> None:
        """Update the game's cover if appropriate"""

        # Obvious skips
        use_sgdb = shared.schema.get_boolean("sgdb")
        if not use_sgdb or game.blacklisted:
            return

        prefer_sgdb = shared.schema.get_boolean("sgdb-prefer")

        # Do nothing if a cover is already present and not preferring SGDB.
        # Every suffix `save_cover` writes: checking only .tiff/.gif let a
        # hand-picked animated .webp pass for "no cover", and saving the
        # download then deleted it.
        if not prefer_sgdb and any(
            (shared.covers_dir / f"{game.game_id}{suffix}").is_file()
            for suffix in (*ANIMATED_SUFFIXES, ".tiff")
        ):
            return

        # Get ID for the game
        try:
            sgdb_id = self.get_game_id(game)
        except (HTTPError, SgdbError) as error:
            logging.warning(
                "%s while getting SGDB ID for %s", type(error).__name__, game.name
            )
            raise error

        # Build different SGDB options to try
        image_uri_kwargs_sets = [{"animated": False}]
        if shared.schema.get_boolean("sgdb-animated"):
            image_uri_kwargs_sets.insert(0, {"animated": True})

        # Download covers
        for uri_kwargs in image_uri_kwargs_sets:
            try:
                uri = self.get_image_uri(sgdb_id, **uri_kwargs)
                # download_bytes streams with a size cap and raises on HTTP
                # errors, so an error page is never saved as a cover image
                content = download_bytes(uri, timeout=10)
                tmp_file_path = Path(Gio.File.new_tmp()[0].get_path())
                converted = None
                try:
                    tmp_file_path.write_bytes(content)
                    # `convert_cover` takes a Path (it reads `.suffix`); it
                    # used to be handed the raw str and only survived because
                    # the resize branch short-circuits before that attribute.
                    converted = convert_cover(tmp_file_path)
                    # A failed conversion returns None, and `save_cover`
                    # deletes every existing cover for the game *before* it
                    # checks its argument (passing None is how the details
                    # dialog clears a cover). Handing the failure straight
                    # through therefore destroyed the cover the game already
                    # had — a bad download left it worse off than not trying
                    # at all.
                    if converted is None:
                        raise SgdbNoImageFound()
                    save_cover(game.game_id, converted)
                finally:
                    # Remove the raw download and whatever temp the conversion
                    # produced. Nothing downstream owns them: `save_cover`
                    # copies, so both were being left in the temp dir forever.
                    tmp_file_path.unlink(missing_ok=True)
                    if converted is not None and converted != tmp_file_path:
                        converted.unlink(missing_ok=True)
            except SgdbAuthError as error:
                # Let caller handle auth errors
                raise error
            except (HTTPError, SgdbError, RequestException) as error:
                logging.warning(
                    "%s while getting image for %s kwargs=%s",
                    type(error).__name__,
                    game.name,
                    str(uri_kwargs),
                )
                continue
            else:
                # Stop as soon as one is finished
                return

        # No image was added
        logging.warning(
            'No matching image found for game "%s" (SGDB ID %s)',
            game.name,
            sgdb_id,
        )
        raise SgdbNoImageFound()
