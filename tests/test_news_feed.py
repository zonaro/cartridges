import unittest
from unittest.mock import patch

import requests

from cartridges.utils import news_feed
from cartridges.utils.news_feed import (
    FREETOKEEP_FEED_URL,
    GAMERPOWER_FEED_URL,
    NEWS_FEEDS,
    TRINITYWEB_FEED_URL,
    NewsPost,
    feeds_for_language,
    fetch_all_news,
    fetch_news,
    is_brazilian_portuguese,
    parse_news,
)


def _item(
    title="Some Game (Steam) Giveaway",
    guid="https://example.com/some-game",
    link="https://example.com/some-game",
    pubdate="Thu, 08 Oct 2026 17:49:15 +0000",
    description="Free game, grab it!",
    category=None,
):
    category_el = f"<category>{category}</category>" if category else ""
    return (
        "<item>"
        f"<title>{title}</title>"
        f"<guid>{guid}</guid>"
        f"<link>{link}</link>"
        f"<pubDate>{pubdate}</pubDate>"
        f"<description>{description}</description>"
        f"{category_el}"
        "</item>"
    )


def _doc(*items):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<rss version=\"2.0\"><channel>" + "".join(items) + "</channel></rss>"
    )


class NewsFeedTests(unittest.TestCase):
    def test_feeds_use_https_aggregates(self):
        urls = [url for _source, url in NEWS_FEEDS]
        self.assertIn(GAMERPOWER_FEED_URL, urls)
        self.assertIn(FREETOKEEP_FEED_URL, urls)
        for url in urls:
            self.assertTrue(url.startswith("https://"))
        self.assertEqual(urls.count(GAMERPOWER_FEED_URL), 1)
        self.assertEqual(urls.count(FREETOKEEP_FEED_URL), 1)
        self.assertTrue(TRINITYWEB_FEED_URL.startswith("https://"))

    def test_is_brazilian_portuguese_accepts_locale_spellings(self):
        for value in ("pt_BR", "pt-BR", "pt_BR.UTF-8", "pt_BR:pt:en"):
            self.assertTrue(is_brazilian_portuguese(value), value)
        for value in ("", "en_US", "pt_PT", "auto", "es_ES.UTF-8"):
            self.assertFalse(is_brazilian_portuguese(value), value)

    def test_feeds_for_language_gates_pt_br_source(self):
        english = [url for _source, url in feeds_for_language("en_US")]
        self.assertEqual(english, [url for _source, url in NEWS_FEEDS])
        self.assertNotIn(TRINITYWEB_FEED_URL, english)

        portuguese = [url for _source, url in feeds_for_language("pt_BR")]
        self.assertIn(TRINITYWEB_FEED_URL, portuguese)
        self.assertEqual(len(portuguese), len(NEWS_FEEDS) + 1)

    def test_fetch_all_news_polls_pt_br_source_only_in_pt_br(self):
        with patch.object(news_feed, "fetch_news", return_value=[]) as fetch:
            fetch_all_news(language="en_US")
            english_urls = [call.args[0] for call in fetch.call_args_list]
            fetch.reset_mock()
            fetch_all_news(language="pt_BR")
            portuguese_urls = [call.args[0] for call in fetch.call_args_list]
        self.assertNotIn(TRINITYWEB_FEED_URL, english_urls)
        self.assertIn(TRINITYWEB_FEED_URL, portuguese_urls)

    def test_parse_tags_source(self):
        posts = parse_news(_doc(_item()), source="GamerPower")
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].source, "GamerPower")
        self.assertEqual(posts[0].title, "Some Game (Steam) Giveaway")

    def test_parse_supports_two_digit_year_pubdate(self):
        posts = parse_news(
            _doc(_item(pubdate="Thu, 08 Oct 26 17:49:15 +0000")),
            source="GamerPower",
        )
        self.assertEqual(len(posts), 1)
        self.assertGreater(posts[0].timestamp, 0)

    def test_parse_keeps_first_category(self):
        posts = parse_news(
            _doc(_item(category="Steam")), source="FreeToKeep"
        )
        self.assertEqual(posts[0].category, "Steam")

    def test_parse_skips_digest_and_titleless(self):
        doc = _doc(
            _item(title="Updates Digest #99"),
            _item(title=""),
            _item(title="Real Post"),
        )
        posts = parse_news(doc, source="FitGirl")
        self.assertEqual([post.title for post in posts], ["Real Post"])

    def test_parse_dedupes_repeated_guid(self):
        doc = _doc(_item(guid="dup"), _item(guid="dup"))
        posts = parse_news(doc, source="GamerPower")
        self.assertEqual(len(posts), 1)

    def test_parse_reads_enclosure_image(self):
        doc = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<rss version=\"2.0\"><channel><item>"
            "<title>Free Game</title>"
            "<guid>ftk-1</guid>"
            "<link>https://freetokeep.gg/game/free-game</link>"
            "<pubDate>Thu, 08 Oct 2026 18:00:43 GMT</pubDate>"
            "<description>Free to keep.</description>"
            "<enclosure url=\"https://example.com/cover.jpg\" "
            "length=\"0\" type=\"image/jpeg\" />"
            "</item></channel></rss>"
        )
        posts = parse_news(doc, source="FreeToKeep")
        self.assertEqual(posts[0].image, "https://example.com/cover.jpg")

    def test_parse_reads_media_content_image(self):
        doc = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<rss version=\"2.0\" "
            "xmlns:media=\"http://search.yahoo.com/mrss/\"><channel><item>"
            "<title>Giveaway</title>"
            "<guid>gp-1</guid>"
            "<link>https://www.gamerpower.com/giveaway</link>"
            "<pubDate>Thu, 08 Oct 26 17:49:15 +0000</pubDate>"
            "<description>Grab it.</description>"
            "<media:content url=\"https://example.com/offer.jpg\" "
            "type=\"image/jpeg\" />"
            "</item></channel></rss>"
        )
        posts = parse_news(doc, source="GamerPower")
        self.assertEqual(posts[0].image, "https://example.com/offer.jpg")

    def test_parse_rejects_non_image_or_unsafe_thumbnail(self):
        doc = _doc(_item()).replace(
            "</channel>",
            "<item><title>Audio</title><guid>a</guid>"
            "<link>https://example.com/a</link>"
            "<enclosure url=\"https://example.com/a.mp3\" type=\"audio/mpeg\" />"
            "</item><item><title>Evil</title><guid>e</guid>"
            "<link>https://example.com/e</link>"
            "<enclosure url=\"javascript:alert(1)\" type=\"image/png\" />"
            "</item></channel>",
        )
        posts = parse_news(doc, source="FreeToKeep")
        by_title = {post.title: post for post in posts}
        self.assertEqual(by_title["Audio"].image, "")
        self.assertEqual(by_title["Evil"].image, "")

    def test_fetch_news_resolves_source_from_url(self):
        with patch.object(
            news_feed, "fetch_feed_text", return_value=_doc(_item())
        ) as transport:
            posts = fetch_news(GAMERPOWER_FEED_URL)
        transport.assert_called_once()
        self.assertEqual(posts[0].source, "GamerPower")

    def test_fetch_all_news_merges_newest_first(self):
        old = NewsPost(
            identifier="old",
            timestamp=100,
            title="Old",
            url="https://example.com/old",
            summary="",
            category="",
            source="FitGirl",
        )
        new = NewsPost(
            identifier="new",
            timestamp=200,
            title="New",
            url="https://example.com/new",
            summary="",
            category="",
            source="GamerPower",
        )
        with patch.object(
            news_feed, "fetch_news", side_effect=[[old], [new], []]
        ):
            self.assertEqual(len(NEWS_FEEDS), 3)
            merged = fetch_all_news()
        self.assertEqual([post.identifier for post in merged], ["new", "old"])

    def test_fetch_all_news_dedupes_across_feeds_keeping_newest(self):
        stale = NewsPost(
            identifier="same",
            timestamp=100,
            title="Stale",
            url="https://example.com/x",
            summary="",
            category="",
            source="FitGirl",
        )
        fresh = NewsPost(
            identifier="same",
            timestamp=300,
            title="Fresh",
            url="https://example.com/x",
            summary="",
            category="",
            source="FreeToKeep",
        )
        with patch.object(
            news_feed, "fetch_news", side_effect=[[stale], [], [fresh]]
        ):
            merged = fetch_all_news()
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].title, "Fresh")

    def test_fetch_all_news_isolates_per_feed_failure(self):
        good = NewsPost(
            identifier="good",
            timestamp=50,
            title="Good",
            url="https://example.com/good",
            summary="",
            category="",
            source="GamerPower",
        )
        with patch.object(
            news_feed,
            "fetch_news",
            side_effect=[[good], requests.ConnectionError("down"), []],
        ):
            merged = fetch_all_news()
        self.assertEqual([post.identifier for post in merged], ["good"])


if __name__ == "__main__":
    unittest.main()
