import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from cartridges.xcloud_catalog import (
    CATALOG_CACHE_FILE,
    CATALOG_TTL,
    CACHE_VERSION,
    CloudGame,
    cache_is_fresh,
    get_catalog,
    load_cached_catalog,
    parse_product_ids,
    parse_products,
    save_cached_catalog,
)


class XCloudCatalogTests(unittest.TestCase):
    def test_packaged_logo_is_the_official_better_xcloud_asset(self):
        logo = Path(__file__).parents[1] / "data" / "xbox-cloud.png"
        self.assertEqual(
            hashlib.sha256(logo.read_bytes()).hexdigest(),
            "b3b4c1f479b877f5303ac92a613392f226019b0d66a89673922db6873153bdc9",
        )

    def test_sigl_header_is_ignored_and_ids_are_deduplicated(self):
        self.assertEqual(
            parse_product_ids(
                [
                    {"siglId": "header", "title": "Todos os jogos"},
                    {"id": "abc"},
                    {"id": "ABC"},
                    {"not-an-id": "ignored"},
                ]
            ),
            ["ABC"],
        )

    def test_product_metadata_and_box_art_are_parsed(self):
        games = parse_products(
            {
                "Products": [
                    {
                        "ProductId": "9abc",
                        "LocalizedProperties": [
                            {
                                "ProductTitle": "Example Game",
                                "DeveloperName": "Studio",
                                "PublisherName": "Publisher",
                                "ShortDescription": "Description",
                                "Images": [
                                    {
                                        "ImagePurpose": "BoxArt",
                                        "Uri": "//cdn.example/cover",
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        )
        self.assertEqual(
            games,
            [
                CloudGame(
                    product_id="9ABC",
                    name="Example Game",
                    developer="Studio",
                    publisher="Publisher",
                    description="Description",
                    cover_url="https://cdn.example/cover?w=400",
                )
            ],
        )
        self.assertEqual(games[0].game_id, "xcloud_9abc")
        self.assertEqual(
            games[0].play_url, "https://www.xbox.com/play/launch/9ABC"
        )

    def test_only_public_zero_price_offer_is_free(self):
        base = {
            "ProductId": "FREE1",
            "LocalizedProperties": [{"ProductTitle": "Free Game"}],
            "DisplaySkuAvailabilities": [
                {
                    "Sku": {"SkuType": "full"},
                    "Availabilities": [
                        {
                            "Actions": ["Details", "Purchase"],
                            "RemediationRequired": False,
                            "OrderManagementData": {
                                "Price": {"ListPrice": 0.0}
                            },
                        }
                    ]
                }
            ],
        }
        subscription = {
            **base,
            "ProductId": "PASS1",
            "LocalizedProperties": [{"ProductTitle": "Game Pass Game"}],
            "DisplaySkuAvailabilities": [
                {
                    "Sku": {"SkuType": "full"},
                    "Availabilities": [
                        {
                            "Actions": ["Details", "Purchase"],
                            "RemediationRequired": True,
                            "OrderManagementData": {
                                "Price": {"ListPrice": 0.0}
                            },
                        }
                    ]
                }
            ],
        }
        games = parse_products({"Products": [base, subscription]})
        self.assertTrue(games[0].is_free)
        self.assertFalse(games[1].is_free)


class XCloudCatalogCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache = Path(self._tmp.name) / CATALOG_CACHE_FILE

    @staticmethod
    def sample() -> list[CloudGame]:
        return [
            CloudGame(product_id="A1", name="Alpha", developer="Studio"),
            CloudGame(product_id="B2", name="Beta", is_free=True),
        ]

    def test_round_trip_preserves_games_and_metadata(self) -> None:
        self.assertTrue(
            save_cached_catalog(self.sample(), self.cache, now=1000.0)
        )
        self.assertEqual(load_cached_catalog(self.cache), self.sample())
        payload = json.loads(self.cache.read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], CACHE_VERSION)
        self.assertEqual(payload["fetched_at"], 1000.0)

    def test_load_missing_corrupt_or_future_version_is_empty(self) -> None:
        self.assertEqual(load_cached_catalog(self.cache), [])
        self.cache.write_text("{not json", encoding="utf-8")
        self.assertEqual(load_cached_catalog(self.cache), [])
        self.cache.write_text(
            json.dumps({"version": CACHE_VERSION + 1, "games": []}),
            encoding="utf-8",
        )
        self.assertEqual(load_cached_catalog(self.cache), [])

    def test_malformed_entries_are_skipped(self) -> None:
        self.cache.write_text(
            json.dumps(
                {
                    "version": CACHE_VERSION,
                    "fetched_at": 0.0,
                    "games": [
                        {"product_id": "OK", "name": "Valid"},
                        {"product_id": "", "name": "No ID"},
                        {"name": "No product id"},
                        "junk",
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(
            load_cached_catalog(self.cache),
            [CloudGame(product_id="OK", name="Valid")],
        )

    def test_save_ignores_empty_catalog(self) -> None:
        self.assertFalse(save_cached_catalog([], self.cache))
        self.assertFalse(self.cache.exists())

    def test_cache_is_fresh_respects_ttl(self) -> None:
        save_cached_catalog(self.sample(), self.cache, now=1000.0)
        self.assertTrue(
            cache_is_fresh(self.cache, now=1000.0 + CATALOG_TTL - 1)
        )
        self.assertFalse(
            cache_is_fresh(self.cache, now=1000.0 + CATALOG_TTL + 1)
        )
        missing = Path(self._tmp.name) / "missing.json"
        self.assertFalse(cache_is_fresh(missing, now=1000.0))

    def test_get_catalog_prefers_fresh_cache(self) -> None:
        save_cached_catalog(self.sample(), self.cache, now=10.0)

        def unreachable() -> list[CloudGame]:
            raise AssertionError("network must not run for a fresh cache")

        self.assertEqual(
            get_catalog(self.cache, fetcher=unreachable, now=20.0),
            self.sample(),
        )

    def test_get_catalog_fetches_and_saves_when_stale(self) -> None:
        save_cached_catalog(
            [CloudGame(product_id="OLD", name="Old")], self.cache, now=0.0
        )
        fresh = [CloudGame(product_id="NEW", name="New")]
        calls: list[bool] = []

        def fetcher() -> list[CloudGame]:
            calls.append(True)
            return fresh

        games = get_catalog(self.cache, fetcher=fetcher, now=CATALOG_TTL + 5)
        self.assertEqual(games, fresh)
        self.assertEqual(len(calls), 1)
        self.assertEqual(load_cached_catalog(self.cache), fresh)

    def test_get_catalog_force_bypasses_fresh_cache(self) -> None:
        save_cached_catalog(self.sample(), self.cache, now=10.0)
        fresh = [CloudGame(product_id="NEW", name="New")]
        self.assertEqual(
            get_catalog(
                self.cache, force=True, fetcher=lambda: fresh, now=20.0
            ),
            fresh,
        )

    def test_get_catalog_falls_back_to_stale_cache_on_error(self) -> None:
        save_cached_catalog(self.sample(), self.cache, now=0.0)

        def offline() -> list[CloudGame]:
            raise ValueError("offline")

        self.assertEqual(
            get_catalog(self.cache, fetcher=offline, now=CATALOG_TTL + 5),
            self.sample(),
        )

    def test_get_catalog_raises_without_cache_on_error(self) -> None:
        def offline() -> list[CloudGame]:
            raise ValueError("offline")

        with self.assertRaises(ValueError):
            get_catalog(self.cache, fetcher=offline)


class XCloudCatalogSyncTests(unittest.TestCase):
    def test_sync_async_returns_false_when_already_running(self) -> None:
        from cartridges import xcloud_catalog

        self.addCleanup(setattr, xcloud_catalog, "_sync_running", False)
        xcloud_catalog._sync_running = True
        self.assertFalse(xcloud_catalog.sync_async())


if __name__ == "__main__":
    unittest.main()
