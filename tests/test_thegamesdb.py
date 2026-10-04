import importlib
import sys
import traceback
import types
import unittest
from unittest.mock import patch

import requests


class _Schema:
    def get_string(self, _key):
        return ""


class TheGamesDBTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_shared = sys.modules.get("cartridges.shared")
        shared = types.ModuleType("cartridges.shared")
        shared.schema = _Schema()
        sys.modules["cartridges.shared"] = shared
        sys.modules.pop("cartridges.utils.thegamesdb", None)
        cls.module = importlib.import_module("cartridges.utils.thegamesdb")

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop("cartridges.utils.thegamesdb", None)
        if cls.original_shared is not None:
            sys.modules["cartridges.shared"] = cls.original_shared
        else:
            sys.modules.pop("cartridges.shared", None)

    def test_find_game_uses_confident_title_and_front_boxart(self):
        payload = {
            "data": {
                "games": [
                    {"id": 2, "game_title": "The Outer Worlds 2", "platform": 1},
                    {"id": 1, "game_title": "The Outer Worlds", "platform": 1},
                ]
            },
            "include": {
                "platform": {"data": {"1": {"name": "PC"}}},
                "boxart": {
                    "base_url": {"original": "https://cdn.example/images/original/"},
                    "data": {
                        "1": [
                            {"type": "boxart", "side": "back", "filename": "back.jpg"},
                            {
                                "type": "boxart",
                                "side": "front",
                                "filename": "front.jpg",
                            },
                        ]
                    },
                },
            },
        }
        client = self.module.TheGamesDBClient("key")
        with patch.object(client, "_get", return_value=payload):
            result = client.find_game("The Outer Worlds")
        self.assertEqual(result.game["id"], 1)
        self.assertEqual(result.platform, "PC")
        self.assertEqual(
            result.cover_url, "https://cdn.example/images/original/front.jpg"
        )

    def test_game_images_groups_supported_artwork(self):
        payload = {
            "data": {
                "base_url": {
                    "original": "https://cdn.example/original/",
                    "medium": "https://cdn.example/medium/",
                },
                "images": {
                    "42": [
                        {"type": "fanart", "filename": "fanart/42.jpg"},
                        {"type": "screenshot", "filename": "screens/42.jpg"},
                        {"type": "boxart", "side": "back", "filename": "back.jpg"},
                    ]
                },
            }
        }
        client = self.module.TheGamesDBClient("key")
        with patch.object(client, "_get", return_value=payload):
            images = client.game_images(42)
        self.assertEqual(
            images["fanart"], ["https://cdn.example/original/fanart/42.jpg"]
        )
        self.assertEqual(
            images["screenshot"], ["https://cdn.example/medium/screens/42.jpg"]
        )
        self.assertEqual(images["boxart"], [])

    def test_request_errors_never_expose_the_api_key(self):
        response = requests.Response()
        response.status_code = 500
        response.url = "https://api.thegamesdb.net/v1/Games/ByGameName?apikey=secret"
        error = requests.HTTPError(response=response)
        client = self.module.TheGamesDBClient("secret")
        with patch.object(self.module, "get_capped", side_effect=error):
            with self.assertRaises(self.module.TheGamesDBError) as raised:
                client.find_game("Example")
        self.assertNotIn("secret", str(raised.exception))
        formatted = "".join(
            traceback.format_exception(
                type(raised.exception),
                raised.exception,
                raised.exception.__traceback__,
            )
        )
        self.assertNotIn("secret", formatted)

    def test_non_object_response_is_reported_as_api_error(self):
        response = requests.Response()
        response.status_code = 200
        response._content = b"[]"  # pylint: disable=protected-access
        client = self.module.TheGamesDBClient("key")
        with patch.object(self.module, "get_capped", return_value=response):
            with self.assertRaises(self.module.TheGamesDBError):
                client.find_game("Example")


if __name__ == "__main__":
    unittest.main()
