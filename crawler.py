import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from config import PROTECTED_DOMAINS, SETTINGS, USER_AGENT


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
        response.raise_for_status()
    except requests.RequestException:
        return []

    soup = BeautifulSoup(response.text, "lxml")
    results = []
    current_episode = None

    for element in soup.select(".entry-content h5"):
        text = element.get_text(" ", strip=True)

        match = re.search(
            r"Download\s+Episode\s+(\d+)",
            text,
            re.IGNORECASE,
        )

        if match:
            current_episode = f"Episode {match.group(1)}"
            continue

        if not current_episode:
            continue

        for anchor in element.find_all("a", href=True):
            href = anchor.get("href", "").strip()

            if "luluvid.com" in href.lower():
                results.append({
                    "title": current_episode,
                    "url": href,
                })
                break

    return results
