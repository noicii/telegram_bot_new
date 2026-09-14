"""Website-wide crawler that returns only actual video/media URLs."""
from __future__ import annotations

import concurrent.futures
import re
from collections import deque
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from config import USER_AGENT

MEDIA_EXTENSIONS = (".mp4", ".m4v", ".webm", ".mov", ".mkv", ".m3u8", ".mpd")
MEDIA_CONTENT_TYPES = ("video/", "application/vnd.apple.mpegurl", "application/x-mpegurl", "application/dash+xml")
URL_RE = re.compile(r"https?://[^\s<>\"'`\\]+", re.I)
ATTRIBUTES = ("href", "src", "data", "content", "data-src", "data-url", "data-file", "data-video", "data-video-url", "data-video-src", "data-stream", "data-stream-url", "data-source", "data-hls", "data-m3u8", "data-mpd", "data-link", "data-download", "data-player", "data-embed", "data-iframe", "data-manifest", "data-playlist")


def _canon(url: str, base: str = "") -> str:
    try:
        value = urljoin(base, str(url).strip())
        p = urlparse(value)
        if p.scheme.lower() not in {"http", "https"} or not p.netloc:
            return ""
        return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path or "/", p.params, p.query, ""))
    except Exception:
        return ""


def _is_media(url: str) -> bool:
    p = urlparse(url)
    lower = (url or "").lower()
    return p.path.lower().endswith(MEDIA_EXTENSIONS) or any(x in lower for x in (".m3u8?", ".mpd?", "/manifest", "/playlist", "videoplayback", "master.m3u8"))


def _extract_media(text: str, base: str) -> set[str]:
    found: set[str] = set()
    if not text:
        return found
    for raw in URL_RE.findall(text):
        u = _canon(raw, base)
        if u and _is_media(u):
            found.add(u)
    return found


def _fetch(url: str, timeout: int = 20):
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,video/*;q=0.8,*/*;q=0.5", "Accept-Language": "en-US,en;q=0.8"}
    try:
        r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        if r.status_code >= 400:
            return None
        return r
    except requests.RequestException:
        return None


def _scan_page(url: str, root_host: str):
    response = _fetch(url)
    if response is None:
        return set(), set()
    final_url = _canon(response.url) or url
    content_type = (response.headers.get("content-type") or "").lower()
    if _is_media(final_url) or any(t in content_type for t in MEDIA_CONTENT_TYPES):
        return {final_url}, set()
    text = response.text or ""
    media = _extract_media(text, final_url)
    soup = BeautifulSoup(text, "lxml")
    links: set[str] = set()
    for tag in soup.find_all(True):
        for attr in ATTRIBUTES:
            value = tag.get(attr)
            if not value:
                continue
            for candidate in URL_RE.findall(str(value)) or [str(value)]:
                u = _canon(candidate, final_url)
                if not u:
                    continue
                if _is_media(u):
                    media.add(u)
                elif urlparse(u).hostname == root_host and tag.name in {"a", "area", "link", "iframe", "frame"}:
                    links.add(u)
    media.update(_extract_media(text, final_url))
    return media, links


def crawl_website_media_urls(raw_url: str, max_pages: int = 300, workers: int = 16) -> list[str]:
    """Crawl same-domain pages and return only unique actual media URLs."""
    start = _canon(raw_url)
    if not start:
        return []
    root_host = (urlparse(start).hostname or "").lower()
    if not root_host:
        return []
    max_pages = max(1, min(int(max_pages or 300), 2000))
    workers = max(1, min(int(workers or 16), 32))
    queue = deque([start])
    queued = {start}
    visited: set[str] = set()
    media: set[str] = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        while queue and len(visited) < max_pages:
            batch = []
            while queue and len(batch) < workers and len(visited) + len(batch) < max_pages:
                batch.append(queue.popleft())
            for url, future in zip(batch, [pool.submit(_scan_page, u, root_host) for u in batch]):
                visited.add(url)
                try:
                    page_media, links = future.result()
                except Exception:
                    continue
                media.update(page_media)
                for link in links:
                    if link not in queued and link not in visited and len(queued) < max_pages:
                        queued.add(link)
                        queue.append(link)
    return sorted(media, key=lambda u: (urlparse(u).path.lower(), u.lower()))
