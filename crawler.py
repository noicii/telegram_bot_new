import html
import json
import re
from urllib.parse import unquote, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from config import PROTECTED_DOMAINS, SETTINGS, USER_AGENT
from utils import sanitize_filename

MEDIA_EXTENSIONS = (".mp4", ".m4v", ".webm", ".mov", ".mkv", ".m3u8", ".mpd")
MEDIA_MARKERS = (".m3u8", ".mpd", "manifest", "playlist", "master.m3u8", "videoplayback", "hls", "dash")
PLAYER_MARKERS = ("/embed/", "/player/", "embed.", "player.", "/watch", "/play/", "/stream/", "iframe", "vidsrc", "filemoon", "streamtape", "streamwish", "dood", "vidhide", "lulu")
PLAYER_PATH_RE = re.compile(r"/(?:d|e|embed|player|watch|play|stream)(?:/|$)", re.I)
URL_RE = re.compile(r"https?://[^\s<>\"'`\\]+", re.I)
SCHEMELESS_URL_RE = re.compile(r"(?<![\w@])(?:www\.)?[a-z0-9.-]+\.[a-z]{2,}(?:/[^\s<>\"'`\\]*)?", re.I)
ATTRIBUTES = (
    "href", "src", "data", "content", "data-src", "data-url", "data-file", "data-video",
    "data-video-url", "data-video-src", "data-stream", "data-stream-url", "data-source",
    "data-hls", "data-m3u8", "data-mpd", "data-link", "data-download", "data-player",
    "data-embed", "data-iframe", "data-manifest", "data-playlist", "poster",
)


def clean_text(value):
    value = html.unescape(unquote(str(value or "")))
    for _ in range(2):
        value = html.unescape(unquote(value)).replace("\\/", "/").replace("\\u0026", "&")
    # Never convert '+' to a space here. In URLs, '+' is a literal character
    # and %2B is explicitly an encoded '+'. Converting it breaks signed media
    # URLs and can make otherwise valid HLS links fail.
    return re.sub(r"\s+", " ", value).strip()


def normalize_url(url, base_url=None):
    value = clean_text(url).strip(" \t\r\n<>\"'`.,;)]}")
    if not value or value.startswith(("javascript:", "mailto:", "tel:", "#")):
        return ""
    if value.startswith("//"):
        value = (urlparse(base_url).scheme if base_url else "https") + ":" + value
    elif base_url:
        value = urljoin(base_url, value)
    try:
        p = urlparse(value)
        if p.scheme.lower() not in {"http", "https"} or not p.netloc:
            return ""
        host = (p.hostname or "").lower()
        if not host:
            return ""
        port = p.port
        netloc = host
        if port and not ((p.scheme.lower() == "http" and port == 80) or (p.scheme.lower() == "https" and port == 443)):
            netloc = f"{host}:{port}"
        return urlunparse((p.scheme.lower(), netloc, p.path or "/", p.params, p.query, ""))
    except Exception:
        return ""


def canonical_url_key(url):
    value = normalize_url(url)
    if not value:
        return ""
    p = urlparse(value)
    return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path, p.params, p.query, ""))


def is_http_url(url):
    return bool(normalize_url(url))


def is_protected_url(url):
    if not url:
        return False
    p = urlparse(url)
    host = (p.hostname or "").lower()
    full = url.lower()
    return any(domain.lower().lstrip(".") in host or domain.lower() in full for domain in PROTECTED_DOMAINS)


def is_media_url(url):
    if not url:
        return False
    lower = url.lower()
    path = urlparse(url).path.lower()
    return path.endswith(MEDIA_EXTENSIONS) or any(marker in lower for marker in MEDIA_MARKERS)


def is_player_like_url(url, text=""):
    value = f"{url} {text}".lower()
    if any(marker in value for marker in PLAYER_MARKERS):
        return True
    try:
        return bool(PLAYER_PATH_RE.search(urlparse(url).path or ""))
    except Exception:
        return False


def extract_urls_from_text(text, base_url=None):
    if not text:
        return []
    decoded = clean_text(text)
    found = []
    blobs = [decoded]
    for _ in range(3):
        decoded = html.unescape(unquote(decoded)).replace("\\/", "/")
        blobs.append(decoded)
    for blob in blobs:
        for match in URL_RE.findall(blob):
            candidate = normalize_url(match, base_url)
            if candidate:
                found.append(candidate)
        for match in SCHEMELESS_URL_RE.findall(blob):
            candidate = normalize_url("https://" + match, base_url)
            if candidate:
                found.append(candidate)
    return list(dict.fromkeys(found))


def extract_json_urls(text, base_url=None):
    found = []
    if not text:
        return found
    for blob in (text, html.unescape(text), unquote(text)):
        try:
            data = json.loads(blob)
        except Exception:
            continue
        stack = [data]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, str):
                found.extend(extract_urls_from_text(item, base_url))
    return list(dict.fromkeys(found))


def extract_protected_urls(text):
    return [u for u in extract_urls_from_text(text) if is_protected_url(u)]


def _headers():
    return {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.8", "Cache-Control": "no-cache"}


def _request_page(url, timeout=30):
    response = requests.get(url, headers=_headers(), timeout=timeout, allow_redirects=True)
    response.raise_for_status()
    return response


def _is_cloudflare_challenge(text):
    lower = (text or "").lower()
    return sum(x in lower for x in ("just a moment...", "__cf_chl_", "cf-chl-", "cf_chl_opt")) >= 2


def resolve_blog_links(raw_url):
    raw_url = raw_url.strip()
    if not raw_url:
        return []
    if is_protected_url(raw_url) or raw_url.lower().startswith("magnet:") or is_media_url(raw_url):
        return [raw_url]
    if not is_http_url(raw_url):
        return [raw_url]
    try:
        response = _request_page(raw_url)
    except requests.RequestException:
        return []
    if response.status_code == 403 or _is_cloudflare_challenge(response.text):
        return []
    soup = BeautifulSoup(response.text, "lxml")
    found = []
    for tag in soup.find_all(True):
        for attr in ATTRIBUTES:
            value = tag.get(attr)
            if value:
                found.extend(extract_urls_from_text(value, response.url))
    found.extend(extract_urls_from_text(response.text, response.url))
    found.extend(extract_json_urls(response.text, response.url))
    return list(dict.fromkeys(u for u in found if is_protected_url(u) or is_media_url(u) or is_player_like_url(u)))


def parse_time_range(value):
    if not value:
        return None
    value = value.strip()
    for pattern in (r"^(\d{2}):(\d{2}):(\d{2})-(\d{2}):(\d{2}):(\d{2})$", r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})$"):
        m = re.match(pattern, value)
        if not m:
            continue
        parts = [int(x) for x in m.groups()]
        if len(parts) == 6:
            start = parts[0] * 3600 + parts[1] * 60 + parts[2]
            end = parts[3] * 3600 + parts[4] * 60 + parts[5]
        else:
            start = parts[0] * 60 + parts[1]
            end = parts[2] * 60 + parts[3]
        return (start, end) if end > start else None
    return None


def parse_input_line(line):
    parts = [part.strip() for part in line.strip().split("|")]
    if not parts or not parts[0]:
        return None
    channel_id = None
    trim_range = None
    custom_name = None
    for part in parts[1:]:
        if not part:
            continue
        if re.fullmatch(r"-?100\d+", part):
            channel_id = int(part)
        elif (parsed := parse_time_range(part)):
            trim_range = parsed
        elif SETTINGS.get("custom_rename", True):
            custom_name = part
    return {"url": parts[0], "channel_id": channel_id, "trim_range": trim_range, "custom_name": custom_name}


def parse_input_lines(lines):
    return [item for line in lines if (item := parse_input_line(line))]


def _episode_candidates(value):
    value = clean_text(value)
    patterns = (r"\bS\d{1,2}\s*[-_. ]?\s*E(?:pisode)?\s*(\d{1,4})\b", r"\bEpisode[\s._:#-]*(\d{1,4})\b", r"\bEp(?:isode)?[\s._:#-]*(\d{1,4})\b", r"(?:^|[^a-z])E[\s._:#-]*(\d{1,4})(?:[^0-9]|$)")
    for pattern in patterns:
        m = re.search(pattern, value, re.I)
        if m:
            return f"Episode {int(m.group(1))}"
    return None


def _resolution(value):
    value = clean_text(value)
    if re.search(r"\b2160p\b|\b4k\b|\buhd\b", value, re.I): return "2160p"
    if re.search(r"\b1080p\b", value, re.I): return "1080p HEVC" if re.search(r"\b(?:hevc|h265|h\.265|x265)\b", value, re.I) else "1080p"
    if re.search(r"\b720p\b", value, re.I): return "720p"
    if re.search(r"\b480p\b", value, re.I): return "480p"
    m = re.search(r"\b(\d{3,4})\s*[x×]\s*(\d{3,4})\b", value)
    if m:
        largest = max(int(m.group(1)), int(m.group(2)))
        if largest >= 2160: return "2160p"
        if largest >= 1080: return "1080p"
        if largest >= 720: return "720p"
        if largest >= 480: return "480p"
    return "Unknown"


def _source(url, text=""):
    combined = f"{url} {text}".lower()
    for name, markers in (("FRDL", ("frdl.my", "frdl.io")), ("LuluStream", ("luluvid.com", "lulustream")), ("DoodStream", ("doodstream", "myvidplay.com")), ("StreamWish", ("streamwish",)), ("Vidhide", ("vidhide",))):
        if any(marker in combined for marker in markers): return name
    return (urlparse(url).hostname or "Unknown").lower()


def _series_name(soup):
    candidates = []
    for selector in ("h1", "article h1", ".entry-title", "meta[property='og:title']"):
        tag = soup.select_one(selector)
        if tag: candidates.append(tag.get("content") if tag.name == "meta" else tag.get_text(" ", strip=True))
    if soup.title: candidates.append(soup.title.get_text(" ", strip=True))
    name = clean_text(next((x for x in candidates if x), ""))
    name = re.sub(r"\s*[|\-–—]\s*(?:https?://)?(?:www\.)?[a-z0-9.-]+\.[a-z]{2,}.*$", "", name, flags=re.I)
    name = re.sub(r"\s*[|–—-]\s*(?:complete|full|all\s+episodes?).*$", "", name, flags=re.I)
    if _episode_candidates(name): return ""
    name = re.sub(r"\b(?:web\s*series|complete|full|all\s+episodes?|reup)\b", " ", name, flags=re.I)
    return re.sub(r"\s+", " ", name).strip(" -|–—:")


def _browser_discover(url):
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return [], ""
    found, page_text = [], ""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=USER_AGENT)
            def on_response(resp):
                try:
                    u = resp.url; ctype = (resp.headers.get("content-type") or "").lower()
                    if is_media_url(u) or "mpegurl" in ctype or "dash" in ctype or "video/" in ctype: found.append(u)
                except Exception: pass
            page.on("response", on_response)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1800)
            page_text = page.locator("body").inner_text(timeout=5000) if page.locator("body") else ""
            found.extend(extract_urls_from_text(page.content(), page.url)); found.extend(extract_urls_from_text(page_text, page.url))
            browser.close()
    except Exception:
        return list(dict.fromkeys(found)), page_text
    return list(dict.fromkeys(found)), page_text


def _result_key(item):
    """Return a conservative identity key for one crawler result.

    Exact URLs are always deduplicated. Different URLs are grouped only when
    episode, resolution and source are all known, which removes repeated player
    links without collapsing unknown-quality alternatives.
    """
    url_key = canonical_url_key(item.get("url", ""))
    episode = clean_text(item.get("episode") or "Unknown Episode").lower()
    resolution = clean_text(item.get("resolution") or "Unknown").lower()
    source = clean_text(item.get("source") or "Unknown").lower()
    if episode != "unknown episode" and resolution != "unknown" and source not in {"", "unknown"}:
        return ("logical", episode, resolution, source)
    return ("url", url_key)


def _candidate_score(item):
    """Prefer directly downloadable media over wrapper/player pages."""
    url = item.get("url", "")
    return (
        int(is_media_url(url)),
        int(is_protected_url(url)),
        int(not is_player_like_url(url)),
        int(item.get("episode") != "Unknown Episode"),
        int(item.get("resolution") != "Unknown"),
        -len(url),
    )


def _merge_result(primary, secondary):
    """Merge useful metadata from a duplicate into the preferred result."""
    merged = dict(primary)
    if merged.get("episode") == "Unknown Episode" and secondary.get("episode") != "Unknown Episode":
        merged["episode"] = secondary["episode"]
    if merged.get("resolution") == "Unknown" and secondary.get("resolution") != "Unknown":
        merged["resolution"] = secondary["resolution"]
    if (not merged.get("title") or merged.get("title") == "Unknown Episode") and secondary.get("title"):
        merged["title"] = secondary["title"]
    if not merged.get("source_url") and secondary.get("source_url"):
        merged["source_url"] = secondary["source_url"]
    return merged


def _dedupe_results(results):
    """Deduplicate exact URLs, then conservative logical episode duplicates."""
    by_url = {}
    for item in results:
        url_key = canonical_url_key(item.get("url", ""))
        if not url_key:
            continue
        existing = by_url.get(url_key)
        if existing is None:
            by_url[url_key] = dict(item)
            continue
        preferred, other = (item, existing) if _candidate_score(item) > _candidate_score(existing) else (existing, item)
        by_url[url_key] = _merge_result(preferred, other)

    grouped = {}
    for item in by_url.values():
        key = _result_key(item)
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = item
            continue
        preferred, other = (item, existing) if _candidate_score(item) > _candidate_score(existing) else (existing, item)
        grouped[key] = _merge_result(preferred, other)
    return list(grouped.values())


def crawl_blog_episodes(raw_url):
    raw_url = raw_url.strip()
    if not raw_url or not is_http_url(raw_url): return []
    try: response = _request_page(raw_url)
    except requests.RequestException: return []
    challenge = response.status_code == 403 or _is_cloudflare_challenge(response.text)
    if challenge:
        browser_urls, browser_text = _browser_discover(raw_url)
        if not browser_urls: return []
        soup = BeautifulSoup("<html><body></body></html>", "lxml"); html_source = browser_text; final_base = raw_url
    else:
        soup = BeautifulSoup(response.text, "lxml"); html_source = response.text; final_base = response.url
    series = _series_name(soup)
    results, current_episode, candidates = [], "Unknown Episode", []

    def collect(value, text="", force=False, context_episode=None):
        for candidate in extract_urls_from_text(value, final_base): candidates.append((candidate, text, force, context_episode))

    content = soup.select_one(".entry-content") or soup
    for element in content.find_all(True):
        element_text = clean_text(element.get_text(" ", strip=True))
        detected = _episode_candidates(element_text)
        if detected: current_episode = detected
        for attr in ATTRIBUTES:
            value = element.get(attr)
            if value: collect(value, element_text, attr in {"data-m3u8", "data-mpd", "data-hls", "data-video-url", "data-video-src", "data-file", "data-source", "data-manifest"}, current_episode)
        if element.name in {"iframe", "embed", "object"}: collect(element.get("src") or element.get("data") or "", element_text, True, current_episode)

    collect(html_source, clean_text(soup.get_text(" ", strip=True)), False, current_episode)
    for script in soup.find_all("script"):
        script_text = script.string or script.get_text(" ", strip=False)
        for u in extract_urls_from_text(script_text, final_base) + extract_json_urls(script_text, final_base): candidates.append((u, clean_text(script_text)[:500], True, current_episode))

    usable_static = sum(1 for u, _, _, _ in candidates if is_media_url(u) or is_protected_url(u) or is_player_like_url(u))
    browser_urls, browser_text = [], ""
    if usable_static < 2 or not candidates:
        browser_urls, browser_text = _browser_discover(final_base)
        for u in browser_urls: candidates.append((u, browser_text[:1000], True, _episode_candidates(browser_text) or current_episode))

    seen = set()
    for href, text, force, context_episode in candidates:
        href = normalize_url(href, final_base)
        if not href or not (is_media_url(href) or is_protected_url(href) or is_player_like_url(href, text)): continue
        key = canonical_url_key(href)
        if not key or key in seen: continue
        seen.add(key)
        combined = f"{text} {href}"
        episode = _episode_candidates(combined) or context_episode or _episode_candidates(browser_text) or "Unknown Episode"
        resolution = _resolution(combined)
        title = sanitize_filename(" - ".join(x for x in (series, episode, resolution if resolution != "Unknown" else "") if x)).strip() or "Unknown Episode"
        results.append({"title": title, "episode": episode, "url": href, "source": _source(href, text), "resolution": resolution, "source_url": raw_url})

    results = _dedupe_results(results)

    def sort_key(item):
        m = re.search(r"(\d+)$", item.get("episode", "")); ep = int(m.group(1)) if m else 10**9
        res_order = {"2160p": 0, "1080p": 1, "1080p HEVC": 1, "720p": 2, "480p": 3, "Unknown": 9}
        return (ep, res_order.get(item.get("resolution", "Unknown"), 8), item.get("source", ""), canonical_url_key(item.get("url", "")))
    results.sort(key=sort_key)
    return results
