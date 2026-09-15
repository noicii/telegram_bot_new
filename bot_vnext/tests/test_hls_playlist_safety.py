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


if __name__ == "__main__":
    unittest.main()
