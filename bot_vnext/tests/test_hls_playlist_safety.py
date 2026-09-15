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


if __name__ == "__main__":
    unittest.main()
