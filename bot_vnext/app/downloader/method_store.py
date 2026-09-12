from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

DB_PATH = Path(__file__).resolve().parents[3] / "bot_vnext.db"
METHODS = ("auto", "hls-multi", "yt-dlp", "browser", "ffmpeg", "aria2c", "direct")
DEFAULT_KEY = "__default__"


def domain_for(url: str) -> str:
    return (urlparse(str(url)).netloc or "").lower().split(":", 1)[0]


def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS downloader_preferences (domain TEXT PRIMARY KEY, method TEXT NOT NULL)")


def _get_sync(domain: str) -> str:
    conn = _connect()
    try:
        _ensure(conn)
        row = conn.execute("SELECT method FROM downloader_preferences WHERE domain = ?", (domain,)).fetchone()
        if row and row[0] in METHODS:
            return row[0]
        row = conn.execute("SELECT method FROM downloader_preferences WHERE domain = ?", (DEFAULT_KEY,)).fetchone()
        return row[0] if row and row[0] in METHODS else "auto"
    finally:
        conn.close()


def _set_sync(domain: str, method: str) -> str:
    method = method if method in METHODS else "auto"
    conn = _connect()
    try:
        _ensure(conn)
        key = domain or DEFAULT_KEY
        conn.execute("INSERT INTO downloader_preferences(domain, method) VALUES(?, ?) ON CONFLICT(domain) DO UPDATE SET method=excluded.method", (key, method))
        conn.commit()
        return method
    finally:
        conn.close()


async def get_method(url: str) -> str:
    return await asyncio.to_thread(_get_sync, domain_for(url))


async def set_method(url: str, method: str) -> str:
    return await asyncio.to_thread(_set_sync, domain_for(url), method)


async def get_default_method() -> str:
    return await asyncio.to_thread(_get_sync, "")


async def set_default_method(method: str) -> str:
    return await asyncio.to_thread(_set_sync, "", method)


def next_method(method: str) -> str:
    current = method if method in METHODS else "auto"
    return METHODS[(METHODS.index(current) + 1) % len(METHODS)]


def method_label(method: str) -> str:
    return {
        "auto": "🤖 Auto",
        "hls-multi": "⚡ HLS Multi 4x",
        "yt-dlp": "🎯 yt-dlp",
        "browser": "🌐 Browser",
        "ffmpeg": "🎞️ FFmpeg",
        "aria2c": "⚡ aria2c",
        "direct": "📥 Direct",
    }.get(method, "🤖 Auto")
