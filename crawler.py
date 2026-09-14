import html
import re
from urllib.parse import unquote, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from config import PROTECTED_DOMAINS, SETTINGS, USER_AGENT
from utils import sanitize_filename


MEDIA_EXTENSIONS = (
    ".mp4",
    ".m4v",
    ".webm",
    ".mov",
    ".mkv",
    ".m3u8",
    ".mpd",
)

MEDIA_MARKERS = (
    ".m3u8",
    ".mpd",
    ".mp4",
    ".m4v",
    ".webm",
    ".mov",
    ".mkv",
    "manifest",
    "playlist",
    "master.m3u8",
    "hls",
    "dash",
    "videoplayback",
)

URL_RE = re.compile(
    r"https?://[^\s<>\"'`\\]+",
    re.IGNORECASE,
)

ATTRIBUTES = (
    "href",
    "src",
    "data-src",
    "data-url",
    "data-file",
    "data-video",
    "data-video-url",
    "data-video-src",
    "data-stream",
    "data-stream-url",
    "data-source",
    "data-hls",
    "data-m3u8",
    "data-mpd",
    "data-link",
    "data-download",
    "content",
)


def clean_text(value):
    value = html.unescape(unquote(value or ""))
    value = value.replace("\\/", "/").replace("\\u0026", "&")
    value = value.replace("+", " ")
    return re.sub(r"\s+", " ", value).strip()


def normalize_url(url, base_url=None):
    if not url:
        return ""

    value = clean_text(str(url)).strip(" \t\r\n<>\"'`.,;)")
    if not value:
        return ""

    if base_url:
        value = urljoin(base_url, value)

    if value.startswith("//"):
        parsed_base = urlparse(base_url or "https://example.com")
        value = f"{parsed_base.scheme}:{value}"

    try:
        parsed = urlparse(value)
    except Exception:
        return ""

    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""

    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return ""

    try:
        port = parsed.port
    except ValueError:
        return ""

    netloc = hostname
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{hostname}:{port}"

    path = parsed.path or "/"
    # Keep query parameters intact because signed media URLs may depend on order.
    return urlunparse((scheme, netloc, path, parsed.params, parsed.query, ""))


def canonical_url_key(url):
    normalized = normalize_url(url)
    if not normalized:
        return ""
    parsed = urlparse(normalized)
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path,
            parsed.params,
            parsed.query,
            "",
        )
    )


def is_http_url(url):
    return bool(normalize_url(url))


def is_protected_url(url):
    if not url:
        return False
    value = urlparse(url).netloc.lower()
    full = url.lower()
    return any(
        domain.lower().lstrip(".") in value or domain.lower() in full
        for domain in PROTECTED_DOMAINS
    )


def is_media_url(url):
    if not url:
        return False
    lower = url.lower()
    path = urlparse(url).path.lower()
    return path.endswith(MEDIA_EXTENSIONS) or any(marker in lower for marker in MEDIA_MARKERS)


def is_player_like_url(url, text=""):
    combined = f"{url} {text}".lower()
    markers = (
        "/embed/",
        "/player/",
        "embed.",
        "player.",
        "video/",
        "watch/",
        "watch?",
        "play/",
        "stream/",
        "iframe",
        "vidsrc",
        "filemoon",
        "streamtape",
        "streamwish",
        "dood",
        "vidhide",
        "lulu",
    )
    return any(marker in combined for marker in markers)


def extract_urls_from_text(text, base_url=None):
    if not text:
        return []

    decoded = clean_text(text)
    found = []
    for match in URL_RE.findall(decoded):
        candidate = normalize_url(match, base_url)
        if candidate:
            found.append(candidate)

    # Some pages store URLs inside JSON with escaped slashes and repeated encoding.
    for _ in range(2):
        decoded = html.unescape(unquote(decoded)).replace("\\/", "/")
        for match in URL_RE.findall(decoded):
            candidate = normalize_url(match, base_url)
            if candidate:
                found.append(candidate)

    return list(dict.fromkeys(found))


def extract_protected_urls(text):
    return [url for url in extract_urls_from_text(text) if is_protected_url(url)]


def _request_page(url, timeout=30):
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.8",
    }
    response = requests.get(
        url,
        headers=headers,
        timeout=timeout,
        allow_redirects=True,
    )
    response.raise_for_status()
    return response


def _is_cloudflare_challenge(text):
    lower = (text or "").lower()
    markers = (
        "just a moment...",
        "__cf_chl_",
        "cf-chl-",
        "cf_chl_opt",
    )
    return sum(marker in lower for marker in markers) >= 2


def resolve_blog_links(raw_url):
    raw_url = raw_url.strip()
    if not raw_url:
        return []
    if is_protected_url(raw_url):
        return [raw_url]
    if raw_url.lower().startswith("magnet:"):
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
            if not value:
                continue
            candidates = extract_urls_from_text(value, response.url)
            found.extend(
                candidate
                for candidate in candidates
                if is_protected_url(candidate) or is_media_url(candidate)
            )

    found.extend(extract_protected_urls(response.text))
    return list(dict.fromkeys(found))


def parse_time_range(value):
    if not value:
        return None
    value = value.strip()
    patterns = (
        r"^(\d{2}):(\d{2}):(\d{2})-(\d{2}):(\d{2}):(\d{2})$",
        r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})$",
    )
    for pattern in patterns:
        match = re.match(pattern, value)
        if not match:
            continue
        parts = [int(x) for x in match.groups()]
        if len(parts) == 6:
            start = parts[0] * 3600 + parts[1] * 60 + parts[2]
            end = parts[3] * 3600 + parts[4] * 60 + parts[5]
        else:
            start = parts[0] * 60 + parts[1]
            end = parts[2] * 60 + parts[3]
        if end <= start:
            return None
        return start, end
    return None


def parse_input_line(line):
    line = line.strip()
    if not line:
        return None
    parts = [part.strip() for part in line.split("|")]
    url = parts[0]
    if not url:
        return None

    channel_id = None
    trim_range = None
    custom_name = None

    for part in parts[1:]:
        if not part:
            continue
        if re.fullmatch(r"-?100\d+", part):
            channel_id = int(part)
            continue
        parsed_trim = parse_time_range(part)
        if parsed_trim:
            trim_range = parsed_trim
            continue
        if SETTINGS.get("custom_rename", True):
            custom_name = part

    return {
        "url": url,
        "channel_id": channel_id,
        "trim_range": trim_range,
        "custom_name": custom_name,
    }


def parse_input_lines(lines):
    return [item for line in lines if (item := parse_input_line(line))]


def crawl_blog_episodes(raw_url):
    raw_url = raw_url.strip()
    if not raw_url or not is_http_url(raw_url):
        return []

    try:
        response = _request_page(raw_url)
    except requests.RequestException:
        return []

    if response.status_code == 403 or _is_cloudflare_challenge(response.text):
        return []

    soup = BeautifulSoup(response.text, "lxml")
    results = []

    def detect_episode(value):
        value = clean_text(value)
        patterns = (
            r"\bS\d{1,2}\s*[-_. ]?\s*E(\d{1,4})\b",
            r"\bEpisode[\s._-]*(\d{1,4})\b",
            r"\bEp[\s._-]*(\d{1,4})\b",
            r"\bE(?:P)?\s*[-_.:]?\s*(\d{1,4})\b",
        )
        for pattern in patterns:
            match = re.search(pattern, value, re.IGNORECASE)
            if match:
                return f"Episode {int(match.group(1))}"
        return None

    def detect_resolution(value):
        value = clean_text(value)
        if re.search(r"\b2160p\b|\b4k\b", value, re.IGNORECASE):
            return "2160p"
        if re.search(r"\b1080p\b", value, re.IGNORECASE):
            if re.search(r"\b(?:HEVC|H265|H\.265|x265)\b", value, re.IGNORECASE):
                return "1080p HEVC"
            return "1080p"
        if re.search(r"\b720p\b", value, re.IGNORECASE):
            return "720p"
        if re.search(r"\b480p\b", value, re.IGNORECASE):
            return "480p"
        match = re.search(r"\b(\d{3,4})\s*[x×]\s*(\d{3,4})\b", value, re.IGNORECASE)
        if match:
            width, height = int(match.group(1)), int(match.group(2))
            largest = max(width, height)
            if largest >= 2160:
                return "2160p"
            if largest >= 1080:
                return "1080p"
            if largest >= 720:
                return "720p"
            if largest >= 480:
                return "480p"
        return "Unknown"

    def detect_source(href, link_text):
        combined = f"{href} {link_text}".lower()
        source_patterns = (
            ("FRDL", ("frdl.my", "frdl.io")),
            ("LuluStream", ("luluvid.com", "lulustream")),
            ("DoodStream", ("doodstream", "myvidplay.com")),
            ("StreamWish", ("streamwish",)),
            ("Vidhide", ("vidhide",)),
        )
        for source_name, markers in source_patterns:
            if any(marker in combined for marker in markers):
                return source_name
        parsed = urlparse(href)
        return parsed.netloc or "Unknown"

    page_title = clean_text(soup.title.get_text(" ", strip=True) if soup.title else "")
    page_h1 = clean_text(soup.find("h1").get_text(" ", strip=True)) if soup.find("h1") else ""
    series_name = page_h1 or page_title
    series_name = re.sub(
        r"\s*[|\-–—]\s*(?:https?://)?(?:www\.)?[a-z0-9.-]+\.[a-z]{2,}.*$",
        "",
        series_name,
        flags=re.IGNORECASE,
    ).strip()
    series_name = re.sub(
        r"\s*[|–—-]\s*(?:complete|full|all\s+episodes?)\s*$",
        "",
        series_name,
        flags=re.IGNORECASE,
    ).strip()
    if detect_episode(series_name):
        series_name = ""

    def make_title(episode, source, resolution):
        name = clean_text(series_name)
        name = re.sub(
            r"\b(?:web\s*series|complete|full|all\s+episodes?|reup)\b",
            " ",
            name,
            flags=re.IGNORECASE,
        )
        name = re.sub(r"\s+", " ", name).strip(" -|–—:")
        parts = [part for part in (name, episode) if part]
        if resolution != "Unknown":
            parts.append(resolution)
        return sanitize_filename(" - ".join(parts)).strip()

    def add_result(episode, href, link_text, force=False):
        href = normalize_url(href, response.url)
        if not href:
            return

        link_text = clean_text(link_text)
        detected_episode = detect_episode(f"{link_text} {href}")
        final_episode = detected_episode or episode or "Unknown Episode"
        source = detect_source(href, link_text)
        resolution = detect_resolution(f"{href} {link_text}")

        candidate = (
            force
            or is_protected_url(href)
            or is_media_url(href)
            or is_player_like_url(href, link_text)
        )
        if not candidate:
            return

        results.append(
            {
                "title": make_title(final_episode, source, resolution),
                "episode": final_episode,
                "url": href,
                "source": source,
                "resolution": resolution,
                "source_url": raw_url,
            }
        )

    content = soup.select_one(".entry-content") or soup
    current_episode = "Unknown Episode"

    # Pass 1: visible links, media tags, iframes and data attributes.
    for element in content.find_all(True):
        element_text = clean_text(element.get_text(" ", strip=True))
        detected = detect_episode(element_text)
        if detected:
            current_episode = detected

        for attr in ATTRIBUTES:
            value = element.get(attr)
            if not value:
                continue
            for candidate in extract_urls_from_text(value, response.url):
                force = attr in {
                    "data-m3u8",
                    "data-mpd",
                    "data-hls",
                    "data-video-url",
                    "data-video-src",
                    "data-file",
                    "data-source",
                }
                add_result(current_episode, candidate, element_text, force=force)

        if element.name in {"iframe", "embed", "object"}:
            for candidate in extract_urls_from_text(element.get("src") or element.get("data"), response.url):
                add_result(current_episode, candidate, element_text, force=True)

    # Pass 2: URLs embedded in inline JavaScript / JSON / HTML source.
    script_text = "\n".join(
        script.get_text(" ", strip=False)
        for script in soup.find_all(["script", "noscript"])
    )
    for candidate in extract_urls_from_text(script_text, response.url):
        context = script_text[max(0, script_text.find(candidate) - 300): script_text.find(candidate) + 300]
        add_result(detect_episode(context) or current_episode, candidate, context, force=True)

    # Pass 3: scan raw HTML. This catches URLs hidden in attributes or escaped JSON
    # that BeautifulSoup did not expose as a normal DOM attribute.
    for candidate in extract_urls_from_text(response.text, response.url):
        if is_media_url(candidate) or is_protected_url(candidate) or is_player_like_url(candidate):
            add_result(current_episode, candidate, "raw-page", force=True)

    # Conservative de-duplication: only identical canonical URLs collapse.
    unique_results = []
    seen = set()
    for item in results:
        key = canonical_url_key(item["url"])
        if not key or key in seen:
            continue
        seen.add(key)
        unique_results.append(item)

    # Stable ordering makes the Telegram selection UI predictable.
    def episode_number(item):
        match = re.search(r"(\d+)$", item.get("episode", ""))
        return int(match.group(1)) if match else 10**9

    unique_results.sort(
        key=lambda item: (
            episode_number(item),
            item.get("resolution", "Unknown") == "Unknown",
            item.get("source", "Unknown").lower(),
            item.get("url", "").lower(),
        )
    )
    return unique_results
