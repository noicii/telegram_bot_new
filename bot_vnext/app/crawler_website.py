from __future__ import annotations

import re
import time
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from crawler import (
    CRAWL_TIMEOUT,
    PROVIDERS,
    _clean,
    _decode_embedded_text,
    _detect_episode,
    _detect_resolution,
    _episode_number,
    _extract_candidates,
    _fetch_html,
    _host,
    _lookup_page_resolution,
    _looks_like_video_candidate,
    _make_title,
    _normalize_url,
    _provider_name,
    _series_name,
    is_http_url,
    is_protected_url,
)

WEBSITE_CRAWL_MAX_DEPTH = 2
WEBSITE_CRAWL_MAX_PAGES = 24
WEBSITE_CRAWL_MAX_SECONDS = 45

_CRAWL_LINK_HINT_RE = re.compile(
    r"(?:episode|ep(?:isode)?|season|video|player|embed|watch|stream|archive|index|part|chapter)",
    re.I,
)
_CRAWL_PAGINATION_RE = re.compile(
    r"(?:\bnext\b|\bprev(?:ious)?\b|\bolder\b|\bpage(?:d)?\b|[?&](?:page|paged|p)=\d+|/(?:page|paged)/?\d+|(?:^|[-_/])\d+(?:[-_/]|$))",
    re.I,
)


def _crawl_link_is_relevant(url: str, text: str) -> bool:
    combined = f"{url} {text}"
    return bool(_CRAWL_LINK_HINT_RE.search(combined) or _CRAWL_PAGINATION_RE.search(combined))


def _discover_crawl_links(soup, base_url: str, source_hosts: set[str], visited=None) -> list[str]:
    visited = visited or set()
    found = []
    seen = set()
    base_url = _normalize_url(base_url)
    for tag in soup.find_all("a"):
        raw = tag.get("href")
        if not raw:
            continue
        url = _normalize_url(urljoin(base_url, _decode_embedded_text(raw)))
        if not is_http_url(url) or _host(url) not in source_hosts:
            continue
        if url in visited or url in seen or url == base_url:
            continue
        text = _clean(tag.get_text(" ", strip=True))
        if not _crawl_link_is_relevant(url, text):
            continue
        seen.add(url)
        found.append(url)
    return found


def _page_episode_context(soup, page_url: str, parent_context: str = "") -> str:
    values = []
    if parent_context:
        values.append(_clean(parent_context))
    if soup.title:
        values.append(_clean(soup.title.get_text(" ", strip=True)))
    for heading in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"], limit=5):
        text = _clean(heading.get_text(" ", strip=True))
        if text:
            values.append(text)
    values.append(page_url)
    return " ".join(dict.fromkeys(value for value in values if value))


def _website_result_sort_key(item):
    episode_no = _episode_number(item.get("episode", ""))
    resolution = item.get("resolution", "")
    res_rank = {"2160p": 4, "1440p": 3, "1080p": 2, "720p": 1}.get(resolution, 0)
    return (episode_no, -res_rank, item.get("source", ""), item.get("url", ""))


def crawl_website_episodes(
    raw_url,
    max_depth=WEBSITE_CRAWL_MAX_DEPTH,
    max_pages=WEBSITE_CRAWL_MAX_PAGES,
    max_seconds=WEBSITE_CRAWL_MAX_SECONDS,
):
    """Crawl bounded relevant same-site pages for `/crawl` only."""
    raw_url = raw_url.strip()
    if not raw_url or not is_http_url(raw_url) or is_protected_url(raw_url):
        return []
    try:
        max_depth = max(0, int(max_depth))
        max_pages = max(0, int(max_pages))
        max_seconds = max(0.0, float(max_seconds))
    except (TypeError, ValueError):
        return []
    if not max_pages or not max_seconds:
        return []

    root_url = _normalize_url(raw_url)
    source_host = _host(root_url)
    if not source_host:
        return []

    started = time.monotonic()
    queue = [(root_url, 0, "")]
    visited = set()
    seen_media = set()
    results = []
    root_series = ""

    while queue and len(visited) < max_pages and time.monotonic() - started < max_seconds:
        page_url, depth, parent_context = queue.pop(0)
        page_url = _normalize_url(page_url)
        if page_url in visited or _host(page_url) != source_host:
            continue
        visited.add(page_url)

        html = _fetch_html(page_url, CRAWL_TIMEOUT)
        if not html:
            continue
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            continue

        page_context = _page_episode_context(soup, page_url, parent_context)
        page_series = _series_name(soup)
        if depth == 0 and page_series:
            root_series = page_series
        series = root_series or page_series
        if not series and parent_context:
            series = re.sub(
                r"\s*[-|–—:]?\s*(?:Episode|Ep|E|Part)\s*[-.#:]?\s*\d+.*$",
                "",
                _clean(parent_context),
                flags=re.I,
            ).strip(" -|–—:")

        for url, text in _extract_candidates(soup, page_url, html):
            combined = " ".join(value for value in (text, page_context, url) if value)
            if not _looks_like_video_candidate(url, combined, source_host):
                continue
            url = _normalize_url(url)
            if not is_http_url(url) or url in seen_media:
                continue
            episode = (
                _detect_episode(text)
                or _detect_episode(page_context)
                or _detect_episode(url)
                or "Unknown Episode"
            )
            provider = _provider_name(url, text)
            resolution = _detect_resolution(f"{text} {url}")
            if resolution == "Unknown" and provider in PROVIDERS:
                resolution = _lookup_page_resolution(url)
            seen_media.add(url)
            results.append({
                "title": _make_title(series, episode, resolution),
                "episode": episode,
                "url": url,
                "source": provider,
                "resolution": resolution,
                "source_url": page_url,
            })

        if depth >= max_depth or time.monotonic() - started >= max_seconds:
            continue

        for child_url in _discover_crawl_links(soup, page_url, {source_host}, visited):
            if child_url not in visited and all(child_url != queued[0] for queued in queue):
                queue.append((child_url, depth + 1, page_context))

    return sorted(results, key=_website_result_sort_key)
