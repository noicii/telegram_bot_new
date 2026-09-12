import re
from urllib.parse import urljoin, urlparse, unquote

import requests
from bs4 import BeautifulSoup

from config import PROTECTED_DOMAINS, SETTINGS, USER_AGENT


from utils import sanitize_filename
def is_protected_url(url):
    if not url:
        return False

    url_lower = url.lower()

    return any(
        domain.lower() in url_lower
        for domain in PROTECTED_DOMAINS
    )


def is_http_url(url):
    try:
        parsed = urlparse(url)

        return parsed.scheme.lower() in {
            "http",
            "https",
        }

    except Exception:
        return False


def extract_protected_urls(text):
    if not text:
        return []

    found = []

    pattern = r'https?://[^\s<>"\'\]\)]+'

    for match in re.findall(pattern, text):
        url = match.rstrip(".,;")

        if is_protected_url(url):
            found.append(url)

    # Remove duplicates while preserving order.
    return list(dict.fromkeys(found))


def resolve_blog_links(raw_url):
    raw_url = raw_url.strip()

    if not raw_url:
        return []

    # Direct protected links and magnets do not need crawling.
    if is_protected_url(raw_url):
        return [raw_url]

    if raw_url.lower().startswith("magnet:"):
        return [raw_url]

    if not is_http_url(raw_url):
        return [raw_url]

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
    }

    try:
        response = requests.get(
            raw_url,
            headers=headers,
            timeout=30,
            allow_redirects=True,
        )

        # Do not treat an inaccessible blog page as a video URL.
        if response.status_code == 403:
            return []

        response.raise_for_status()

    except requests.RequestException:
        # Crawling failed, so do not send the original blog URL
        # to the downloader as if it were a media URL.
        return []

    # Detect Cloudflare challenge pages.
    # A challenge page is not the actual blog content, so never
    # return the blog URL as if it were a discovered video URL.
    challenge_markers = (
        "just a moment...",
        "__cf_chl_",
        "cf-chl-",
        "cloudflare",
    )

    response_text_lower = response.text.lower()

    if sum(
        1 for marker in challenge_markers
        if marker in response_text_lower
    ) >= 2:
        return []

    soup = BeautifulSoup(
        response.text,
        "lxml",
    )

    results = []
    # Search video/source tags.
    media_ext = (".mp4", ".m4v", ".webm", ".mov", ".mkv", ".m3u8", ".mpd")

    for tag in soup.find_all(["video", "source"]):
        for attr in ("src", "data-src", "data-url", "data-video", "data-file"):
            value = tag.get(attr)
            if value:
                absolute_url = urljoin(response.url, value.strip())
                clean_url = absolute_url.split("?", 1)[0].lower()
                if clean_url.endswith(media_ext):
                    results.append(absolute_url)

    # Search links from href attributes.
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "").strip()

        if not href:
            continue

        absolute_url = urljoin(
            response.url,
            href,
        )

        if is_protected_url(absolute_url):
            results.append(absolute_url)

    # Also search complete page text/source.
    results.extend(
        extract_protected_urls(response.text)
    )

    # Preserve order and remove duplicates.
    results = list(dict.fromkeys(results))

    return results


def parse_time_range(value):
    if not value:
        return None

    value = value.strip()

    patterns = [
        r"^(\d{2}):(\d{2}):(\d{2})-(\d{2}):(\d{2}):(\d{2})$",
        r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})$",
    ]

    for pattern in patterns:
        match = re.match(pattern, value)

        if not match:
            continue

        parts = [int(x) for x in match.groups()]

        if len(parts) == 6:
            start = (
                parts[0] * 3600
                + parts[1] * 60
                + parts[2]
            )

            end = (
                parts[3] * 3600
                + parts[4] * 60
                + parts[5]
            )

        else:
            start = (
                parts[0] * 60
                + parts[1]
            )

            end = (
                parts[2] * 60
                + parts[3]
            )

        if end <= start:
            return None

        return start, end

    return None


def parse_input_line(line):
    line = line.strip()

    if not line:
        return None

    parts = [
        part.strip()
        for part in line.split("|")
    ]

    url = parts[0]

    if not url:
        return None

    channel_id = None
    trim_range = None
    custom_name = None

    for part in parts[1:]:
        if not part:
            continue

        # Telegram channel IDs normally look like -100xxxxxxxxxx.
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
    results = []

    for line in lines:
        item = parse_input_line(line)

        if item:
            results.append(item)

    return results
def crawl_blog_episodes(raw_url):
    raw_url = raw_url.strip()

    if not raw_url or not is_http_url(raw_url):
        return []

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
    }
    try:
        response = requests.get(
            raw_url,
            headers=headers,
            timeout=30,
            allow_redirects=True,
        )

        if response.status_code == 403:
            return []
        response.raise_for_status()

    except requests.RequestException:
        return []

    response_text_lower = response.text.lower()

    challenge_markers = (
        "just a moment...",
        "__cf_chl_",
        "cf-chl-",
    )
    if sum(
        1 for marker in challenge_markers
        if marker in response_text_lower
    ) >= 2:
        return []

    soup = BeautifulSoup(response.text, "lxml")

    from html import unescape

    results = []
    current_episode = "Unknown Episode"
    def clean_text(value):
        value = unescape(unquote(value or ""))
        value = value.replace("+", " ")
        return re.sub(r"\s+", " ", value).strip()

    def detect_episode(value):
        value = clean_text(value)

        patterns = (
            r"\bS\d{1,2}E(\d{1,4})\b",
            r"\bEpisode[\s._-]*(\d{1,4})\b",
            r"\bEp[\s._-]*(\d{1,4})\b",
        )
        for pattern in patterns:
            match = re.search(pattern, value, re.IGNORECASE)
            if match:
                return f"Episode {int(match.group(1))}"

        return None

    def detect_resolution(value):
        value = clean_text(value)

        if re.search(r"\b1080p\b", value, re.IGNORECASE):
            if re.search(r"\b(?:HEVC|H265|H\.265|x265)\b", value, re.IGNORECASE):
                return "1080p HEVC"
            return "1080p"

        if re.search(r"\b720p\b", value, re.IGNORECASE):
            return "720p"

        if re.search(r"\b480p\b", value, re.IGNORECASE):
            return "480p"

        if re.search(r"\b2160p\b|\b4K\b", value, re.IGNORECASE):
            return "2160p"

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
    # Extract the series name from the blog/page itself.
    page_title = clean_text(
        soup.title.get_text(" ", strip=True)
        if soup.title
        else ""
    )

    page_h1 = ""
    h1 = content_h1 = soup.find("h1")
    if h1:
        page_h1 = clean_text(h1.get_text(" ", strip=True))

    series_name = page_h1 or page_title

    # Remove common website suffixes from the page title.
    if series_name:
        series_name = re.sub(
            r"\\s*[|\\-–—]\\s*(?:https?://)?(?:www\\.)?[a-z0-9.-]+\\.[a-z]{2,}.*$",
            "",
            series_name,
            flags=re.IGNORECASE,
        ).strip()

        # Remove page-level words that are not part of the actual series name.
        series_name = re.sub(
            r"\s*[|–—-]\s*(?:complete|full|all\s+episodes?)\s*$",
            "",
            series_name,
            flags=re.IGNORECASE,
        ).strip()

    # Do not accidentally use an episode title as the series name.
    if detect_episode(series_name):
        series_name = ""

    def make_title(episode, source, resolution, href, link_text):
        name = clean_text(series_name)

        # Remove generic page words from the actual series name.
        for marker in (
            "Web Series",
            "WEB SERIES",
            "web series",
            "Complete",
            "complete",
            "FULL",
            "Full",
            "full",
            "All Episodes",
            "ALL EPISODES",
            "all episodes",
            "[REUP]",
            "[Reup]",
            "[reup]",
            "REUP",
            "Reup",
            "reup",
        ):
            name = name.replace(marker, " ")

        name = re.sub(r"\\s+", " ", name).strip(" -|–—:")

        parts = []
        if name:
            parts.append(name)
        if episode:
            parts.append(episode)
        if resolution and resolution != "Unknown":
            parts.append(resolution)

        return sanitize_filename(" - ".join(parts)).strip()

    def add_result(episode, href, link_text):
        href = clean_text(href)

        if not href or not is_http_url(href):
            return

        href_lower = href.lower()

        media_like = bool(
            re.search(
                r"\.(?:mkv|mp4|m4v|webm|mov)(?:[?#].*)?$",
                href_lower,
                re.IGNORECASE,
            )
        )

        protected_like = any(
            domain in href_lower
            for domain in PROTECTED_DOMAINS
        )

        source = detect_source(href, link_text)
        resolution = detect_resolution(
            f"{href} {link_text}"
        )

        if not (media_like or protected_like or source != "Unknown"):
            return
        detected_episode = detect_episode(f"{href} {link_text}")
        final_episode = detected_episode or episode

        title = make_title(
            final_episode,
            source,
            resolution,
            href,
            link_text,
        )

        results.append({
            "title": title,
            "episode": final_episode,
            "url": href,
            "source": source,
            "resolution": resolution,
            "source_url": raw_url,
        })

    content = soup.select_one(".entry-content") or soup

    for element in content.find_all(
        ["h1", "h2", "h3", "h4", "h5", "h6", "a"]
    ):
        if element.name != "a":
            heading_text = clean_text(
                element.get_text(" ", strip=True)
            )
            detected_episode = detect_episode(heading_text)

            if detected_episode:
                current_episode = detected_episode

            continue

        href = element.get("href", "").strip()
        link_text = element.get_text(" ", strip=True)

        detected_episode = detect_episode(
            f"{link_text} {href}"
        )

        add_result(
            detected_episode or current_episode,
            href,
            link_text,
        )

    unique_results = []
    seen_urls = set()

    for item in results:
        if item["url"] in seen_urls:
            continue

        seen_urls.add(item["url"])
        unique_results.append(item)

    return unique_results
