import hashlib
import unittest
from pathlib import Path

from cartridges.xcloud_catalog import CloudGame, parse_product_ids, parse_products


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


if __name__ == "__main__":
    unittest.main()
