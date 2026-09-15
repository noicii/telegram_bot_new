"""Full website crawler for series -> episode -> resolution -> media URLs."""
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
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import USER_AGENT
from crawler import _browser_discover

MEDIA_EXTENSIONS = (".mp4", ".m4v", ".webm", ".mov", ".mkv", ".m3u8", ".mpd", ".ts")
MEDIA_TYPES = ("video/", "application/vnd.apple.mpegurl", "application/x-mpegurl", "application/dash+xml")
PLAYER_MARKERS = ("/embed/", "/player/", "embed.", "player.", "/watch", "/play/", "/stream/", "iframe", "m3u8", "mpd", "video")
PLAYER_PATH_RE = re.compile(r"/(?:d|e|embed|player|watch|play|stream)(?:/|$)", re.I)
ATTRIBUTES = ("href", "src", "data", "content", "poster", "data-src", "data-url", "data-file", "data-video", "data-video-url", "data-video-src", "data-stream", "data-stream-url", "data-source", "data-hls", "data-m3u8", "data-mpd", "data-link", "data-download", "data-player", "data-embed", "data-iframe", "data-manifest", "data-playlist", "srcset", "imagesrcset", "action", "formaction", "ping", "cite")
URL_RE = re.compile(r"(?:https?:)?//[^\s<>\"'`\\]+", re.I)
QUOTED_RELATIVE_RE = re.compile(r"[\"'`]((?:/|\./|\.\./)[^\"'`<>\s]+)[\"'`]", re.I)
CSS_URL_RE = re.compile(r"url\(\s*[\"']?([^\"')\s]+)[\"']?\s*\)", re.I)
EP_RE = re.compile(r"\bS\s*(\d{1,2})\s*[-_. ]?\s*E(?:P(?:ISODE)?)?\s*(\d{1,4})\b|\b(?:Episode|Ep)\s*[-_.:# ]*\s*(\d{1,4})\b|\bE\s*[-_.:# ]*\s*(\d{1,4})\b", re.I)
RES_RE = re.compile(r"\b(2160p|1080p|720p|480p)\b|\b(4k|uhd)\b|\b(\d{3,4})\s*[x×]\s*(\d{3,4})\b", re.I)
DEFAULT_MAX_PAGES = 2000
DEFAULT_WORKERS = 16
MAX_EXTERNAL_PLAYER_HOPS = 128
MAX_BROWSER_DISCOVERIES = 200

_thread_local = threading.local()


def _clean(value: str) -> str:
    # Do not convert '+' to spaces: '+' is a valid literal URL/query character.
    value = html.unescape(str(value or "")).replace("\\/", "/").replace("\\u0026", "&")
    return re.sub(r"\s+", " ", value).strip()


def _canon(url: str, base: str = "") -> str:
    try:
        value = _clean(url).strip(" \t\r\n<>\"'`.,;)]}")
        if not value or value.lower().startswith(("javascript:", "mailto:", "tel:", "#", "data:")):
            return ""
        p = urlparse(urljoin(base, value))
        if p.scheme.lower() not in {"http", "https"} or not p.netloc or not p.hostname:
            return ""
        return urlunparse((p.scheme.lower(), p.hostname.lower(), p.path or "/", p.params, p.query, ""))
    except Exception:
        return ""


def _media(url: str, content_type: str = "") -> bool:
    if not url:
        return False
    p = urlparse(url)
    lower = url.lower()
    return p.path.lower().endswith(MEDIA_EXTENSIONS) or any(x in lower for x in (".m3u8?", ".mpd?", "/manifest", "/playlist", "master.m3u8", "videoplayback")) or any(x in (content_type or "").lower() for x in MEDIA_TYPES)


def _player_like(url: str, text: str = "") -> bool:
    value = f"{url} {text}".lower()
    return any(x in value for x in PLAYER_MARKERS) or bool(PLAYER_PATH_RE.search(urlparse(url).path or ""))


def _urls(text: str, base: str) -> set[str]:
    if not text:
        return set()
    value = html.unescape(str(text)).replace("\\/", "/")
    found = set()
    for blob in (value, unquote(value)):
        for raw in URL_RE.findall(blob):
            u = _canon(raw, base)
            if u: found.add(u)
        for raw in QUOTED_RELATIVE_RE.findall(blob):
            u = _canon(raw, base)
            if u: found.add(u)
        for raw in CSS_URL_RE.findall(blob):
            u = _canon(raw, base)
            if u: found.add(u)
    return found


def _json_urls(text: str, base: str) -> set[str]:
    found = set()
    for script in re.findall(r"<script[^>]*>(.*?)</script>", text or "", re.I | re.S):
        found.update(_urls(script, base))
        try: obj = json.loads(html.unescape(script).strip())
        except Exception: obj = None
        stack = [obj] if obj is not None else []
        while stack:
            item = stack.pop()
            if isinstance(item, dict): stack.extend(item.values())
            elif isinstance(item, list): stack.extend(item)
            elif isinstance(item, str): found.update(_urls(item, base))
    return found


def _episode(value: str) -> str:
    m = EP_RE.search(_clean(value))
    if not m: return "Unknown Episode"
    if m.group(1) and m.group(2): return f"Episode S{int(m.group(1)):02d}E{int(m.group(2)):02d}"
    n = next((g for g in m.groups()[2:] if g), None)
    return f"Episode {int(n):02d}" if n else "Unknown Episode"


def _resolution(value: str) -> str:
    m = RES_RE.search(_clean(value))
    if not m: return "Unknown"
    if m.group(1): return m.group(1).lower()
    if m.group(2): return "2160p"
    largest = max(int(m.group(3)), int(m.group(4)))
    return "2160p" if largest >= 2160 else "1080p" if largest >= 1080 else "720p" if largest >= 720 else "480p" if largest >= 480 else "Unknown"


def _series(soup: BeautifulSoup, fallback: str = "") -> str:
    candidates = []
    for selector in ("h1", ".entry-title", ".post-title", "article h1", "meta[property='og:title']"):
        tag = soup.select_one(selector)
        if tag: candidates.append(tag.get("content") if tag.name == "meta" else tag.get_text(" ", strip=True))
    if soup.title: candidates.append(soup.title.get_text(" ", strip=True))
    name = _clean(next((x for x in candidates if x), fallback)) or "Unknown Series"
    name = re.sub(r"\s*[|–—-]\s*(?:https?://)?(?:www\.)?[a-z0-9.-]+\.[a-z]{2,}.*$", "", name, flags=re.I)
    name = re.sub(r"\b(?:watch|download)\b\s*", "", name, flags=re.I)
    if _episode(name) != "Unknown Episode":
        for selector in (".breadcrumb a", ".breadcrumbs a", "nav a", ".category a"):
            for tag in soup.select(selector):
                candidate = _clean(tag.get_text(" ", strip=True))
                if candidate and _episode(candidate) == "Unknown Episode" and len(candidate) > 2: return candidate
        return "Unknown Series"
    name = re.sub(r"\s*[|–—-]\s*(?:complete|full|all\s+episodes?|reup).*$", "", name, flags=re.I)
    name = re.sub(r"\b(?:web\s*series|complete|full|all\s+episodes?|reup)\b", " ", name, flags=re.I)
    return re.sub(r"\s+", " ", name).strip(" -|–—:") or "Unknown Series"


def _source(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    for name, markers in (("FRDL", ("frdl",)), ("LuluStream", ("lulu",)), ("DoodStream", ("dood",)), ("StreamWish", ("streamwish",)), ("Vidhide", ("vidhide",))):
        if any(x in host for x in markers): return name
    return host or "Unknown"


def _session():
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        retry = Retry(total=2, connect=2, read=2, status=2, backoff_factor=0.4, status_forcelist=(408, 425, 429, 500, 502, 503, 504), allowed_methods=frozenset(("GET",)))
        adapter = HTTPAdapter(pool_connections=32, pool_maxsize=32, max_retries=retry)
        session.mount("http://", adapter); session.mount("https://", adapter)
        session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5", "Accept-Language": "en-US,en;q=0.8", "Cache-Control": "no-cache"})
        _thread_local.session = session
    return session


def _fetch(url: str, timeout: int = 25):
    try:
        r = _session().get(url, timeout=timeout, allow_redirects=True)
        return None if r.status_code >= 400 else r
    except requests.RequestException:
        return None


def _crawlable(u: str) -> bool:
    path = urlparse(u).path.lower()
    return not _media(u) and not any(path.endswith(x) for x in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".css", ".js", ".woff", ".woff2", ".ttf", ".ico", ".xml", ".json", ".txt", ".pdf", ".zip", ".rar"))


def _same_site(host: str, root: str) -> bool:
    host, root = (host or "").lower().rstrip("."), (root or "").lower().rstrip(".")
    return host == root or bool(root and host.endswith("." + root))


def _is_followup_link(u: str, root_host: str) -> bool:
    return _same_site(urlparse(u).hostname or "", root_host) or _player_like(u)


def _page_context(soup: BeautifulSoup, final_url: str):
    bits = []
    if soup.title: bits.append(soup.title.get_text(" ", strip=True))
    for selector in ("h1", ".entry-title", ".post-title", "meta[property='og:title']"):
        tag = soup.select_one(selector)
        if tag: bits.append(tag.get("content") if tag.name == "meta" else tag.get_text(" ", strip=True))
    bits.append(final_url)
    value = " ".join(bits)
    return _episode(value), _resolution(value)


def _scan_page(url: str, root_host: str, browser_budget: list[int], budget_lock: threading.Lock):
    response = _fetch(url)
    if response is None: return set(), set(), "Unknown Series", "Unknown Episode", 1, 1
    final_url = _canon(response.url) or url
    ctype = (response.headers.get("content-type") or "").lower()
    if _media(final_url, ctype): return {(final_url, "Unknown Episode", "Unknown", _source(final_url))}, set(), "Unknown Series", "Unknown Episode", 1, 0
    text = response.text or ""
    soup = BeautifulSoup(text, "lxml")
    series = _series(soup, final_url)
    page_ep, page_res = _page_context(soup, final_url)
    media, links = set(), set()
    player = _player_like(final_url, text[:30000])

    for tag in soup.find_all(True):
        tag_text = _clean(tag.get_text(" ", strip=True))
        tag_repr = str(tag)
        player = player or _player_like(tag_repr, tag_text)
        for attr in ATTRIBUTES:
            value = tag.get(attr)
            if not value: continue
            values = [str(value)]
            if attr in {"srcset", "imagesrcset"}: values += [x.strip().split(" ")[0] for x in str(value).split(",") if x.strip()]
            values += list(_urls(str(value), final_url))
            for raw in values:
                u = _canon(raw, final_url)
                if not u: continue
                if _media(u):
                    ctx = f"{tag_text} {tag.get('title','')} {tag.get('aria-label','')} {u}"
                    ep, res = _episode(ctx), _resolution(ctx)
                    media.add((u, ep if ep != "Unknown Episode" else page_ep, res if res != "Unknown" else page_res, _source(u)))
                elif _is_followup_link(u, root_host) and _crawlable(u): links.add(u)

    for u in _urls(text, final_url) | _json_urls(text, final_url):
        if _media(u):
            ep = _episode(u)
            media.add((u, ep if ep != "Unknown Episode" else page_ep, _resolution(u), _source(u)))
        elif _is_followup_link(u, root_host) and _crawlable(u): links.add(u)

    for tag in soup.find_all("meta"):
        content = str(tag.get("content") or "")
        m = re.search(r"url\s*=\s*([^;]+)$", content, re.I)
        if m:
            u = _canon(m.group(1).strip(), final_url)
            if u and _is_followup_link(u, root_host) and _crawlable(u): links.add(u)
    for tag in soup.find_all("link"):
        u = _canon(tag.get("href"), final_url)
        if u and _is_followup_link(u, root_host) and _crawlable(u): links.add(u)

    if player:
        with budget_lock:
            allowed = browser_budget[0] > 0
            if allowed: browser_budget[0] -= 1
        if allowed:
            try:
                browser_urls, browser_text = _browser_discover(final_url)
                for u in set(browser_urls) | _urls(browser_text, final_url):
                    cu = _canon(u, final_url)
                    if not cu: continue
                    if _media(cu): media.add((cu, _episode(cu) if _episode(cu) != "Unknown Episode" else page_ep, _resolution(cu), _source(cu)))
                    elif _is_followup_link(cu, root_host) and _crawlable(cu): links.add(cu)
            except Exception:
                pass
    return media, links, series, page_ep, 1, 0


def _sitemap_urls(start: str, root_host: str) -> set[str]:
    found, seen, pending = set(), set(), deque((urljoin(start, x) for x in ("/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml")))
    while pending and len(seen) < 50:
        sitemap = _canon(pending.popleft(), start)
        if not sitemap or sitemap in seen: continue
        seen.add(sitemap)
        r = _fetch(sitemap, 15)
        if not r or ("xml" not in (r.headers.get("content-type") or "").lower() and "<url" not in r.text[:500].lower() and "<sitemap" not in r.text[:500].lower()): continue
        for raw in re.findall(r"<loc[^>]*>(.*?)</loc>", r.text or "", re.I | re.S):
            u = _canon(raw, sitemap)
            if not u: continue
            if _same_site(urlparse(u).hostname or "", root_host) and _crawlable(u): found.add(u)
            elif u not in seen and len(seen) < 50: pending.append(u)
    return found


def _crawl(raw_url: str, max_pages: int = DEFAULT_MAX_PAGES, workers: int = DEFAULT_WORKERS):
    start = _canon(raw_url)
    if not start: return [], {"pages": 0, "failed": 0, "series": 0, "episodes": 0, "links": 0, "duplicates": 0}
    root_host = (urlparse(start).hostname or "").lower()
    max_pages = max(1, min(int(max_pages or DEFAULT_MAX_PAGES), DEFAULT_MAX_PAGES))
    workers = max(1, min(int(workers or DEFAULT_WORKERS), 32))
    queue, queued, visited, results = deque([start]), {start}, set(), []
    external_hops, failed_pages = 0, 0
    browser_budget, budget_lock = [min(MAX_BROWSER_DISCOVERIES, max_pages)], threading.Lock()
    for u in _sitemap_urls(start, root_host):
        if len(queued) >= max_pages: break
        if u not in queued: queued.add(u); queue.append(u)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        while queue and len(visited) < max_pages:
            batch = []
            while queue and len(batch) < workers and len(visited) + len(batch) < max_pages:
                u = queue.popleft()
                if u not in visited: batch.append(u)
            if not batch: continue
            futures = {pool.submit(_scan_page, u, root_host, browser_budget, budget_lock): u for u in batch}
            for future, page_url in futures.items():
                visited.add(page_url)
                try: media, links, series, page_ep, _, failed = future.result(); failed_pages += int(failed or 0)
                except Exception: failed_pages += 1; continue
                results.extend((series, ep, res, u, src, page_url) for u, ep, res, src in media if _media(u))
                for link in links:
                    if link in queued or link in visited or len(queued) >= max_pages: continue
                    host = (urlparse(link).hostname or "").lower()
                    if not _same_site(host, root_host):
                        if not _player_like(link) or external_hops >= MAX_EXTERNAL_PLAYER_HOPS: continue
                        external_hops += 1
                    queued.add(link); queue.append(link)

    by_url = {}
    for row in results:
        key = _canon(row[3])
        if not key: continue
        old = by_url.get(key)
        if old is None: by_url[key] = row; continue
        by_url[key] = (row[0] if old[0] == "Unknown Series" and row[0] != "Unknown Series" else old[0], row[1] if old[1] == "Unknown Episode" and row[1] != "Unknown Episode" else old[1], row[2] if old[2] == "Unknown" and row[2] != "Unknown" else old[2], old[3], old[4], old[5])
    rows = list(by_url.values())
    rows.sort(key=lambda x: (_clean(x[0]).lower(), _episode_sort(x[1]), _resolution_sort(x[2]), x[3]))
    stats = {"pages": len(visited), "failed": failed_pages, "series": len({r[0] for r in rows if r[0] != "Unknown Series"}), "episodes": len({(r[0], r[1]) for r in rows}), "links": len(rows), "duplicates": max(0, len(results) - len(rows))}
    return rows, stats


def _episode_sort(value: str):
    m = re.search(r"S(\d+)E(\d+)", value or "", re.I)
    if m: return (0, int(m.group(1)), int(m.group(2)))
    m = re.search(r"(\d+)", value or "")
    return (1, int(m.group(1)) if m else 999999, 0)


def _resolution_sort(value: str):
    m = re.search(r"(\d+)", value or "")
    return -(int(m.group(1)) if m else -1)


def _format(rows: list[tuple], stats: dict) -> list[str]:
    grouped = defaultdict(list)
    for series, ep, res, url, src, page in rows: grouped[series].append((ep, res, url))
    lines = []
    for series in sorted(grouped, key=str.lower):
        lines += [f"Web Series: {series}", ""]
        episodes = defaultdict(list)
        for ep, res, url in grouped[series]: episodes[ep].append((res, url))
        for ep in sorted(episodes, key=_episode_sort):
            lines.append(ep); seen = set()
            for res, url in sorted(episodes[ep], key=lambda x: (_resolution_sort(x[0]), x[1])):
                if (res, url) not in seen: seen.add((res, url)); lines.append(f"{res} — {url}")
            lines.append("")
        lines.append("")
    lines += ["--- SCAN SUMMARY ---", f"Pages scanned: {stats['pages']}", f"Failed pages: {stats['failed']}", f"Series found: {stats['series']}", f"Episodes found: {stats['episodes']}", f"Video links found: {stats['links']}", f"Duplicates removed: {stats['duplicates']}"]
    return lines


def crawl_website_media_urls(raw_url: str, max_pages: int = DEFAULT_MAX_PAGES, workers: int = DEFAULT_WORKERS) -> list[str]:
    """Return only unique media URLs; /scrawl writes this list directly to TXT."""
    rows, _ = _crawl(raw_url, max_pages=max_pages, workers=workers)
    seen, urls = set(), []
    for row in rows:
        url = row[3]
        if url and url not in seen: seen.add(url); urls.append(url)
    return urls
