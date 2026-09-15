import unittest

import crawler


class CrawlerDedupeTests(unittest.TestCase):
    def test_exact_url_duplicates_merge_metadata(self):
        rows = [
            {
                "title": "Unknown Episode",
                "episode": "Unknown Episode",
                "url": "https://example.com/video.m3u8#frag",
                "source": "example.com",
                "resolution": "Unknown",
                "source_url": "https://site.test/post",
            },
            {
                "title": "Show - Episode 2 - 1080p",
                "episode": "Episode 2",
                "url": "https://example.com/video.m3u8",
                "source": "example.com",
                "resolution": "1080p",
                "source_url": "https://site.test/post",
            },
        ]
        result = crawler._dedupe_results(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["episode"], "Episode 2")
        self.assertEqual(result[0]["resolution"], "1080p")

    def test_same_episode_quality_source_collapses_player_duplicates(self):
        rows = [
            {
                "title": "Show - Episode 4 - 720p",
                "episode": "Episode 4",
                "url": "https://streamwish.to/embed/a1",
                "source": "StreamWish",
                "resolution": "720p",
                "source_url": "https://site.test/post",
            },
            {
                "title": "Show - Episode 4 - 720p",
                "episode": "Episode 4",
                "url": "https://streamwish.to/player/b2",
                "source": "StreamWish",
                "resolution": "720p",
                "source_url": "https://site.test/post",
            },
        ]
        result = crawler._dedupe_results(rows)
        self.assertEqual(len(result), 1)

    def test_unknown_resolution_does_not_collapse_different_urls(self):
        rows = [
            {
                "title": "Show - Episode 5",
                "episode": "Episode 5",
                "url": "https://streamwish.to/embed/a1",
                "source": "StreamWish",
                "resolution": "Unknown",
                "source_url": "https://site.test/post",
            },
            {
                "title": "Show - Episode 5",
                "episode": "Episode 5",
                "url": "https://streamwish.to/embed/b2",
                "source": "StreamWish",
                "resolution": "Unknown",
                "source_url": "https://site.test/post",
            },
        ]
        result = crawler._dedupe_results(rows)
        self.assertEqual(len(result), 2)

    def test_different_resolutions_are_preserved(self):
        rows = [
            {
                "title": "Show - Episode 6 - 1080p",
                "episode": "Episode 6",
                "url": "https://streamwish.to/embed/a1",
                "source": "StreamWish",
                "resolution": "1080p",
                "source_url": "https://site.test/post",
            },
            {
                "title": "Show - Episode 6 - 720p",
                "episode": "Episode 6",
                "url": "https://streamwish.to/embed/b2",
                "source": "StreamWish",
                "resolution": "720p",
                "source_url": "https://site.test/post",
            },
        ]
        result = crawler._dedupe_results(rows)
        self.assertEqual(len(result), 2)


if __name__ == "__main__":
    unittest.main()
