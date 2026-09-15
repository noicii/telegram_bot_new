import unittest

from app.downloader.engine import HybridDownloader


class HLSPlaylistSafetyTests(unittest.TestCase):
    def test_plain_ts_playlist_uses_segment_path(self):
        playlist = """#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\nseg-000.ts\n#EXTINF:6,\nseg-001.ts\n#EXT-X-ENDLIST\n"""
        self.assertFalse(HybridDownloader._playlist_requires_ffmpeg(playlist))

    def test_fmp4_playlist_uses_ffmpeg(self):
        playlist = """#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-MAP:URI=\"init.mp4\"\n#EXTINF:4,\nseg-000.m4s\n#EXT-X-ENDLIST\n"""
        self.assertTrue(HybridDownloader._playlist_requires_ffmpeg(playlist))

    def test_byte_range_playlist_uses_ffmpeg(self):
        playlist = """#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXT-X-BYTERANGE:1200@0\n#EXTINF:6,\nmedia.ts\n#EXT-X-BYTERANGE:1400\n#EXTINF:6,\nmedia.ts\n#EXT-X-ENDLIST\n"""
        self.assertTrue(HybridDownloader._playlist_requires_ffmpeg(playlist))

    def test_ffmpeg_headers_include_browser_cookies(self):
        headers = {"User-Agent": "test-agent", "Referer": "https://example.test/"}
        cookies = {"session": "abc123", "token": "xyz"}
        result = HybridDownloader._ffmpeg_headers(headers, cookies)
        self.assertEqual(result["Cookie"], "session=abc123; token=xyz")
        self.assertEqual(result["User-Agent"], "test-agent")
        self.assertEqual(result["Referer"], "https://example.test/")
        self.assertNotIn("Cookie", headers)

    def test_ffmpeg_headers_without_cookies_do_not_add_cookie(self):
        result = HybridDownloader._ffmpeg_headers({"Referer": "https://example.test/"}, {})
        self.assertNotIn("Cookie", result)

    def test_hls_retryable_statuses(self):
        for status in (408, 425, 429, 500, 502, 503, 504, 599):
            self.assertTrue(HybridDownloader._hls_status_retryable(status), status)
        for status in (200, 301, 400, 401, 403, 404):
            self.assertFalse(HybridDownloader._hls_status_retryable(status), status)

    def test_retry_after_seconds_header(self):
        self.assertEqual(HybridDownloader._retry_after_seconds("5"), 5.0)
        self.assertEqual(HybridDownloader._retry_after_seconds("999"), 30.0)
        self.assertIsNone(HybridDownloader._retry_after_seconds("not-a-delay"))
        self.assertIsNone(HybridDownloader._retry_after_seconds(None))

    def test_hls_retry_delay_respects_retry_after(self):
        self.assertEqual(HybridDownloader._hls_retry_delay(0, 7.5), 7.5)
        self.assertEqual(HybridDownloader._hls_retry_delay(1, 999), 30.0)

    def test_hls_retry_delay_is_bounded_without_retry_after(self):
        for attempt in range(3):
            delay = HybridDownloader._hls_retry_delay(attempt)
            self.assertGreaterEqual(delay, 0.0)
            self.assertLessEqual(delay, 12.0)


if __name__ == "__main__":
    unittest.main()
