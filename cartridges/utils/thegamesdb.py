# thegamesdb.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Small, defensive client for TheGamesDB v1 API."""

from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urljoin

import requests

from cartridges import shared
from cartridges.utils.download import get_capped
from cartridges.utils.title_match import CONFIDENT_SCORE, rank_candidates


API_BASE = "https://api.thegamesdb.net/v1/"
DETAIL_FIELDS = "players,publishers,genres,overview,rating,platform,coop"
IMAGE_TYPES = "fanart,boxart,screenshot"


class TheGamesDBError(Exception):
    """The service failed or returned an unsupported response."""


class TheGamesDBAuthError(TheGamesDBError):
    """The configured API key was rejected or exhausted."""


class TheGamesDBNotFound(TheGamesDBError):
    """No confident title match exists."""


@dataclass(frozen=True)
class TheGamesDBResult:
    game: dict[str, Any]
    platform: Optional[str]
    cover_url: Optional[str]


def _mapping(payload: Any, *keys: str) -> dict:
    current = payload
    for key in keys:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return current if isinstance(current, dict) else {}


def _image_url(base_urls: dict, filename: Any, size: str = "original") -> Optional[str]:
    if not isinstance(filename, str) or not filename:
        return None
    base = base_urls.get(size) or base_urls.get("original")
    if not isinstance(base, str) or not base:
        return None
    return urljoin(base.rstrip("/") + "/", filename.lstrip("/"))


class TheGamesDBClient:
    """Query metadata and artwork using the key from preferences."""

    def __init__(self, api_key: Optional[str] = None) -> None:
        self.api_key = (
            api_key
            if api_key is not None
            else shared.schema.get_string("thegamesdb-key").strip()
        )

    def _get(self, endpoint: str, **params: Any) -> dict:
        if not self.api_key:
            raise TheGamesDBAuthError("missing API key")
        params["apikey"] = self.api_key
        try:
            response = get_capped(
                urljoin(API_BASE, endpoint), params=params, timeout=15
            )
            if response.status_code == 403:
                raise TheGamesDBAuthError("API key rejected or allowance exhausted")
            response.raise_for_status()
            payload = response.json()
        except TheGamesDBAuthError:
            raise
        except requests.RequestException as error:
            # HTTPError includes response.url, which contains the query-string
            # API key. Keep credentials out of logs and error dialogs.
            status = getattr(getattr(error, "response", None), "status_code", None)
            message = (
                f"request failed with status {status}"
                if status
                else "request failed"
            )
            raise TheGamesDBError(message) from None
        except ValueError as error:
            raise TheGamesDBError("response was not JSON") from error
        if not isinstance(payload, dict):
            raise TheGamesDBError("response is not an object")
        if payload.get("code") == 403:
            raise TheGamesDBAuthError("API key rejected or allowance exhausted")
        if not isinstance(payload.get("data"), dict):
            raise TheGamesDBError("response has no data object")
        return payload

    def find_game(self, name: str) -> TheGamesDBResult:
        from cartridges.utils.name_cleaner import search_variants

        queries = search_variants(name) or [name]
        for query in queries:
            payload = self._get(
                "Games/ByGameName",
                name=query,
                fields=DETAIL_FIELDS,
                include="boxart,platform",
            )
            games = payload["data"].get("games")
            candidates = [
                game
                for game in games or []
                if isinstance(game, dict) and game.get("id") is not None
            ]
            ranked = rank_candidates(query, candidates, name_key="game_title")
            if ranked and ranked[0][1].score >= CONFIDENT_SCORE:
                game = ranked[0][0]
                break
        else:
            raise TheGamesDBNotFound(name)

        include = (
            payload.get("include") if isinstance(payload.get("include"), dict) else {}
        )
        platforms = _mapping(include, "platform", "data") or _mapping(
            include, "platform"
        )
        platform = platforms.get(str(game.get("platform"))) or platforms.get(
            game.get("platform")
        )
        platform_name = platform.get("name") if isinstance(platform, dict) else None

        boxart = _mapping(include, "boxart")
        base_urls = (
            boxart.get("base_url")
            if isinstance(boxart.get("base_url"), dict)
            else {}
        )
        images = boxart.get("data") if isinstance(boxart.get("data"), dict) else {}
        entries = images.get(str(game.get("id"))) or images.get(game.get("id")) or []
        front = next(
            (
                image
                for image in entries
                if isinstance(image, dict)
                and image.get("type") == "boxart"
                and image.get("side") == "front"
            ),
            None,
        )
        cover_url = _image_url(base_urls, front.get("filename") if front else None)
        return TheGamesDBResult(game, platform_name, cover_url)

    def game_images(self, game_id: int | str) -> dict[str, list[str]]:
        payload = self._get(
            "Games/Images", games_id=str(game_id), **{"filter[type]": IMAGE_TYPES}
        )
        data = payload["data"]
        base_urls = (
            data.get("base_url") if isinstance(data.get("base_url"), dict) else {}
        )
        images = data.get("images") if isinstance(data.get("images"), dict) else {}
        entries = images.get(str(game_id)) or images.get(game_id) or []
        result = {"fanart": [], "screenshot": [], "boxart": []}
        for image in entries:
            if not isinstance(image, dict) or image.get("type") not in result:
                continue
            if image.get("type") == "boxart" and image.get("side") != "front":
                continue
            url = _image_url(
                base_urls,
                image.get("filename"),
                size="medium" if image.get("type") == "screenshot" else "original",
            )
            if url:
                result[image["type"]].append(url)
        return result


def metadata_from_result(result: TheGamesDBResult) -> dict[str, Any]:
    """Convert an API result to fields stored by :class:`Game`."""
    game = result.game
    return {
        "tgdb_id": game.get("id"),
        "tgdb_platform": result.platform,
        "tgdb_players": game.get("players"),
        "tgdb_age_rating": game.get("rating"),
        "tgdb_coop": game.get("coop"),
        "release_date": game.get("release_date"),
        "description": game.get("overview"),
    }
