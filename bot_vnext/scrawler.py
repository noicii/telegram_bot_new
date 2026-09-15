"""Full website crawler for series -> episode -> resolution -> media TXT output."""
from __future__ import annotations

import concurrent.futures
import html
import json
import re
import threading
from collections import defaultdict, deque
from urllib.parse import unquote, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from config import USER_AGENT
from crawler import _browser_discover

MEDIA_EXTENSIONS = (".mp4", ".m4v", ".webm", ".mov", ".mkv", ".m3u8", ".mpd", ".ts")
MEDIA_TYPES = ("video/", "application/vnd.apple.mpegurl", "application/x-mpegurl", "application/dash+xml")
PLAYER_MARKERS = ("/embed/", "/player/", "embed.", "player.", "/watch", "/play/", "/stream/", "iframe", "m3u8", "mpd", "video")
ATTRIBUTES = (
    "href", "src", "data", "content", "poster", "data-src", "data-url", "data-file",
    "data-video", "data-video-url", "data-video-src", "data-stream", "data-stream-url",
    "data-source", "data-hls", "data-m3u8", "data-mpd", "data-link", "data-download",
    "data-player", "data-embed", "data-iframe", "data-manifest", "data-playlist",
)
URL_RE = re.compile(r"https?://[^\s<>\"'`\\]+", re.I)
EP_RE = re.compile(
    r"\bS\s*(\d{1,2})\s*[-_. ]?\s*E(?:P(?:ISODE)?)?\s*(\d{1,4})\b|"
    r"\b(?:Episode|Ep)\s*[-_.:# ]*\s*(\d{1,4})\b|"
    r"\bE\s*[-_.:# ]*\s*(\d{1,4})\b",
    re.I,
)
RES_RE = re.compile(r"\b(2160p|1080p|720p|480p)\b|\b(4k|uhd)\b|\b(\d{3,4})\s*[x×]\s*(\d{3,4})\b", re.I)
DEFAULT_MAX_PAGES = 2000
DEFAULT_WORKERS = 16


def _clean(value: str) -> str:
    value = html.unescape(unquote(str(value or ""))).replace("\\/", "/")
    value = value.replace("\\u0026", "&").replace("+", " ")
    return re.sub(r"\s+", " ", value).strip()


def _canon(url: str, base: str = "") -> str:
    try:
        value = _clean(url).strip(" \t\r\n<>\"'`.,;)]}")
        if not value or value.startswith(("javascript:", "mailto:", "tel:", "#")):
            return ""
        value = urljoin(base, value)
        p = urlparse(value)
        if p.scheme.lower() not in {"http", "https"} or not p.netloc:
            return ""
        host = (p.hostname or "").lower()
        if not host:
            return ""
        return urlunparse((p.scheme.lower(), host, p.path or "/", p.params, p.query, ""))
    except Exception:
        return ""


def _media(url: str, content_type: str = "") -> bool:
    if not url:
        return False
    p = urlparse(url)
    lower = url.lower()
    if p.path.lower().endswith(MEDIA_EXTENSIONS):
        return True
    if any(x in lower for x in (".m3u8?", ".mpd?", "/manifest", "/playlist", "master.m3u8", "videoplayback")):
        return True
    return any(x in (content_type or "").lower() for x in MEDIA_TYPES)


def _player_like(url: str, text: str = "") -> bool:
    value = f"{url} {text}".lower()
    return any(marker in value for marker in PLAYER_MARKERS)


def _urls(text: str, base: str) -> set[str]:
    found = set()
    if not text:
        return found
    value = _clean(text)
    blobs = [value]
    for _ in range(2):
        value = html.unescape(unquote(value)).replace("\\/", "/")
        blobs.append(value)
    for blob in blobs:
        for raw in URL_RE.findall(blob):
            u = _canon(raw, base)
            if u:
                found.add(u)
    return found


def _json_urls(text: str, base: str) -> set[str]:
    found = set()
    if not text:
        return found
    for script in re.findall(r"<script[^>]*>(.*?)</script>", text, re.I | re.S):
        decoded = _clean(script)
        found.update(_urls(decoded, base))
        # JSON blobs often contain escaped URLs but are not valid standalone JSON.
        try:
            obj = json.loads(decoded)
        except Exception:
            continue
        stack = [obj]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, str):
                found.update(_urls(item, base))
    return found


def _episode(value: str) -> str:
    value = _clean(value)
    m = EP_RE.search(value)
    if not m:
        return "Unknown Episode"
    if m.group(1) and m.group(2):
        return f"Episode S{int(m.group(1)):02d}E{int(m.group(2)):02d}"
    number = next((g for g in m.groups()[2:] if g), None)
    return f"Episode {int(number):02d}" if number else "Unknown Episode"


def _resolution(value: str) -> str:
    value = _clean(value)
    m = RES_RE.search(value)
    if not m:
        return "Unknown"
    if m.group(1):
        return m.group(1).lower()
    if m.group(2):
        return "2160p"
    a, b = int(m.group(3)), int(m.group(4))
    largest = max(a, b)
    if largest >= 2160: return "2160p"
    if largest >= 1080: return "1080p"
    if largest >= 720: return "720p"
    if largest >= 480: return "480p"
    return "Unknown"


def _series(soup: BeautifulSoup, fallback: str = "") -> str:
    candidates = []
    for selector in ("h1", ".entry-title", ".post-title", "article h1", "meta[property='og:title']"):
        tag = soup.select_one(selector)
        if tag:
            candidates.append(tag.get("content") if tag.name == "meta" else tag.get_text(" ", strip=True))
    if soup.title:
        candidates.append(soup.title.get_text(" ", strip=True))
    name = _clean(next((x for x in candidates if x), fallback))
    if not name:
        return "Unknown Series"
    name = re.sub(r"\s*[|–—-]\s*(?:https?://)?(?:www\.)?[a-z0-9.-]+\.[a-z]{2,}.*$", "", name, flags=re.I)
    name = re.sub(r"\b(?:watch|download)\b\s*", "", name, flags=re.I)
    if _episode(name) != "Unknown Episode":
        # Try breadcrumb/category text before giving up.
        for selector in (".breadcrumb a", ".breadcrumbs a", "nav a", ".category a"):
            for tag in soup.select(selector):
                candidate = _clean(tag.get_text(" ", strip=True))
                if candidate and _episode(candidate) == "Unknown Episode" and len(candidate) > 2:
                    return candidate
        return "Unknown Series"
    name = re.sub(r"\s*[|–—-]\s*(?:complete|full|all\s+episodes?|reup).*$", "", name, flags=re.I)
    name = re.sub(r"\b(?:web\s*series|complete|full|all\s+episodes?|reup)\b", " ", name, flags=re.I)
    return re.sub(r"\s+", " ", name).strip(" -|–—:") or "Unknown Series"


def _source(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return "Unknown"
    for name, markers in (("FRDL", ("frdl",)), ("LuluStream", ("lulu",)), ("DoodStream", ("dood",)), ("StreamWish", ("streamwish",)), ("Vidhide", ("vidhide",))):
        if any(x in host for x in markers):
            return name
    return host


def _fetch(url: str, timeout: int = 25):
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5",
        "Accept-Language": "en-US,en;q=0.8",
        "Cache-Control": "no-cache",
    }
    try:
        r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        if r.status_code >= 400:
            return None
        return r
    except requests.RequestException:
        return None


def _scan_page(url: str, root_host: str, browser_budget: list[int], budget_lock: threading.Lock):
    response = _fetch(url)
    if response is None:
        return set(), set(), "Unknown Series", "Unknown Episode", 1, 1
    final_url = _canon(response.url) or url
    content_type = (response.headers.get("content-type") or "").lower()
    if _media(final_url, content_type):
        return {(final_url, "Unknown Episode", "Unknown", _source(final_url))}, set(), "Unknown Series", "Unknown Episode", 1, 0
    text = response.text or ""
    soup = BeautifulSoup(text, "lxml")
    series = _series(soup, final_url)
    page_episode = _episode(" ".join((soup.title.get_text(" ", strip=True) if soup.title else "", soup.get_text(" ", strip=True)[:12000], final_url)))
    if page_episode == "Unknown Episode":
        page_episode = _episode(final_url)
    media = set()
    links = set()
    player = False

    # Every HTML attribute is inspected. Only same-domain HTTP links are queued;
    # external links are retained only when they look like actual media.
    for tag in soup.find_all(True):
        tag_text = _clean(tag.get_text(" ", strip=True))
        if _player_like(str(tag), tag_text):
            player = True
        for attr in ATTRIBUTES:
            value = tag.get(attr)
            if not value:
                continue
            raw_values = [str(value)] + list(_urls(str(value), final_url))
            for raw in raw_values:
                u = _canon(raw, final_url)
                if not u:
                    continue
                if _media(u):
                    context = f"{tag_text} {tag.get('title','')} {tag.get('aria-label','')} {u}"
                    ep = _episode(context)
                    res = _resolution(context)
                    media.add((u, ep if ep != "Unknown Episode" else page_episode, res, _source(u)))
                elif urlparse(u).hostname == root_host and attr == "href":
                    links.add(u)

    for u in _urls(text, final_url) | _json_urls(text, final_url):
        if _media(u):
            media.add((u, page_episode, _resolution(u), _source(u)))

    # Browser discovery is intentionally budgeted so a large site does not launch
    # thousands of Chromium instances, while JS-heavy player pages still work.
    if not media and player:
        with budget_lock:
            allowed = browser_budget[0] > 0
            if allowed:
                browser_budget[0] -= 1
        if allowed:
            try:
                browser_urls, browser_text = _browser_discover(final_url)
                for u in browser_urls:
                    cu = _canon(u, final_url)
                    if cu and _media(cu):
                        media.add((cu, page_episode, _resolution(cu), _source(cu)))
                for u in _urls(browser_text, final_url):
                    if _media(u):
                        media.add((u, page_episode, _resolution(u), _source(u)))
            except Exception:
                pass
    return media, links, series, page_episode, 1, 0


def _sitemap_urls(start: str, root_host: str) -> set[str]:
    found = set()
    candidates = [urljoin(start, "/sitemap.xml"), urljoin(start, "/sitemap_index.xml")]
    for sitemap in candidates:
        r = _fetch(sitemap, timeout=15)
        if not r or "xml" not in (r.headers.get("content-type") or "").lower() and "<url" not in r.text[:500].lower():
            continue
        for raw in re.findall(r"<loc[^>]*>(.*?)</loc>", r.text or "", re.I | re.S):
            u = _canon(raw, sitemap)
            if u and urlparse(u).hostname == root_host:
                found.add(u)
    return found


def _crawl(raw_url: str, max_pages: int = DEFAULT_MAX_PAGES, workers: int = DEFAULT_WORKERS):
    start = _canon(raw_url)
    if not start:
        return [], {"pages": 0, "failed": 0, "series": 0, "episodes": 0, "links": 0, "duplicates": 0}
    root_host = (urlparse(start).hostname or "").lower()
    requested = int(max_pages or DEFAULT_MAX_PAGES)
    if requested == 300:
        requested = DEFAULT_MAX_PAGES
    max_pages = max(1, min(requested, DEFAULT_MAX_PAGES))
    workers = max(1, min(int(workers or DEFAULT_WORKERS), 32))

    queue = deque([start])
    queued = {start}
    visited = set()
    results = []
    browser_budget = [min(80, max_pages)]
    budget_lock = threading.Lock()

    # Sitemaps often contain archive/series pages that are not reachable from the
    # homepage navigation. Seed them without allowing them to exceed the page cap.
    for u in _sitemap_urls(start, root_host):
        if len(queued) >= max_pages:
            break
        if u not in queued:
            queued.add(u)
            queue.append(u)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        while queue and len(visited) < max_pages:
            batch = []
            while queue and len(batch) < workers and len(visited) + len(batch) < max_pages:
                u = queue.popleft()
                if u in visited:
                    continue
                batch.append(u)
            if not batch:
                continue
            futures = {pool.submit(_scan_page, u, root_host, browser_budget, budget_lock): u for u in batch}
            for future, page_url in futures.items():
                visited.add(page_url)
                try:
                    media, links, series, page_episode, _, failed = future.result()
                except Exception:
                    continue
                results.extend((series, ep, res, url, src, page_url) for url, ep, res, src in media if _media(url))
                for link in links:
                    if link not in queued and link not in visited and len(queued) < max_pages:
                        queued.add(link)
                        queue.append(link)

    # Canonical media URL is the strongest duplicate key. Preserve the richest
    # metadata encountered across pages pointing at the same media.
    by_url = {}
    for row in results:
        series, ep, res, url, src, page = row
        key = _canon(url)
        if not key:
            continue
        old = by_url.get(key)
        if old is None:
            by_url[key] = row
            continue
        if old[0] == "Unknown Series" and series != "Unknown Series":
            old = (series, old[1], old[2], old[3], old[4], old[5])
        if old[1] == "Unknown Episode" and ep != "Unknown Episode":
            old = (old[0], ep, old[2], old[3], old[4], old[5])
        if old[2] == "Unknown" and res != "Unknown":
            old = (old[0], old[1], res, old[3], old[4], old[5])
        by_url[key] = old

    rows = list(by_url.values())
    rows.sort(key=lambda x: (_clean(x[0]).lower(), _episode_sort(x[1]), _resolution_sort(x[2]), x[3]))
    stats = {
        "pages": len(visited),
        "failed": 0,
        "series": len({r[0] for r in rows if r[0] != "Unknown Series"}),
        "episodes": len({(r[0], r[1]) for r in rows}),
        "links": len(rows),
        "duplicates": max(0, len(results) - len(rows)),
    }
    return rows, stats


def _episode_sort(value: str):
    m = re.search(r"S(\d+)E(\d+)", value or "", re.I)
    if m:
        return (0, int(m.group(1)), int(m.group(2)))
    m = re.search(r"(\d+)", value or "")
    return (1, int(m.group(1)) if m else 999999, 0)


def _resolution_sort(value: str):
    m = re.search(r"(\d+)", value or "")
    return -(int(m.group(1)) if m else -1)


def _format(rows: list[tuple], stats: dict) -> list[str]:
    grouped = defaultdict(list)
    for series, ep, res, url, src, page in rows:
        grouped[series].append((ep, res, url))
    lines = []
    for series in sorted(grouped, key=lambda x: x.lower()):
        lines.append(f"Web Series: {series}")
        lines.append("")
        episodes = defaultdict(list)
        for ep, res, url in grouped[series]:
            episodes[ep].append((res, url))
        for ep in sorted(episodes, key=_episode_sort):
            lines.append(ep)
            seen = set()
            for res, url in sorted(episodes[ep], key=lambda x: (_resolution_sort(x[0]), x[1])):
                key = (res, url)
                if key in seen:
                    continue
                seen.add(key)
                lines.append(f"{res} — {url}")
            lines.append("")
        lines.append("")
    lines.extend([
        "--- SCAN SUMMARY ---",
        f"Pages scanned: {stats['pages']}",
        f"Series found: {stats['series']}",
        f"Episodes found: {stats['episodes']}",
        f"Video links found: {stats['links']}",
        f"Duplicates removed: {stats['duplicates']}",
    ])
    return lines


def crawl_website_media_urls(raw_url: str, max_pages: int = DEFAULT_MAX_PAGES, workers: int = DEFAULT_WORKERS) -> list[str]:
    """Full-site crawl returning TXT-ready series/episode/resolution lines.

    Kept under the existing function name so the production bot does not need a
    risky second integration point. The old 300-page caller is automatically
    upgraded to the 2000-page safe limit.
    """
    rows, stats = _crawl(raw_url, max_pages=max_pages, workers=workers)
    return _format(rows, stats)
