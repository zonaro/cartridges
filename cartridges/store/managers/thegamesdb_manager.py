# thegamesdb_manager.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Import metadata and fallback artwork from TheGamesDB."""

import logging
from pathlib import Path

import requests
from gi.repository import Gio

from cartridges import shared
from cartridges.errors.friendly_error import FriendlyError
from cartridges.game import Game
from cartridges.store.managers.async_manager import AsyncManager
from cartridges.store.managers.cover_manager import CoverManager
from cartridges.store.managers.sgdb_manager import SgdbManager
from cartridges.store.managers.steam_api_manager import SteamAPIManager
from cartridges.utils.download import download_bytes
from cartridges.utils.save_cover import ANIMATED_SUFFIXES, convert_cover, save_cover
from cartridges.utils.thegamesdb import (
    TheGamesDBAuthError,
    TheGamesDBClient,
    TheGamesDBError,
    TheGamesDBNotFound,
    metadata_from_result,
)


class TheGamesDBManager(AsyncManager):
    """Complete missing details and provide artwork fallbacks."""

    run_after = (SteamAPIManager, CoverManager, SgdbManager)
    retryable_on = (TheGamesDBError, requests.RequestException, ConnectionError)
    continue_on = (TheGamesDBNotFound,)

    def main(self, game: Game, _additional_data: dict) -> None:
        if (
            game.blacklisted
            or game.is_launcher
            or not shared.schema.get_boolean("thegamesdb")
            or not shared.schema.get_string("thegamesdb-key").strip()
        ):
            return

        client = TheGamesDBClient()
        try:
            result = client.find_game(game.name)
        except TheGamesDBAuthError as error:
            self.cancellable.cancel()
            raise FriendlyError(
                _("Não foi possível autenticar no TheGamesDB"),
                _("Verifique a chave da API e a franquia mensal disponível."),
            ) from error
        incoming = metadata_from_result(result)

        # TheGamesDB is a metadata fallback. A value already supplied by the
        # importer, Steam or the user always wins.
        game.update_values(
            {
                key: value
                for key, value in incoming.items()
                if value not in (None, "", [])
                and (key.startswith("tgdb_") or not getattr(game, key, None))
            }
        )

        has_cover = any(
            (shared.covers_dir / f"{game.game_id}{suffix}").is_file()
            for suffix in (*ANIMATED_SUFFIXES, ".tiff")
        )
        if not has_cover and result.cover_url:
            raw_path = Path(Gio.File.new_tmp()[0].get_path())
            converted = None
            try:
                raw_path.write_bytes(download_bytes(result.cover_url, timeout=20))
                converted = convert_cover(raw_path)
                if converted is not None:
                    save_cover(game.game_id, converted)
            except (OSError, requests.RequestException) as error:
                logging.info(
                    "TheGamesDB cover fallback failed for %s: %s", game.name, error
                )
            finally:
                raw_path.unlink(missing_ok=True)
                if converted is not None and converted != raw_path:
                    converted.unlink(missing_ok=True)

        try:
            images = client.game_images(result.game["id"])
        except TheGamesDBAuthError as error:
            self.cancellable.cancel()
            raise FriendlyError(
                _("Não foi possível autenticar no TheGamesDB"),
                _("Verifique a chave da API e a franquia mensal disponível."),
            ) from error
        game.update_values(
            {
                "tgdb_screenshots": images["screenshot"][:8],
                "tgdb_fanart": images["fanart"][:4],
                "tgdb_checked": 1,
            }
        )

        # Auth/protocol errors are intentionally not swallowed: the manager's
        # error collection makes them diagnosable without breaking the import.
