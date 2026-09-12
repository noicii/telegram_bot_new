"""Uploader package.

Exports are intentionally lazy to keep Pyrogram and engine imports out of
package initialization and avoid circular imports during startup/tests.
"""

__all__ = ["UploadEngine", "UploadError"]
