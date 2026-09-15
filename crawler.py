import json
import re
from functools import lru_cache
from html import unescape
from urllib.parse import parse_qsl, urlencode, unquote, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from config import PROTECTED_DOMAINS, SETTINGS, USER_AGENT
from utils import sanitize_filename

CRAWL_TIMEOUT = 25
RESOLUTION_TIMEOUT = 8
MAX_HTML_BYTES = 8 * 1024 * 1024
MEDIA_RE = re.compile(r"\.(?:mp4|m4v|webm|mov|mkv|m3u8|mpd)(?:[?#].*)?$", re.I)
VIDEO_HINT_RE = re.compile(r"(?:/embed(?:/|$)|/player(?:/|$)|/video(?:/|$)|/stream(?:/|$)|/watch(?:/|$)|/download(?:/|$)|[?&](?:embed|video|stream|file|src|url)=)", re.I)
RESOLUTION_RE = re.compile(r"(?<!\d)(2160p|1440p|1080p|720p|576p|540p|480p|360p|240p)(?!\d)", re.I)
DIMENSION_RE = re.compile(r"(?<!\d)(\d{3,4})\s*[x×]\s*(\d{3,4})(?!\d)", re.I)
EPISODE_PATTERNS = (
    re.compile(r"\bS\d{1,2}\s*[-._ ]?\s*E\s*(\d{1,4})\b", re.I),
    re.compile(r"\b(?:Season\s*\d{1,2}\s*[-._ ]*)?Episode\s*[-._#:]?\s*(\d{1,4})\b", re.I),
    re.compile(r"\bEp\s*[-._#:]?\s*(\d{1,4})\b", re.I),
    re.compile(r"\bEP\s*[-._#:]?\s*(\d{1,4})\b", re.I),
    re.compile(r"\bE\s*[-._#:]?\s*(\d{1,4})\b", re.I),
    re.compile(r"\bPart\s*[-._#:]?\s*(\d{1,4})\b", re.I),
)
PROVIDERS = {
    "FRDL": ("frdl.my", "frdl.io"),
    "LuluStream": ("luluvid.com", "lulustream"),
    "DoodStream": ("doodstream", "myvidplay.com"),
    "StreamWish": ("streamwish",),
    "Vidhide": ("vidhide",),
    "StreamTape": ("streamtape",),
    "FileLions": ("filelions",),
    "MixDrop": ("mixdrop",),
    "Voe": ("voe.sx", "voe-network"),
}


def _clean(value):
    value = unescape(unquote(str(value or ""))).strip()
    return re.sub(r"\s+", " ", value)


def _host(url):
    try:
        return urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
    except Exception:
        return ""


def _normalize_url(url):
    url = _clean(url).strip("\"'<>[](){}.,; ")
    if not url:
        return ""
    try:
        p = urlparse(url)
        if p.scheme.lower() not in ("http", "https"):
            return url
        query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                 if not k.lower().startswith(("utm_", "fbclid", "gclid", "ref_"))]
        return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path, "", urlencode(query), p.fragment))
    except Exception:
        return url


def is_protected_url(url):
    if not url:
        return False
    low = url.lower()
    return any(str(domain).lower() in low for domain in PROTECTED_DOMAINS)


def is_http_url(url):
    try:
        return urlparse(url).scheme.lower() in {"http", "https"}
    except Exception:
        return False


def _decode_embedded_text(text):
    if not text:
        return ""
    value = unescape(str(text))
    for _ in range(2):
        value = value.replace("\\/", "/")
        value = value.replace("\\u002f", "/").replace("\\u002F", "/")
        value = value.replace("\\u0026", "&").replace("\\u003d", "=").replace("\\u003D", "=")
        value = value.replace("&amp;", "&")
        decoded = unquote(value)
        if decoded == value:
            break
        value = decoded
    return value


def extract_protected_urls(text):
    if not text:
        return []
    text = _decode_embedded_text(text)
    pattern = r"https?://[^\s<>\"'\]\)]+"
    found = []
    for match in re.findall(pattern, text, re.I):
        url = _normalize_url(match)
        if is_protected_url(url):
            found.append(url)
    return list(dict.fromkeys(found))


def _extract_urls_from_text(text, base_url=""):
    if not text:
        return []
    text = _decode_embedded_text(text)
    patterns = (
        r"(?:https?://|//)[^\s<>\"'\]\)]+",
        r"(?:(?:href|src|url|file|source|embed|video|stream|download)\s*[:=]\s*[\"'])([^\"']+)",
    )
    found = []
    for pattern in patterns:
        for match in re.findall(pattern, text, re.I):
            raw = match if isinstance(match, str) else match[0]
            if not raw:
                continue
            url = _normalize_url(urljoin(base_url, raw))
            if is_http_url(url):
                found.append(url)
    return list(dict.fromkeys(found))


def _candidate_urls_from_tag(tag, base_url):
    values = []
    attrs = (
        "href", "src", "data-src", "data-url", "data-link", "data-video", "data-file",
        "data-href", "data-embed", "data-iframe", "data-stream", "data-play", "data-download",
        "data-source", "data-player", "data-config", "data-options", "content",
    )
    for attr in attrs:
        value = tag.get(attr)
        if value:
            values.extend(_extract_urls_from_text(value, base_url))
            raw = _decode_embedded_text(value).strip()
            if is_http_url(raw):
                values.append(_normalize_url(raw))
    for value in tag.attrs.values():
        if isinstance(value, str) and ("http" in value.lower() or value.startswith("//") or "\\/" in value):
            values.extend(_extract_urls_from_text(value, base_url))
    return list(dict.fromkeys(x for x in values if is_http_url(x)))


def _provider_name(url, text=""):
    combined = f"{url} {text}".lower()
    for name, markers in PROVIDERS.items():
        if any(marker in combined for marker in markers):
            return name
    return _host(url) or "Unknown"


def _is_media(url):
    return bool(MEDIA_RE.search(url or ""))


def _looks_like_video_candidate(url, text="", source_host=""):
    if not is_http_url(url):
        return False
    if _is_media(url) or is_protected_url(url):
        return True
    provider = _provider_name(url, text)
    if provider in PROVIDERS:
        return True
    if VIDEO_HINT_RE.search(url):
        return True
    combined = f"{url} {text}".lower()
    if re.search(r"\b(?:video|player|stream|embed|watch|download|play|m3u8|mpd)\b", combined, re.I):
        return True
    host = _host(url)
    return bool(host and source_host and host != source_host and re.search(r"(?:vid|stream|video|player|media|cdn|file)", host, re.I))


def _challenge_page(text):
    low = (text or "").lower()
    markers = ("just a moment...", "__cf_chl_", "cf-chl-", "cf-ray")
    return sum(marker in low for marker in markers) >= 2


def _fetch_html(url, timeout=CRAWL_TIMEOUT):
    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.8",
            },
            timeout=timeout,
            allow_redirects=True,
        )
        if response.status_code in (403, 429):
            return None
        response.raise_for_status()
        if len(response.content) > MAX_HTML_BYTES:
            return response.text[:MAX_HTML_BYTES]
        if _challenge_page(response.text):
            return None
        return response.text
    except requests.RequestException:
        return None


def _extract_candidates(soup, base_url, raw_html):
    candidates = []
    tags = soup.find_all([
        "a", "iframe", "embed", "video", "source", "track", "object", "form",
        "script", "meta", "link", "button",
    ])
    for tag in tags:
        text = _clean(tag.get_text(" ", strip=True))
        for url in _candidate_urls_from_tag(tag, base_url):
            candidates.append((url, text))

    script_text = "\n".join(tag.string or tag.get_text(" ", strip=False) for tag in soup.find_all("script"))
    for text in (raw_html, script_text):
        for url in _extract_urls_from_text(text, base_url):
            candidates.append((url, ""))

    for tag in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        payload = tag.string or tag.get_text(" ", strip=False)
        try:
            data = json.loads(payload)
            stack = [data]
            while stack:
                item = stack.pop()
                if isinstance(item, dict):
                    for value in item.values():
                        if isinstance(value, (dict, list)):
                            stack.append(value)
                        elif isinstance(value, str):
                            for url in _extract_urls_from_text(value, base_url):
                                candidates.append((url, value))
                elif isinstance(item, list):
                    stack.extend(item)
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

    for url in extract_protected_urls(raw_html):
        candidates.append((url, ""))

    seen = set()
    output = []
    for url, text in candidates:
        key = _normalize_url(url)
        if not key or not is_http_url(key) or key in seen:
            continue
        seen.add(key)
        output.append((key, _clean(text)))
    return output


def resolve_blog_links(raw_url):
    raw_url = raw_url.strip()
    if not raw_url:
        return []
    if is_protected_url(raw_url) or raw_url.lower().startswith("magnet:"):
        return [raw_url]
    if not is_http_url(raw_url):
        return [raw_url]
    html = _fetch_html(raw_url)
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    source_host = _host(raw_url)
    return list(dict.fromkeys(
        url for url, text in _extract_candidates(soup, raw_url, html)
        if _looks_like_video_candidate(url, text, source_host)
    ))


def parse_time_range(value):
    if not value:
        return None
    value = value.strip()
    for pattern in (
        r"^(\d{2}):(\d{2}):(\d{2})-(\d{2}):(\d{2}):(\d{2})$",
        r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})$",
    ):
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
        return (start, end) if end > start else None
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
    return {"url": url, "channel_id": channel_id, "trim_range": trim_range, "custom_name": custom_name}


def parse_input_lines(lines):
    return [item for line in lines if (item := parse_input_line(line))]


def _detect_episode(value):
    value = _clean(value)
    for pattern in EPISODE_PATTERNS:
        match = pattern.search(value)
        if match:
            return f"Episode {int(match.group(1))}"
    return None


def _episode_number(value):
    episode = _detect_episode(value)
    if not episode:
        return 10**9
    return int(episode.rsplit(" ", 1)[-1])


def _detect_resolution(value):
    value = _clean(value)
    match = RESOLUTION_RE.search(value)
    if match:
        return match.group(1).lower()
    if re.search(r"\b(?:4k|uhd)\b", value, re.I):
        return "2160p"
    match = DIMENSION_RE.search(value)
    if match:
        width, height = int(match.group(1)), int(match.group(2))
        long_side = max(width, height)
        if long_side >= 3840: return "2160p"
        if long_side >= 1920: return "1080p"
        if long_side >= 1280: return "720p"
        if long_side >= 854: return "480p"
        if long_side >= 640: return "360p"
    return "Unknown"


@lru_cache(maxsize=256)
def _lookup_page_resolution(url):
    html = _fetch_html(url, RESOLUTION_TIMEOUT)
    if not html:
        return "Unknown"
    soup = BeautifulSoup(html, "lxml")
    values = []
    if soup.title:
        values.append(soup.title.get_text(" ", strip=True))
    values.append(soup.get_text(" ", strip=True))
    values.append(html)
    for tag in soup.find_all(["video", "source", "meta"]):
        values.extend(str(tag.get(attr, "")) for attr in ("src", "data-src", "data-file", "data-video", "content"))
    for value in values:
        resolution = _detect_resolution(value)
        if resolution != "Unknown":
            return resolution
    return "Unknown"


def _series_name(soup):
    title = _clean(
        soup.find("h1").get_text(" ", strip=True)
        if soup.find("h1")
        else (soup.title.get_text(" ", strip=True) if soup.title else "")
    )
    title = re.sub(r"\s*[|–—-]\s*(?:https?://)?(?:www\.)?[a-z0-9.-]+\.[a-z]{2,}.*$", "", title, flags=re.I)
    title = re.sub(r"\s*[|–—-]\s*(?:complete|full|all\s+episodes?)\s*$", "", title, flags=re.I)
    if _detect_episode(title):
        return ""
    return re.sub(r"\s+", " ", title).strip(" -|–—:")


def _make_title(series, episode, resolution):
    name = re.sub(r"\b(?:web\s*series|complete|full|all\s+episodes?|reup)\b", " ", series, flags=re.I)
    name = re.sub(r"\s+", " ", name).strip(" -|–—:")
    parts = [x for x in (name, episode, resolution if resolution != "Unknown" else "") if x]
    return sanitize_filename(" - ".join(parts)).strip()


def _nearest_episode_context(element):
    parts = []
    parent = element.parent
    if parent:
        parts.append(_clean(parent.get_text(" ", strip=True)))
    for previous in element.find_all_previous(["h1", "h2", "h3", "h4", "h5", "h6"], limit=3):
        text = _clean(previous.get_text(" ", strip=True))
        if text:
            parts.append(text)
            break
    return " ".join(parts)


def crawl_blog_episodes(raw_url):
    raw_url = raw_url.strip()
    if not raw_url or not is_http_url(raw_url):
        return []
    html = _fetch_html(raw_url)
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    content = soup.select_one(".entry-content") or soup
    series = _series_name(soup)
    current_episode = "Unknown Episode"
    candidates = []

    for element in content.find_all([
        "h1", "h2", "h3", "h4", "h5", "h6", "a", "iframe", "embed", "video",
        "source", "object", "form", "script", "meta", "button",
    ]):
        text = _clean(element.get_text(" ", strip=True))
        local_context = " ".join((text, _nearest_episode_context(element), str(element)))
        detected = _detect_episode(local_context)
        if element.name.startswith("h") and detected:
            current_episode = detected
        for url in _candidate_urls_from_tag(element, raw_url):
            candidates.append((url, local_context, detected or current_episode))

    for url, text in _extract_candidates(content, raw_url, str(content)):
        candidates.append((url, text, _detect_episode(text) or current_episode))

    results = []
    seen = set()
    source_host = _host(raw_url)
    for url, link_text, context_episode in candidates:
        url = _normalize_url(url)
        if not is_http_url(url) or not _looks_like_video_candidate(url, link_text, source_host):
            continue
        episode = _detect_episode(link_text) or context_episode or _detect_episode(url) or "Unknown Episode"
        provider = _provider_name(url, link_text)
        known_provider = provider in PROVIDERS
        resolution = _detect_resolution(f"{link_text} {url}")
        if resolution == "Unknown" and known_provider:
            resolution = _lookup_page_resolution(url)
        key = _normalize_url(url)
        if key in seen:
            continue
        seen.add(key)
        results.append({
            "title": _make_title(series, episode, resolution),
            "episode": episode,
            "url": url,
            "source": provider,
            "resolution": resolution,
            "source_url": raw_url,
        })

    def sort_key(item):
        episode_no = _episode_number(item.get("episode", ""))
        resolution = item.get("resolution", "")
        res_rank = {"2160p": 4, "1440p": 3, "1080p": 2, "720p": 1}.get(resolution, 0)
        return (episode_no, -res_rank, item.get("source", ""), item.get("url", ""))

    return sorted(results, key=sort_key)
