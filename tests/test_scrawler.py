import unittest

from bot_vnext import scrawler


class ScrawlerUnitTests(unittest.TestCase):
    def test_canon_normalizes_protocol_and_query_fragment(self):
        self.assertEqual(
            scrawler._canon("https://Example.COM/path/file.m3u8?token=1#fragment"),
            "https://example.com/path/file.m3u8?token=1",
        )

    def test_media_detection_handles_common_media_formats(self):
        for url in (
            "https://cdn.example/video.mp4",
            "https://cdn.example/master.m3u8?token=abc",
            "https://cdn.example/manifest.mpd",
            "https://cdn.example/playlist",
        ):
            with self.subTest(url=url):
                self.assertTrue(scrawler._media(url))

    def test_player_detection_is_generic(self):
        for url in (
            "https://player.example/embed/abc",
            "https://video.example/player/abc",
            "https://media.example/watch/123",
            "https://media.example/play/123",
        ):
            with self.subTest(url=url):
                self.assertTrue(scrawler._player_like(url))

    def test_external_player_url_is_recognized_as_player_like(self):
        # Regression fixture for external player hosts such as luluvdo.com.
        # The crawler must use URL structure/semantics, not a hardcoded domain list.
        self.assertTrue(scrawler._player_like("https://luluvdo.com/d/pmmss3e3tov3"))

    def test_episode_and_resolution_extraction(self):
        self.assertEqual(scrawler._episode("My Series S02E07 1080p"), "Episode S02E07")
        self.assertEqual(scrawler._episode("My Series Episode 12"), "Episode 12")
        self.assertEqual(scrawler._resolution("My Video 1920x1080"), "1080p")
        self.assertEqual(scrawler._resolution("My Video 720p"), "720p")

    def test_format_keeps_series_episode_resolution_and_url_together(self):
        rows = [
            (
                "Lollipop 4",
                "Episode 04",
                "1080p",
                "https://frdl.example/a.mkv",
                "FRDL",
                "https://site.example/episode-4",
            )
        ]
        output = "\n".join(scrawler._format(rows, {
            "pages": 1,
            "failed": 0,
            "series": 1,
            "episodes": 1,
            "links": 1,
            "duplicates": 0,
        }))
        self.assertIn("Web Series: Lollipop 4", output)
        self.assertIn("Episode 04", output)
        self.assertIn("1080p — https://frdl.example/a.mkv", output)


if __name__ == "__main__":
    unittest.main()
