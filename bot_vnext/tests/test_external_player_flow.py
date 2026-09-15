import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bot_vnext import scrawler


class ExternalPlayerFlowTest(unittest.TestCase):
    def test_root_page_to_external_d_player_to_hls(self):
        root = "https://source.example/episode-1"
        player = "https://player.example/d/abc123"
        manifest = "https://cdn.example/video/episode-1/master.m3u8"

        pages = {
            root: '<html><head><title>Episode 1</title></head><body><a href="https://player.example/d/abc123">Watch</a></body></html>',
            player: '<html><script>var src = "https://cdn.example/video/episode-1/master.m3u8";</script></html>',
        }

        def fake_fetch(url, *args, **kwargs):
            text = pages.get(url, "")
            return SimpleNamespace(
                url=url,
                text=text,
                headers={"content-type": "text/html; charset=utf-8"},
                status_code=200,
            ) if text else None

        with patch.object(scrawler, "_fetch", side_effect=fake_fetch), \
             patch.object(scrawler, "_sitemap_urls", return_value=[]), \
             patch.object(scrawler, "_browser_discover", return_value=[]):
            rows, stats = scrawler._crawl(root, max_pages=10, workers=2)

        urls = [row[3] for row in rows]
        self.assertIn(manifest, urls)
        self.assertEqual(len(urls), 1)
        self.assertGreaterEqual(stats["pages"], 2)


if __name__ == "__main__":
    unittest.main()
