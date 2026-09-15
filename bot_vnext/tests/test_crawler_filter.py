import unittest
from types import SimpleNamespace
from unittest.mock import patch

import crawler


class CrawlerFilterTests(unittest.TestCase):
    def test_generic_external_player_paths_are_supported(self):
        for url in (
            "https://player.example/d/abc123",
            "https://player.example/e/abc123",
            "https://player.example/embed/abc123",
            "https://player.example/player/abc123",
            "https://player.example/watch/abc123",
            "https://player.example/play/abc123",
            "https://player.example/stream/abc123",
        ):
            with self.subTest(url=url):
                self.assertTrue(crawler.is_player_like_url(url))

    def test_episode_crawl_filters_obvious_non_media_urls(self):
        root = "https://source.example/episode-1"
        player = "https://player.example/d/abc123"
        media = "https://cdn.example/video/episode-1-1080p.mp4"
        junk = (
            "https://static.cloudflareinsights.com/beacon.min.js",
            "https://meta-info-author.meta",
            "https://example.com/rb-gallery-popup.mfp-bg.mfp-ready.rb",
            "https://fw-mega-cat.is/assets/style.css",
        )
        html = f"""
        <html>
          <head><title>Episode 1</title></head>
          <body>
            <article class="entry-content">
              <a href="{player}">Watch Episode 1</a>
              <a href="{media}">1080p Download</a>
              <script>
                const analytics = "{junk[0]}";
                const author = "{junk[1]}";
              </script>
              <img src="{junk[2]}" />
              <link href="{junk[3]}" />
            </article>
          </body>
        </html>
        """

        def fake_request(url, timeout=30):
            return SimpleNamespace(
                url=url,
                text=html,
                status_code=200,
            )

        with patch.object(crawler, "_request_page", side_effect=fake_request), \
             patch.object(crawler, "_browser_discover", return_value=([], "")):
            results = crawler.crawl_blog_episodes(root)

        urls = {item["url"] for item in results}
        self.assertIn(player, urls)
        self.assertIn(media, urls)
        for bad in junk:
            self.assertNotIn(bad, urls)


if __name__ == "__main__":
    unittest.main()
