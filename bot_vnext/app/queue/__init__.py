"""Queue package.

Keep package imports lazy so importing the standard-library ``queue`` module
is never shadowed by this package and manager modules do not form cycles.
"""

__all__ = ["DownloadManager", "UploadManager"]
