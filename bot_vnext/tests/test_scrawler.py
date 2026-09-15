import threading
import unittest
from unittest.mock import patch

from bot_vnext import scrawler


class ScrawlerUnitTests(unittest.TestCase):
    def test_canon_normalizes_protocol_and_query_fragment(self):
        self.assertEqual(scrawler._canon("https://Example.COM/path/file.m3u8?token=1#fragment"), "https://example.com/path/file.m3u8?token=1")

    def test_canon_preserves_literal_and_encoded_plus_in_query(self):
        self.assertEqual(scrawler._canon("https://example.com/video.m3u8?sig=a+b&x=1%2B2"), "https://example.com/video.m3u8?sig=a+b&x=1%2B2")

    def test_media_detection_handles_common_media_formats(self):
        for url in ("https://cdn.example/video.mp4", "https://cdn.example/master.m3u8?token=abc", "https://cdn.example/manifest.mpd", "https://cdn.example/playlist"):
            with self.subTest(url=url): self.assertTrue(scrawler._media(url))

    def test_player_detection_is_generic(self):
        for url in ("https://player.example/embed/abc", "https://video.example/player/abc", "https://media.example/watch/123", "https://media.example/play/123", "https://media.example/d/abc123", "https://media.example/e/abc123"):
            with self.subTest(url=url): self.assertTrue(scrawler._player_like(url))

    def test_external_player_url_is_recognized_as_player_like(self):
        self.assertTrue(scrawler._player_like("https://luluvdo.com/d/pmmss3e3tov3"))

    def test_episode_and_resolution_extraction(self):
        self.assertEqual(scrawler._episode("My Series S02E07 1080p"), "Episode S02E07")
        self.assertEqual(scrawler._episode("My Series Episode 12"), "Episode 12")
        self.assertEqual(scrawler._resolution("My Video 1920x1080"), "1080p")
        self.assertEqual(scrawler._resolution("My Video 720p"), "720p")

    def test_index_page_does_not_assign_first_body_episode_to_page(self):
        html = "<html><head><title>My Series - All Episodes</title></head><body><a href='/episode-1'>Episode 1</a><a href='/episode-2'>Episode 2</a></body></html>"
        class Response:
            status_code = 200
            url = "https://site.example/"
            text = html
            headers = {"content-type": "text/html"}
        with patch.object(scrawler, "_fetch", return_value=Response()):
            media, links, *_ = scrawler._scan_page("https://site.example/", "site.example", [0], threading.Lock())
        self.assertFalse(media)
        self.assertIn("https://site.example/episode-1", links)
        self.assertIn("https://site.example/episode-2", links)

    def test_format_keeps_series_episode_resolution_and_url_together(self):
        rows = [("Lollipop 4", "Episode 04", "1080p", "https://frdl.example/a.mkv", "FRDL", "https://site.example/episode-4")]
        output = "\n".join(scrawler._format(rows, {"pages": 1, "failed": 0, "series": 1, "episodes": 1, "links": 1, "duplicates": 0}))
        self.assertIn("Web Series: Lollipop 4", output)
        self.assertIn("Episode 04", output)
        self.assertIn("1080p — https://frdl.example/a.mkv", output)

    def test_public_scrawl_output_contains_only_urls(self):
        rows = [("Show", "Episode 01", "1080p", "https://cdn.example/a.m3u8", "cdn.example", "https://site.example/e1"), ("Show", "Episode 02", "1080p", "https://cdn.example/b.m3u8", "cdn.example", "https://site.example/e2")]
        with patch.object(scrawler, "_crawl", return_value=(rows, {})):
            result = scrawler.crawl_website_media_urls("https://site.example")
        self.assertEqual(result, ["https://cdn.example/a.m3u8", "https://cdn.example/b.m3u8"])


if __name__ == "__main__":
    unittest.main()
