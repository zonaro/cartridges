import unittest

from cartridges.xcloud_catalog import CloudGame, parse_product_ids, parse_products


class XCloudCatalogTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
